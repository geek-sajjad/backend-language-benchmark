#!/usr/bin/env python3
"""Benchmark runner (docs/06-runner-and-results.md). Runs on the loadgen host; standard library only.

  bench.py check                                   hosts, versions, sysctls, data files
  bench.py conformance [--stacks …]                conformance/check.py against each stack
  bench.py calibrate                               DB, k6 and downstream ceilings → results/calibration.json
  bench.py run [--stacks …] [--tests …] [--dry-run] full campaign → results/<campaign-id>/
  bench.py resume <campaign-id>                    continue; completed pairs are skipped
  bench.py common-load <campaign-id>               rerun only the test 9 common-load phase
  bench.py csv <campaign-id>                       rebuild results.csv and scores.txt

Hosts come from runner/hosts.toml (written by infra/host-setup/deploy.sh). Without it the
runner works in local mode: every role is this machine.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from benchlib.calibrate import calibrate  # noqa: E402
from benchlib.campaign import Campaign, Context, estimate, stack_selection  # noqa: E402
from benchlib.cluster import Cluster, Database, Stack  # noqa: E402
from benchlib.config import ALL_TESTS, Config  # noqa: E402
from benchlib.k6 import k6_version  # noqa: E402
from benchlib.util import REPO, log  # noqa: E402

SYSCTLS = {"net.core.somaxconn": "65535", "net.ipv4.tcp_tw_reuse": "1",
           "net.ipv4.ip_local_port_range": "1024\t65535"}


def cmd_check(ctx, args):
    c, ok = ctx.cluster, True

    def report(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"  {'ok  ' if good else 'FAIL'} {name} {detail}")

    print(f"mode: {c.mode}")
    for name, h in c.hosts.items():
        try:
            docker = h.sh("docker version --format '{{.Server.Version}}'").strip()
            report(f"{name}: reachable, docker {docker}", True)
        except Exception as e:  # noqa: BLE001
            report(f"{name}: reachable", False, str(e))
            continue
        if not c.local:
            for key, want in SYSCTLS.items():
                got = h.sh(f"sysctl -n {key}", check=False).strip()
                report(f"{name}: {key}", got == want, f"= {got!r}")
            report(f"{name}: swap off", h.sh("swapon --noheadings", check=False).strip() == "")
            report(f"{name}: ethtool {c.iface(name)}",
                   "allowance_exceeded" in h.sh(f"ethtool -S {c.iface(name)} 2>&1", check=False))
    report("k6", "v" in k6_version(), k6_version())
    db = Database(c)
    try:
        report("postgres", True, db.version())
        report("bench_template exists", db.psql("SELECT count(*) FROM pg_database WHERE datname = 'bench_template'", "postgres") == "1")
        report("bench has 100,000 products", db.psql("SELECT count(*) FROM products") == "100000")
    except Exception as e:  # noqa: BLE001
        report("postgres", False, str(e))
    try:
        with urllib.request.urlopen(f"http://{c.db.ip}:9000/health", timeout=3) as r:
            report("downstream mock", r.status == 200)
    except OSError as e:
        report("downstream mock", False, str(e))
    for path in ("k6/data/jwt/public.pem", "k6/data/jwt/private.pem", "k6/data/jwt/tokens.json",
                 "k6/data/transform/0200.json"):
        report(path, (REPO / path).exists())
    if ctx.prom.available():
        with urllib.request.urlopen(c.prometheus_url + "/api/v1/targets", timeout=5) as r:
            targets = json.loads(r.read())["data"]["activeTargets"]
        down = [f"{t['labels'].get('job')}/{t['labels'].get('host')}" for t in targets if t["health"] != "up"]
        report(f"prometheus: {len(targets)} targets", not down, f"down: {down}" if down else "")
    else:
        report("prometheus", c.local, f"not reachable at {c.prometheus_url}" + (" (optional locally)" if c.local else ""))
    print("all checks passed" if ok else "some checks failed")
    return 0 if ok else 1


def cmd_conformance(ctx, args):
    c, failed = ctx.cluster, []
    db = Database(c)
    for name in stack_selection(ctx.cfg, c, args.stacks):
        log(f"conformance: {name}")
        db.reset()
        stack = Stack(c, name)
        stack.restart()
        env = {**os.environ, "PGHOST": c.db.ip}
        p = subprocess.run([sys.executable, str(REPO / "conformance" / "check.py"), c.base_url], env=env)
        stack.down()
        if p.returncode != 0:
            failed.append(name)
    db.reset()
    log(f"conformance failed: {failed}" if failed else "conformance passed for every stack")
    return 1 if failed else 0


def cmd_run(ctx, args):
    stacks = stack_selection(ctx.cfg, ctx.cluster, args.stacks)
    tests = ALL_TESTS if args.tests in (None, "all") else [int(t) for t in args.tests.split(",")]
    if not stacks:
        raise SystemExit("no runnable stacks")
    secs = estimate(ctx.cfg, len(stacks), tests)
    if args.dry_run:
        print(f"mode {ctx.cluster.mode}; stacks {stacks}; tests {tests}")
        print(f"per test: smoke {ctx.cfg['search']['smoke']['duration_s']} s, warm-up "
              f"{ctx.cfg['search']['warmup']['duration_s']} s, probe, ~5 steps × "
              f"({ctx.cfg['search']['settle_s']} + {ctx.cfg['search']['measure_s']} s) + recovery")
        print(f"estimated duration: {secs / 3600:.1f} h")
        return 0
    campaign = Campaign.create(ctx, args.campaign, args.seed, stacks, tests)
    log(f"estimated duration: {secs / 3600:.1f} h")
    campaign.run()
    return 0


def cmd_resume(ctx, args):
    Campaign(ctx, args.campaign_id).run()
    return 0


def cmd_common_load(ctx, args):
    c = Campaign(ctx, args.campaign_id)
    for stack in c.manifest["stacks"]:
        done = c.dir / stack / "common-load" / "result.json"
        if done.exists():
            done.rename(done.with_suffix(".previous.json"))
    c.common_load()
    c.write_csv()
    return 0


def cmd_csv(ctx, args):
    Campaign(ctx, args.campaign_id).write_csv()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="default: config/bench.toml")
    ap.add_argument("--hosts", help="default: runner/hosts.toml (missing = local mode)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("conformance")
    p.add_argument("--stacks", default="all")
    sub.add_parser("calibrate")
    p = sub.add_parser("run")
    p.add_argument("--stacks", default="all", help="comma-separated, or all")
    p.add_argument("--tests", default="all", help="comma-separated test numbers, or all")
    p.add_argument("--campaign", help="campaign id (default: <utc time>-<git sha>)")
    p.add_argument("--seed", type=int, help="shuffle seed (default: random, saved in the manifest)")
    p.add_argument("--dry-run", action="store_true", help="print the plan and estimated duration only")
    for name in ("resume", "common-load", "csv"):
        sub.add_parser(name).add_argument("campaign_id")
    args = ap.parse_args()

    ctx = Context(Config(pathlib.Path(args.config) if args.config else None), Cluster(args.hosts))
    handlers = {"check": cmd_check, "conformance": cmd_conformance, "calibrate": lambda c, a: calibrate(c) and 0,
                "run": cmd_run, "resume": cmd_resume, "common-load": cmd_common_load, "csv": cmd_csv}
    sys.exit(handlers[args.cmd](ctx, args))


if __name__ == "__main__":
    main()

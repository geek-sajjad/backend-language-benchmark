"""Campaign orchestration: run, resume, common-load, csv (docs/06-runner-and-results.md)."""
import contextlib
import csv
import io
import random
import shutil
import subprocess
import time

from .cluster import Database, Stack
from .config import MIXED_TEST
from .k6 import K6, k6_version
from .prom import Prom
from .search import InvalidStep, Pair, SetupError, run_pair
from .spot import FINGERPRINT_KEYS, SpotInterruption, SpotWatch, fingerprint
from .util import REPO, atomic_write, atomic_write_json, log, read_json, set_log_file, utc_now


class Context:
    def __init__(self, cfg, cluster):
        self.cfg, self.cluster = cfg, cluster
        self.prom = Prom(cluster.prometheus_url)
        self.k6 = K6(cfg, cluster, self.prom)


def git(*args):
    p = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


class Campaign:
    def __init__(self, ctx, campaign_id):
        self.ctx, self.cfg, self.cluster = ctx, ctx.cfg, ctx.cluster
        self.id = campaign_id
        self.dir = REPO / "results" / campaign_id
        set_log_file(self.dir / "campaign.log")

    @classmethod
    def create(cls, ctx, name=None, seed=None, stacks=(), tests=()):
        sha = git("rev-parse", "--short", "HEAD") or "nogit"
        cid = name or f"{utc_now().strftime('%Y-%m-%dT%H%MZ')}-{sha}"
        c = cls(ctx, cid)
        if (c.dir / "manifest.json").exists():
            raise SystemExit(f"campaign {cid} already exists; use `resume {cid}`")
        c.write_manifest(seed if seed is not None else random.SystemRandom().randrange(2**31), stacks, tests)
        return c

    @property
    def manifest(self):
        return read_json(self.dir / "manifest.json")

    # ------------------------------------------------------------------ manifest

    def write_manifest(self, seed, stacks, tests):
        c, db = self.cluster, Database(self.cluster)

        def safe(fn):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 - recorded, not fatal
                return f"unavailable: {e}"

        manifest = {
            "campaign_id": self.id,
            "created": utc_now().isoformat(),
            "mode": c.mode,
            "git_sha": git("rev-parse", "HEAD"),
            "git_dirty": bool(git("status", "--porcelain")),
            "config_sha256": self.cfg.sha256,
            "shuffle_seed": seed,
            "stacks": list(stacks),
            "tests": list(tests),
            "hosts": fingerprint(c),  # compared by `resume` (06 "Spot interruptions")
            "k6": k6_version(),
            "postgres": safe(db.version),
            "image_digests": safe(lambda: c.app.sh("cat results/image-digests.json", check=False).strip() or None),
            "calibration": read_json(REPO / "results" / "calibration.json"),
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.cfg.path, self.dir / "bench.toml")
        atomic_write_json(self.dir / "manifest.json", manifest)

    # ------------------------------------------------------------------ run

    def pair_dir(self, stack, test):
        return self.dir / stack / str(test)

    def order(self, test):
        stacks = list(self.manifest["stacks"])
        random.Random(f"{self.manifest['shuffle_seed']}-{test}").shuffle(stacks)
        return stacks

    def run(self):
        m = self.manifest
        log(f"campaign {self.id}: stacks {m['stacks']}, tests {m['tests']}")
        with self.watching():
            for test in m["tests"]:  # test-major order
                for stack in self.order(test):
                    out = self.pair_dir(stack, test)
                    if (out / "result.json").exists():
                        continue
                    log(f"{stack} · test {test}")
                    self.start_pair(stack, test, out)
                    try:
                        r = run_pair(self.ctx, stack, test, out)
                    except InvalidStep as e:
                        log(f"INVALID: {stack} test {test}: {e}")
                        log(f"fix the cause, then: python3 runner/bench.py resume {self.id}")
                        raise SystemExit(2)
                    except SpotInterruption:
                        self.pause(stack, test)
                    log(f"  → {r['status']}, max_rps {r.get('max_rps')}")
            if MIXED_TEST in m["tests"]:
                self.common_load()
        self.write_csv()

    def start_pair(self, stack, test, out):
        """Records the pair in progress and sets aside the steps of an earlier unfinished attempt
        (INVALID, interrupted or crashed): a pair is only ever scored from one complete search."""
        atomic_write_json(self.dir / "current.json", {"stack": stack, "test": test, "started": utc_now().isoformat()})
        steps = out / "steps"
        if steps.exists() and any(steps.iterdir()):
            dest = out / "discarded" / utc_now().strftime("%Y%m%dT%H%M%SZ")
            dest.parent.mkdir(parents=True, exist_ok=True)
            steps.rename(dest)
            log(f"  unfinished earlier attempt moved to {dest.relative_to(self.dir)}")

    # ------------------------------------------------------------------ spot interruptions

    @contextlib.contextmanager
    def watching(self):
        watch = SpotWatch(self.cluster).start()
        self.ctx.k6.spot = watch
        try:
            yield watch
        finally:
            watch.stop()
            self.ctx.k6.spot = None

    def pause(self, stack, test):
        """Finished pairs stay as they are; the interrupted pair is rerun by `resume` (06)."""
        record = self.ctx.k6.spot.paused_record(stack, test)
        atomic_write_json(self.dir / "paused.json", record)
        log(f"PAUSED (paused_spot): {record['host']}: {record['reason']} during {stack} test {test}")
        log("results so far are saved. When the hosts run again: terraform apply -refresh-only, "
            f"deploy.sh, then python3 runner/bench.py resume {self.id}")
        raise SystemExit(3)

    def check_hardware(self):
        """Before `resume`: refuse unless every host matches the manifest's fingerprint. A host
        that restarted (new boot_id) runs on another physical machine now; if that is the app host,
        the finished pairs of the current test are rerun, so one test is never split across machines."""
        if self.cluster.local:
            return
        m = self.manifest
        now = fingerprint(self.cluster)
        diffs = [f"{host} {k}: {rec[k]!r} → {now.get(host, {}).get(k)!r}"
                 for host, rec in m["hosts"].items() for k in FINGERPRINT_KEYS
                 if k in rec and now.get(host, {}).get(k) != rec[k]]
        for key, current in (("k6", k6_version()), ("postgres", Database(self.cluster).version)):
            try:
                current = current() if callable(current) else current
            except Exception as e:  # noqa: BLE001
                current = f"unavailable: {e}"
            if m.get(key) and current != m[key]:
                diffs.append(f"{key}: {m[key]!r} → {current!r}")
        if diffs:
            for d in diffs:
                log(f"hardware differs: {d}")
            log("not resuming: the campaign stays paused until identical hardware is available")
            raise SystemExit(3)

        paused = read_json(self.dir / "paused.json")
        restarted = [h for h, rec in m["hosts"].items() if rec.get("boot_id") and now[h]["boot_id"] != rec["boot_id"]]
        if not paused and not restarted:
            return
        current = read_json(self.dir / "current.json", {})
        test = (paused or current).get("test")
        rerun = self.supersede(test) if "app" in restarted and test is not None else []
        entry = {"paused": paused, "resumed": utc_now().isoformat(), "restarted_hosts": restarted,
                 "rerun_test": test if rerun else None, "rerun_stacks": rerun}
        m.setdefault("pauses", []).append(entry)
        for h in restarted:
            m["hosts"][h]["boot_id"] = now[h]["boot_id"]
        atomic_write_json(self.dir / "manifest.json", m)
        (self.dir / "paused.json").unlink(missing_ok=True)
        log(f"resuming after pause: restarted hosts {restarted or 'none'}"
            + (f"; test {test} rerun for {', '.join(rerun)}" if rerun else ""))

    def supersede(self, test):
        """Sets aside the finished results of one test (or the common-load phase) for every stack."""
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        rerun = []
        for stack in self.manifest["stacks"]:
            d = self.dir / stack / "common-load" if test == "common" else self.pair_dir(stack, test)
            if (d / "result.json").exists():
                (d / "result.json").rename(d / f"result.superseded-{stamp}.json")
                rerun.append(stack)
        return rerun

    # ------------------------------------------------------------------ common load

    def common_load(self):
        """Test 9 at L_common = 0.8 × the lowest passing max_rps (04 "Test 9 extras")."""
        rates = {}
        for stack in self.manifest["stacks"]:
            r = read_json(self.pair_dir(stack, MIXED_TEST) / "result.json")
            if r and r["status"] == "ok":
                rates[stack] = r["max_rps"]
        if not rates:
            log("common load: no stack passed test 9, skipped")
            return
        l_common = round(self.cfg["common_load"]["factor"] * min(rates.values()), 1)
        duration = self.cfg["common_load"]["duration_s"]
        log(f"common load: L_common = {l_common} req/s for {duration} s")
        for stack in self.order("common"):
            out = self.dir / stack / "common-load"
            if (out / "result.json").exists() or stack not in rates:
                continue
            log(f"{stack} · common load")
            self.start_pair(stack, "common", out)
            pair = Pair(self.ctx, stack, MIXED_TEST, out)
            spot = self.ctx.k6.spot
            try:
                pair.reset()
                pair.smoke()
                pair.warmup()
                sampler = pair.stack.memory_sampler(duration + 5)
                start = time.time()
                n = pair._k6("common", rate=l_common, measure_s=duration)
                metrics = self.ctx.prom.window(start + duration, duration)
                samples = [int(x) for x in sampler.communicate(timeout=60)[0].split() if x.isdigit()]
                state = pair.stack.state() or {}
            except SpotInterruption:
                self.pause(stack, "common")
            except Exception as e:
                if spot and spot.explains():
                    self.pause(stack, "common")
                if not isinstance(e, SetupError):
                    raise
                log(f"  SETUP ERROR: {e}")
                continue
            if spot and spot.tripped.is_set():
                self.pause(stack, "common")
            peak_cgroup = round(max(samples) / 2**20, 1) if samples else None
            metric = self.cfg.get("memory", "metric", default="cgroup_anon_shmem")
            peak = peak_cgroup if metric == "cgroup_anon_shmem" else metrics.get("app_rss_mb")
            failed = not state.get("Running", True)
            result = {
                "stack": stack, "l_common": l_common, "duration_s": duration,
                "status": ("oom" if state.get("OOMKilled") else "crash") if failed else "ok",
                "p99_common_ms": n["p99_ms"], "p95_common_ms": n["p95_ms"], "error_rate": n["error_rate"],
                "measured_rps": n["measured_rps"], "endpoints": n["endpoints"],
                "peak_rss_mb": peak, "memory_metric": metric,
                "peak_cgroup_anon_shmem_mb": peak_cgroup, "peak_cadvisor_rss_mb": metrics.get("app_rss_mb"),
                "metrics": metrics,
            }
            atomic_write_json(out / "result.json", result)
            atomic_write(out / "app.log", pair.stack.logs())
            log(f"  p99 {n['p99_ms']:.2f} ms, peak memory {peak} MB ({metric})")

    # ------------------------------------------------------------------ results.csv

    def write_csv(self):
        rows = []
        stacks = self.cfg.stacks
        for stack in self.manifest["stacks"]:
            common = read_json(self.dir / stack / "common-load" / "result.json")
            for test in self.manifest["tests"]:
                r = read_json(self.pair_dir(stack, test) / "result.json")
                if not r:
                    continue
                row = {"stack": stack, "language": stacks.get(stack, ""), "test": test,
                       "max_rps": r["max_rps"] if r["status"] == "ok" else "", "status": r["status"],
                       "p99_common_ms": "", "peak_rss_mb": "", "db_bound": int(bool(r.get("db_bound")))}
                if test == MIXED_TEST and r["status"] == "ok":
                    # A crash or OOM during the common-load run scores latency and memory as failures (09 C6).
                    if common and common["status"] == "ok":
                        row["p99_common_ms"] = round(common["p99_common_ms"], 2)
                        row["peak_rss_mb"] = common["peak_rss_mb"]
                    elif common:
                        row["status"] = common["status"]
                        row["max_rps"] = ""
                rows.append(row)
        buf = io.StringIO()
        w = csv.DictWriter(buf, ["stack", "language", "test", "max_rps", "status", "p99_common_ms", "peak_rss_mb", "db_bound"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        atomic_write(self.dir / "results.csv", buf.getvalue())
        p = subprocess.run(["python3", str(REPO / "scoring" / "score.py"), str(self.dir / "results.csv")],
                           capture_output=True, text=True)
        atomic_write(self.dir / "scores.txt", p.stdout + p.stderr)
        log(f"wrote {self.dir / 'results.csv'} ({len(rows)} rows) and scores.txt")


def estimate(cfg, n_stacks, tests):
    """Rough campaign duration in seconds (06 "Estimated duration")."""
    s = cfg["search"]
    per_pair = 40 + s["smoke"]["duration_s"] + s["warmup"]["duration_s"] + 90 + 6 * (s["settle_s"] + s["measure_s"] + 15)
    total = sum(per_pair + (30 if cfg.is_write_test(t) else 0) for t in tests) * n_stacks
    if MIXED_TEST in tests:
        total += n_stacks * (60 + s["warmup"]["duration_s"] + cfg["common_load"]["duration_s"])
    return total


def stack_selection(cfg, cluster, arg):
    names = list(cfg.stacks) if arg in (None, "all") else [s.strip() for s in arg.split(",")]
    unknown = [n for n in names if n not in cfg.stacks]
    if unknown:
        raise SystemExit(f"unknown stacks: {unknown} (configured: {list(cfg.stacks)})")
    ready = [n for n in names if Stack(cluster, n).exists()]
    skipped = sorted(set(names) - set(ready))
    if skipped:
        log(f"skipping stacks without stacks/<name>/compose.yml: {', '.join(skipped)}")
    return ready

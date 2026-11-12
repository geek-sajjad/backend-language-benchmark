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
from .spot import SESSION_KEYS, SPEC_KEYS, SpotInterruption, SpotWatch, fingerprint
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
        c.record_session("run", stacks)
        return c

    def add_stacks(self, stacks, tests):
        """A later session (06 "Sessions: one stack per day"): adds its stacks to the campaign
        after checking that the hosts match the hardware spec recorded by the first session."""
        m = self.manifest
        if not m:
            raise SystemExit(f"no campaign {self.id} in {self.dir.parent}")
        if list(tests) != m["tests"]:
            raise SystemExit(f"campaign {self.id} runs tests {m['tests']}; leave out --tests")
        done = [s for s in stacks if s in m["stacks"]]
        if done:
            raise SystemExit(f"already in campaign {self.id}: {', '.join(done)}; use `resume {self.id}`")
        unfinished = [s for s in m["stacks"] if self.unfinished(s)]
        if unfinished:
            raise SystemExit(f"finish {', '.join(unfinished)} first: python3 runner/bench.py resume {self.id}")
        self.check_hardware()
        m = self.manifest
        m["stacks"] += list(stacks)
        atomic_write_json(self.dir / "manifest.json", m)
        self.record_session("run", stacks)

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
            # written on the hosts and never synced (deploy.sh): results, hosts.toml, Prometheus targets
            "git_dirty": bool(git("status", "--porcelain", "--", ".", ":!results", ":!runner/hosts.toml",
                                  ":!monitoring/prometheus/targets")),
            "config_sha256": self.cfg.sha256,
            "shuffle_seed": seed,
            "stacks": list(stacks),
            "tests": list(tests),
            # the hardware spec: every later session and every resume must match it (06 "Sessions")
            "hosts": {h: {k: rec[k] for k in ("private_ip", *SPEC_KEYS)} for h, rec in fingerprint(c).items()},
            "k6": k6_version(),
            "postgres": safe(db.version),
            "calibration": read_json(REPO / "results" / "calibration.json"),
            "sessions": [],
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

    def record_session(self, kind, stacks):
        """Appends a session to the manifest when the hosts are new (other instance IDs or a
        restart since the last session); otherwise adds the stacks to the current session."""
        m = self.manifest
        sessions = m.setdefault("sessions", [])
        now = fingerprint(self.cluster)
        ids = {h: {k: rec.get(k) for k in SESSION_KEYS} for h, rec in now.items()}
        last = sessions[-1] if sessions else None
        if last and last["kind"] == kind and {h: {k: rec.get(k) for k in SESSION_KEYS}
                                               for h, rec in last["hosts"].items()} == ids:
            last["stacks"] += [s for s in stacks if s not in last["stacks"]]
        else:
            try:
                digests = self.cluster.app.sh("cat results/image-digests.json", check=False, timeout=30).strip()
            except (OSError, subprocess.SubprocessError) as e:
                digests = f"unavailable: {e}"
            sessions.append({"n": len(sessions) + 1, "kind": kind, "started": utc_now().isoformat(),
                             "stacks": list(stacks), "hosts": now, "image_digests": digests or None})
            log(f"session {len(sessions)} ({kind}): {', '.join(stacks)}")
        atomic_write_json(self.dir / "manifest.json", m)

    def run(self, stacks=None):
        """The searches of every unfinished (stack, test) pair, one stack at a time
        (06 "Flow of run"). The common-load phase is separate: `bench.py common-load`."""
        m = self.manifest
        stacks = stacks or m["stacks"]
        log(f"campaign {self.id}: stacks {stacks}, tests {m['tests']}")
        with self.watching():
            for stack in stacks:
                for test in m["tests"]:
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
        self.write_csv()
        if MIXED_TEST in m["tests"]:
            missing = self.missing_mixed()
            log(f"still without a test {MIXED_TEST} result: {', '.join(missing)}" if missing else
                f"every stack has a test {MIXED_TEST} result: python3 runner/bench.py common-load {self.id}")

    def unfinished(self, stack):
        return any(not (self.pair_dir(stack, t) / "result.json").exists() for t in self.manifest["tests"])

    def missing_mixed(self, partial=False):
        """Stacks without a test 9 result. L_common depends on every stack, so unless `partial`,
        every configured stack counts, also those whose session hasn't run yet."""
        stacks = list(self.manifest["stacks"])
        if not partial:
            stacks += [s for s in stack_selection(self.cfg, self.cluster, "all", quiet=True) if s not in stacks]
        return [s for s in stacks if not (self.pair_dir(s, MIXED_TEST) / "result.json").exists()]

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
        """Before a later session, `resume` or `common-load`: refuse unless every host matches the
        campaign's hardware spec. Instance IDs and boot_ids are not compared: each session runs on
        new hosts, and finished pairs are kept when a host moves (06 "Spot interruptions")."""
        if self.cluster.local:
            return
        m = self.manifest
        now = fingerprint(self.cluster)
        diffs = [f"{host} {k}: {rec[k]!r} → {now.get(host, {}).get(k)!r}"
                 for host, rec in m["hosts"].items() for k in SPEC_KEYS
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
            log("not continuing: the campaign stays paused until matching hardware is available")
            raise SystemExit(3)

        paused = read_json(self.dir / "paused.json")
        if paused:
            m.setdefault("pauses", []).append({"paused": paused, "resumed": utc_now().isoformat()})
            atomic_write_json(self.dir / "manifest.json", m)
            (self.dir / "paused.json").unlink(missing_ok=True)
            log(f"resuming after pause ({paused['host']}: {paused['reason']}); "
                f"only {paused['stack']} test {paused['test']} is rerun")

    # ------------------------------------------------------------------ common load

    def common_load(self):
        """Test 9 at L_common = 0.8 × the lowest passing max_rps (04 "Test 9 extras"), every stack
        in one session (session 8)."""
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
                       "p99_common_ms": "", "peak_rss_mb": "", "db_bound": int(bool(r.get("db_bound"))),
                       "loadgen_bound": int(bool(r.get("loadgen_bound")))}
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
        w = csv.DictWriter(buf, ["stack", "language", "test", "max_rps", "status", "p99_common_ms", "peak_rss_mb", "db_bound",
                                 "loadgen_bound"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        atomic_write(self.dir / "results.csv", buf.getvalue())
        p = subprocess.run(["python3", str(REPO / "scoring" / "score.py"), str(self.dir / "results.csv")],
                           capture_output=True, text=True)
        atomic_write(self.dir / "scores.txt", p.stdout + p.stderr)
        log(f"wrote {self.dir / 'results.csv'} ({len(rows)} rows) and scores.txt")


def estimate(cfg, n_stacks, tests):
    """Rough duration of the searches in seconds, without the common-load phase (06 "Estimated duration")."""
    s = cfg["search"]
    per_pair = 40 + s["smoke"]["duration_s"] + s["warmup"]["duration_s"] + 90 + 6 * (s["settle_s"] + s["measure_s"] + 15)
    return sum(per_pair + (30 if cfg.is_write_test(t) else 0) for t in tests) * n_stacks


def stack_selection(cfg, cluster, arg, quiet=False):
    names = list(cfg.stacks) if arg in (None, "all") else [s.strip() for s in arg.split(",")]
    unknown = [n for n in names if n not in cfg.stacks]
    if unknown:
        raise SystemExit(f"unknown stacks: {unknown} (configured: {list(cfg.stacks)})")
    ready = [n for n in names if Stack(cluster, n).exists()]
    skipped = sorted(set(names) - set(ready))
    if skipped and not quiet:
        log(f"skipping stacks without stacks/<name>/compose.yml: {', '.join(skipped)}")
    return ready

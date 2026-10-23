"""The max_rps search for one (stack, test) pair (docs/04-methodology.md "Finding max_rps")."""
import time

from .cluster import CommandError, Database, Stack
from .config import MIXED_TEST
from .k6 import K6Error
from .spot import SpotInterruption
from .util import atomic_write, atomic_write_json, log


class InvalidStep(RuntimeError):
    """The setup, not the app, limited the step. The campaign stops; fix it and `resume`."""


class SetupError(RuntimeError):
    """The smoke test failed: a broken deployment, never scored."""


class Pair:
    def __init__(self, ctx, stack_name, test, out_dir):
        self.ctx, self.test, self.out = ctx, test, out_dir
        self.cfg, self.cluster, self.k6, self.prom = ctx.cfg, ctx.cluster, ctx.k6, ctx.prom
        self.stack = Stack(self.cluster, stack_name)
        self.db = Database(self.cluster)
        self.search_cfg = self.cfg["search"]
        self.slo = self.cfg["slo"]
        self.validity = self.cfg["validity"]
        self.write = self.cfg.is_write_test(test)
        self.attempt = 1 + len(list(out_dir.glob("search*.json")))
        self.seq = 0
        self.baseline_p50 = None
        self.cores = None
        self.events = []

    # ------------------------------------------------------------------ k6 helpers

    def _k6(self, label, **kw):
        self.seq += 1
        path = self.out / "steps" / f"a{self.attempt}-{self.seq:02d}-{label}.summary.json"
        # unique per k6 run (attempt + sequence): a confirm rerun at the same rate must not share
        # Prometheus series with the first run (step(): dropped iterations per window)
        tags = {"testid": f"{self.stack.name}/{self.test}/a{self.attempt}-{self.seq:02d}-{label}", "stack": self.stack.name,
                "test": str(self.test)}
        self.last_testid = tags["testid"]
        return self.k6.run(self.test, path, tags=tags, **kw)

    def reset(self):
        if self.write:
            log("  recreate bench from bench_template")
            self.db.reset()
        startup = self.stack.restart()
        if self.cores is None:
            self.cores = self.stack.cpu_cores()
        log(f"  {self.stack.name} healthy after {startup} s")
        return startup

    def smoke(self):
        s = self.search_cfg["smoke"]
        n = self._k6("smoke", rate=s["rate"], measure_s=s["duration_s"], check_body=True)
        if n["requests"] == 0 or n["checks_failed"] or n["error_rate"] > 0:
            raise SetupError(f"smoke: {n['requests']} requests, {n['checks_failed']} failed checks, "
                             f"error rate {n['error_rate']:.2%}")
        self.baseline_p50 = n["p50_ms"]
        log(f"  smoke ok, p50 {n['p50_ms']:.2f} ms")
        return n

    def warmup(self):
        w = self.search_cfg["warmup"]
        to_rate = self.cfg.override("search.warmup", "to_rate", self.test, w["to_rate"])
        self._k6("warmup", mode="warmup", from_rate=w["from_rate"], to_rate=to_rate, duration_s=w["duration_s"])

    def slo_ok(self, n):
        return (n["requests"] > 0 and n["p95_ms"] is not None
                and n["p95_ms"] < self.slo["p95_ms"] and n["p99_ms"] < self.slo["p99_ms"]
                and n["error_rate"] < self.slo["error_rate"])

    # ------------------------------------------------------------------ probe

    def probe(self):
        """Doubling stages, each a short fixed-rate run: (stage_s - window_s) settle, then a
        window_s measure. E = the last stage whose window met the SLO. This replaces the single
        ramping run + `--out json` of the original design (09 R4): exact numbers per window and
        no per-sample output that could load the generator."""
        p = self.search_cfg["probe"]
        rate = self.cfg.override("search.probe", "start_rate", self.test, p["start_rate"])
        last_ok = None
        stages = []
        while rate <= p["max_rate"]:
            n = self._k6(f"probe-{int(rate)}", rate=rate, settle_s=p["stage_s"] - p["window_s"],
                         measure_s=p["window_s"])
            ok = self.slo_ok(n)
            stages.append({"rate": rate, "ok": ok, **{k: n[k] for k in ("p95_ms", "p99_ms", "error_rate", "dropped_iterations")}})
            if not ok:
                break
            last_ok = rate
            rate *= p["factor"]
        lowest = self.search_cfg["lowest_rate"]
        estimate = last_ok if last_ok is not None else max(lowest, stages[0]["rate"] / p["factor"])
        log(f"  probe: E = {estimate:.0f} req/s ({len(stages)} stages)")
        return estimate, stages

    # ------------------------------------------------------------------ steps

    def step(self, rate):
        s = self.search_cfg
        eth_before = self.cluster.ethtool()
        start = time.time()
        tail = min(5, s["settle_s"])
        n = self._k6(f"step-{int(rate)}", rate=rate, settle_s=s["settle_s"], tail_s=tail,
                     measure_s=s["measure_s"])
        # k6 starts sending after its VUs are initialised (seconds with a large pool), so the
        # window is anchored at the end of the run, not at the launch
        end = time.time()
        eth_after = self.cluster.ethtool()
        time.sleep(3 if self.prom.available() else 0)  # let the last 1 s scrapes land
        metrics = self.prom.window(end, s["measure_s"])
        # The settle phase is excluded from all checks (04), but k6 counts dropped iterations over
        # the whole run: drops at the jump to a new rate (app start-up spike) aren't the
        # generator's fault. The validity check uses the settle tail + measure window from the
        # Prometheus counter; the total stays in the record. Without Prometheus: the total.
        n["dropped_total"] = n["dropped_iterations"]
        windowed = self.prom.counter_delta("k6_dropped_iterations_total", self.last_testid,
                                           end - s["measure_s"] - tail, end)
        if windowed is not None:
            n["dropped_iterations"] = int(round(windowed))
        state = self.stack.state() or {}

        verdict, reasons = self.judge(n, metrics, eth_before, eth_after, state)
        record = {"rate": rate, "verdict": verdict, "reasons": reasons, "db_bound": "db_bound" in reasons,
                  "started": start, **n,
                  "metrics": metrics, "ethtool": {"before": eth_before, "after": eth_after},
                  "container": {k: state.get(k) for k in ("Running", "OOMKilled", "ExitCode", "Status")}}
        if verdict == "pass" and n["settle_tail_p99_ms"] and n["p99_ms"]:
            drift = abs(n["settle_tail_p99_ms"] - n["p99_ms"]) / n["p99_ms"]
            record["settle_unstable"] = drift > s["settle_unstable_threshold"]
        atomic_write_json(self.out / "steps" / f"a{self.attempt}-{self.seq:02d}-step-{int(rate)}.json", record)
        startup = n["dropped_total"] - n["dropped_iterations"]
        log(f"  step {rate:>9.0f} req/s → {verdict:<4} p95 {fmt(n['p95_ms'])} p99 {fmt(n['p99_ms'])} "
            f"err {n['error_rate']:.2%} dropped {n['dropped_iterations']}"
            f"{f' (+{startup} at start)' if startup > 0 else ''} {' '.join(reasons)}")
        return record

    def judge(self, n, metrics, eth_before, eth_after, state):
        reasons = []
        if not state.get("Running", True):
            kind = "oom" if state.get("OOMKilled") else "crash"
            self.events.append({"event": kind, "exit_code": state.get("ExitCode")})
            return "fail", [kind]
        slo_ok = self.slo_ok(n)
        if not slo_ok:
            reasons.append("slo")
        # Dropped iterations mean the generator could not keep up, unless the app was already
        # failing the SLO: then k6 simply ran out of VUs waiting on a slow app (the step fails).
        if n["dropped_iterations"] > self.validity["max_dropped_iterations"] and slo_ok:
            raise InvalidStep(f"dropped_iterations={n['dropped_iterations']} at a passing rate")
        if not self.cluster.local:
            if self.validity["check_ethtool_allowances"]:
                for host, counters in eth_after.items():
                    for k, v in counters.items():
                        if v > eth_before.get(host, {}).get(k, 0):
                            raise InvalidStep(f"{host} {k} increased")
            for key, limit in (("db_cpu", self.validity["max_db_cpu"]), ("loadgen_cpu", self.validity["max_loadgen_cpu"])):
                if metrics.get(key) is None:
                    raise InvalidStep(f"{key} missing from Prometheus")
                if metrics[key] < limit:
                    continue
                # In the DB tests Postgres may be the bottleneck: the step stays valid and is
                # flagged, so the result can be reported as DB-limited (04 "When Postgres is the bottleneck").
                if key == "db_cpu" and self.test in self.validity.get("db_bound_tests", []):
                    reasons.append("db_bound")
                    continue
                raise InvalidStep(f"{key} {metrics[key]:.0%} >= {limit:.0%}")
        return ("pass" if slo_ok else "fail"), reasons

    # ------------------------------------------------------------------ recovery

    def app_cpu_fraction(self):
        cores_used = self.prom.app_cpu_now()
        if cores_used is not None:
            return cores_used / self.cores
        return self.stack.docker_cpu_fraction(self.cores)

    def recover(self):
        """Wait until the app is idle and answers at baseline speed again (04 "Recovery check")."""
        r = self.search_cfg["recovery"]
        start = time.time()
        if not (self.stack.state() or {}).get("Running", False):
            return self.restart_and_warm(start, "container not running")
        time.sleep(r["min_s"])
        while time.time() - start < r["max_s"]:
            cpu = self.app_cpu_fraction()
            if cpu is not None and cpu >= r["app_cpu_below"]:
                time.sleep(2)
                continue
            if self.db.active_app_queries() > 0:
                time.sleep(2)
                continue
            n = self._k6("recovery", rate=r["probe_rate"], measure_s=r["probe_s"])
            limit = r["probe_p50_factor"] * self.baseline_p50 + 1
            if n["error_rate"] == 0 and n["p50_ms"] is not None and n["p50_ms"] <= limit:
                if self.write:
                    self.db.checkpoint()
                return {"recovery_s": round(time.time() - start, 1), "recovery_restart": False}
        return self.restart_and_warm(start, "not recovered")

    def restart_and_warm(self, start, why):
        log(f"  {why} after {time.time() - start:.0f} s: restart + warm-up")
        self.events.append({"event": "recovery_restart", "why": why})
        self.stack.restart()
        self.warmup()
        return {"recovery_s": round(time.time() - start, 1), "recovery_restart": True}

    # ------------------------------------------------------------------ search

    def run(self):
        """Returns the pair result. Raises SetupError or InvalidStep."""
        s = self.search_cfg
        b = s["bracket"]
        result = {"stack": self.stack.name, "test": self.test, "attempt": self.attempt}
        try:
            result["startup_s"] = self.reset()
            self.smoke()
            self.warmup()
            estimate, probe_stages = self.probe()
            result.update(estimate=estimate, probe=probe_stages, probe_recovery=self.recover())

            steps, tested = [], set()
            rate = max(s["lowest_rate"], round(b["first_step"] * estimate))
            converged = False
            while len(steps) < s["max_steps"]:
                rec = self.step(rate)
                rec.update(self.recover())
                steps.append(rec)
                tested.add(rate)
                nxt, done = self.next_rate(steps)
                if done:
                    converged = True
                    break
                if nxt in tested:
                    converged = True
                    break
                rate = nxt
            best = self.best_pass(steps)
            confirmed = None
            if best and s.get("confirm_final", False):
                best, confirmed = self.confirm(steps, best)
        finally:
            atomic_write(self.out / "app.log", self.stack.logs())

        result.update(steps=[{k: v for k, v in st.items() if k not in ("ethtool", "endpoints")} for st in steps],
                      converged=converged, confirmed=confirmed, events=self.events, image=self.stack.image())
        rss = [st["metrics"]["app_rss_mb"] for st in steps if st["metrics"].get("app_rss_mb") is not None]
        result["peak_rss_search_mb"] = max(rss) if rss else None
        if best:
            # Test 9: the measured request rate; tests 2-8: the offered rate (02, 04).
            result.update(status="ok", max_rps=best["measured_rps"] if self.test == MIXED_TEST else best["rate"],
                          best_step=best["rate"], db_bound=best.get("db_bound", False))
        else:
            crashes = [e["event"] for e in self.events if e["event"] in ("crash", "oom")]
            result.update(status=crashes[-1] if crashes else "slo_fail", max_rps=None)
        result["settle_unstable"] = any(st.get("settle_unstable") for st in steps)
        result["db_bound_steps"] = sum(1 for st in steps if st.get("db_bound"))
        atomic_write_json(self.out / f"search.a{self.attempt}.json", result)
        return result

    def best_pass(self, steps):
        """Highest passing step below the lowest failing rate (09 R5): a pass above a failure is
        treated as noise and not used."""
        fails = [st["rate"] for st in steps if st["verdict"] != "pass"]
        lowest_fail = min(fails) if fails else float("inf")
        passes = [st for st in steps if st["verdict"] == "pass" and st["rate"] < lowest_fail]
        return max(passes, key=lambda st: (st["rate"], st["started"])) if passes else None

    def next_rate(self, steps):
        """(next rate, done). Bracket ×1.15 / ×0.75, then bisect to the 5% resolution."""
        s, b = self.search_cfg, self.search_cfg["bracket"]
        last = steps[-1]
        fails = [st["rate"] for st in steps if st["verdict"] != "pass"]
        best = self.best_pass(steps)
        if not fails:
            if last["rate"] >= s["probe"]["max_rate"]:
                return None, True
            return min(s["probe"]["max_rate"], round(last["rate"] * b["up"])), False
        lowest_fail = min(fails)
        if best is None:
            if last["rate"] <= s["lowest_rate"]:
                return None, True  # slo_fail
            return max(s["lowest_rate"], round(last["rate"] * b["down"])), False
        if (lowest_fail - best["rate"]) / best["rate"] <= s["bisect_resolution"]:
            return None, True
        return round((best["rate"] + lowest_fail) / 2), False

    def confirm(self, steps, best):
        """Rerun the final rate once (09 R5). If it fails, step down one bisection interval
        and try again, at most 3 times. Returns (best step, confirmed)."""
        s = self.search_cfg
        fails = [st["rate"] for st in steps if st["verdict"] != "pass"]
        interval = (min(fails) - best["rate"]) if fails else best["rate"] * s["bisect_resolution"]
        rate = best["rate"]
        for _ in range(3):
            rec = self.step(rate)
            rec.update(self.recover())
            rec["confirmation"] = True
            steps.append(rec)
            if rec["verdict"] == "pass":
                return rec, True
            rate = round(rate - max(interval, rate * s["bisect_resolution"]))
            if rate < s["lowest_rate"]:
                break
        return None, False


def fmt(ms):
    return f"{ms:7.2f}" if ms is not None else "      -"


def run_pair(ctx, stack, test, out_dir):
    """Runs a pair and writes result.json. SetupError → status setup_error (not scored).
    A spot interruption anywhere in the pair raises SpotInterruption and nothing is written."""
    pair = Pair(ctx, stack, test, out_dir)
    spot = ctx.k6.spot
    try:
        result = pair.run()
    except SetupError as e:
        if spot and spot.explains():
            raise SpotInterruption(spot.host, spot.reason) from e
        log(f"  SETUP ERROR: {e}")
        result = {"stack": stack, "test": test, "status": "setup_error", "error": str(e), "max_rps": None}
        atomic_write(out_dir / "app.log", pair.stack.logs())
    except (CommandError, K6Error, InvalidStep) as e:
        # A host going away makes commands fail and Prometheus lose its metrics: that is an
        # interruption, not an INVALID setup.
        if spot and spot.explains():
            raise SpotInterruption(spot.host, spot.reason) from e
        if isinstance(e, InvalidStep):
            raise
        raise InvalidStep(f"setup command failed: {e}") from e
    if spot:
        spot.check()  # a notice that arrived after the last step still discards the pair
    if result["status"] != "setup_error":
        atomic_write_json(out_dir / "result.json", result)
    return result

"""Runs one k6 process and turns its summary into the numbers the runner judges."""
import json
import os
import subprocess

from .config import TEST_SCRIPTS
from .util import REPO


class K6Error(RuntimeError):
    pass


def k6_version():
    return subprocess.run(["k6", "version"], capture_output=True, text=True).stdout.strip()


class K6:
    def __init__(self, cfg, cluster, prom):
        self.cfg, self.cluster, self.prom = cfg, cluster, prom

    def run(self, test, summary_path, *, mode="step", rate=0.0, settle_s=0, tail_s=0, measure_s=0,
            from_rate=0.0, to_rate=0.0, duration_s=0, check_body=False, tags=None, base_url=None,
            script=None):
        """Rates are in req/s; test 9 is converted to sessions/s here. Returns parse_summary()."""
        per_iter = self.cfg.requests_per_iteration(test) if test else 1.0
        slo = self.cfg["slo"]
        env = {
            "BASE_URL": base_url or self.cluster.base_url,
            "MODE": mode,
            "RATE": rate / per_iter,
            "SETTLE_S": settle_s,
            "SETTLE_TAIL_S": tail_s,
            "MEASURE_S": measure_s,
            "FROM_RATE": from_rate / per_iter,
            "TO_RATE": to_rate / per_iter,
            "DURATION_S": duration_s,
            "CHECK_BODY": "1" if check_body else "0",
            "TIMEOUT_S": slo["request_timeout_s"],
            "P95_MS": slo["p95_ms"],
            "P99_MS": slo["p99_ms"],
            "ERROR_RATE": slo["error_rate"],
            "SUMMARY_FILE": str(summary_path),
            "JOURNEY_WEIGHTS": self.cfg.journey_weights(),
            "AVG_REQUESTS_PER_SESSION": per_iter,
        }
        argv = []
        cpuset = self.cfg.get("loadgen", "k6_cpuset")
        if cpuset and not self.cluster.local:
            argv += ["taskset", "-c", cpuset]
        argv += ["k6", "run", "--quiet", "--no-color", "--no-usage-report"]
        for k, v in (tags or {}).items():
            argv += ["--tag", f"{k}={v}"]
        for k, v in env.items():
            argv += ["-e", f"{k}={v}"]
        proc_env = dict(os.environ)
        if self.prom.available():
            argv += ["--out", "experimental-prometheus-rw"]
            proc_env.update({
                "K6_PROMETHEUS_RW_SERVER_URL": self.cluster.prometheus_url + "/api/v1/write",
                "K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM": "true",
                "K6_PROMETHEUS_RW_PUSH_INTERVAL": "1s",
            })
        argv.append(str(REPO / "k6" / "tests" / (script or TEST_SCRIPTS[test])))

        summary_path.parent.mkdir(parents=True, exist_ok=True)
        length = duration_s if mode == "warmup" else settle_s + measure_s
        p = subprocess.run(argv, cwd=REPO, env=proc_env, capture_output=True, text=True,
                           timeout=length + 120)
        # 0 = thresholds passed, 99 = a threshold failed (the runner judges the numbers itself).
        if p.returncode not in (0, 99) or not summary_path.exists():
            raise K6Error(f"k6 exited {p.returncode}: {p.stderr.strip()[-2000:]}")
        return parse_summary(summary_path, measure_s, mode)


def parse_summary(path, measure_s, mode="step"):
    metrics = json.loads(path.read_text())["metrics"]

    def value(key, field, default=None):
        return metrics.get(key, {}).get("values", {}).get(field, default)

    dur = "http_req_duration{phase:measure}"
    requests = value("http_reqs{phase:measure}", "count", 0)
    endpoints = {}
    for key in metrics:
        if key.startswith("http_req_duration{phase:measure,name:"):
            name = key[len("http_req_duration{phase:measure,name:"):-1]
            endpoints[name] = {
                "p99_ms": value(key, "p(99)"),
                "error_rate": value(f"http_req_failed{{phase:measure,name:{name}}}", "rate"),
            }
    return {
        "mode": mode,
        "requests": requests,
        "iterations": value("iterations{phase:measure}", "count", 0),
        # count ÷ measure window, never the submetric's own `rate` (docs/06, "Reading the k6 summary")
        "measured_rps": round(requests / measure_s, 2) if measure_s else None,
        "p50_ms": value(dur, "med"),
        "p95_ms": value(dur, "p(95)"),
        "p99_ms": value(dur, "p(99)"),
        "max_ms": value(dur, "max"),
        "error_rate": value("http_req_failed{phase:measure}", "rate", 0.0),
        "dropped_iterations": value("dropped_iterations", "count", 0),
        "settle_tail_p99_ms": value("http_req_duration{phase:settle_tail}", "p(99)"),
        "checks_failed": value("checks", "fails", 0),
        "endpoints": endpoints,
    }

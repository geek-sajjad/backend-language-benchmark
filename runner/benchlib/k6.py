"""Runs k6 and turns its summary into the numbers the runner judges.

One k6 process tops out at ~20k req/s on the loadgen, with most cores idle: its metrics pipeline
is per process (results/sizing/k6-split-2026-10). A step above `k6_max_rate_per_process`
therefore runs as N processes, each with RATE/N on the whole `k6_cpuset`, started paused and
resumed together, and their summaries are merged (merge_numbers)."""
import json
import math
import os
import re
import socket
import subprocess
import tempfile
import time
import urllib.request

from .config import TEST_SCRIPTS
from .spot import SpotInterruption
from .util import REPO

READY_STATUS = 4  # k6 execution status "paused before run": VUs initialised, waiting for resume
READY_TIMEOUT_S = 120


class K6Error(RuntimeError):
    pass


def k6_version():
    return subprocess.run(["k6", "version"], capture_output=True, text=True).stdout.strip()


def parse_cpuset(spec):
    """"0-13" or "0-3,8,10-11" → [0, 1, ...]."""
    cores = []
    for part in str(spec).split(","):
        if "-" in part:
            lo, hi = part.split("-")
            cores += range(int(lo), int(hi) + 1)
        elif part.strip():
            cores.append(int(part))
    return cores


def split_memlimit(value, n):
    """GOMEMLIMIT for one of n processes, in bytes (Go accepts a plain byte count)."""
    m = re.fullmatch(r"(\d+)\s*(B|KiB|MiB|GiB|TiB)?", str(value).strip())
    if not m:
        raise ValueError(f"k6_gomemlimit {value!r}: expected e.g. 16GiB")
    unit = {"B": 1, None: 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40}[m.group(2)]
    return str(int(m.group(1)) * unit // n)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class K6:
    def __init__(self, cfg, cluster, prom):
        self.cfg, self.cluster, self.prom = cfg, cluster, prom
        self.spot = None  # SpotWatch during a campaign: a trip kills the running k6 processes

    def processes(self, req_rate):
        """How many k6 processes a step at req_rate (req/s) gets: 1 up to k6_max_rate_per_process,
        then one more per further slice, at most one per core of k6_cpuset (all share its cores)."""
        per = self.cfg.get("loadgen", "k6_max_rate_per_process")
        if not per or req_rate <= per:
            return 1
        cores = self._cores()
        return max(1, min(math.ceil(req_rate / per), len(cores)))

    def _cores(self):
        cpuset = self.cfg.get("loadgen", "k6_cpuset")
        if cpuset and not self.cluster.local:
            return parse_cpuset(cpuset)
        return list(range(os.cpu_count() or 1))

    def run(self, test, summary_path, *, mode="step", rate=0.0, settle_s=0, tail_s=0, measure_s=0,
            from_rate=0.0, to_rate=0.0, duration_s=0, check_body=False, tags=None, base_url=None,
            script=None):
        """Rates are in req/s; test 9 is converted to sessions/s here. Returns parse_summary(),
        merged over the processes if the step was split."""
        per_iter = self.cfg.requests_per_iteration(test) if test else 1.0
        n = self.processes(to_rate if mode == "warmup" else rate)
        slo = self.cfg["slo"]
        env = {
            "BASE_URL": base_url or self.cluster.base_url,
            "MODE": mode,
            "RATE": rate / per_iter / n,
            "SETTLE_S": settle_s,
            "SETTLE_TAIL_S": tail_s,
            "MEASURE_S": measure_s,
            "FROM_RATE": from_rate / per_iter / n,
            "TO_RATE": to_rate / per_iter / n,
            "DURATION_S": duration_s,
            "CHECK_BODY": "1" if check_body else "0",
            "TIMEOUT_S": slo["request_timeout_s"],
            "P95_MS": slo["p95_ms"],
            "P99_MS": slo["p99_ms"],
            "ERROR_RATE": slo["error_rate"],
            "JOURNEY_WEIGHTS": self.cfg.journey_weights(),
            "AVG_REQUESTS_PER_SESSION": per_iter,
        }
        proc_env = dict(os.environ)
        for key, var in (("k6_gogc", "GOGC"), ("k6_gomemlimit", "GOMEMLIMIT")):
            if self.cfg.get("loadgen", key):
                proc_env[var] = str(self.cfg.get("loadgen", key))
        if n > 1 and "GOMEMLIMIT" in proc_env:
            proc_env["GOMEMLIMIT"] = split_memlimit(proc_env["GOMEMLIMIT"], n)
        prom = self.prom.available()
        if prom:
            proc_env.update({
                "K6_PROMETHEUS_RW_SERVER_URL": self.cluster.prometheus_url + "/api/v1/write",
                "K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM": "true",
                "K6_PROMETHEUS_RW_PUSH_INTERVAL": "1s",
            })

        cpuset = self.cfg.get("loadgen", "k6_cpuset")
        paths = [summary_path] if n == 1 else [
            summary_path.with_name(summary_path.name.replace(".summary.json", f".p{i}.summary.json"))
            for i in range(n)]
        procs = []
        for i, path in enumerate(paths):
            argv = []
            # Every process gets the whole k6 cpuset: a process pinned to its own 1-2 cores couldn't
            # absorb bursts and dropped iterations with most of the loadgen idle (k6-split-2026-10).
            if cpuset and not self.cluster.local:
                argv += ["taskset", "-c", cpuset]
            argv += ["k6", "run", "--quiet", "--no-color", "--no-usage-report"]
            port = None
            if n > 1:
                port = free_port()
                argv += ["--paused", "--address", f"127.0.0.1:{port}"]
            for k, v in (tags or {}).items():
                argv += ["--tag", f"{k}={v}"]
            if n > 1:
                argv += ["--tag", f"k6proc={i}"]  # keeps the processes' Prometheus series apart
            for k, v in {**env, "SUMMARY_FILE": str(path)}.items():
                argv += ["-e", f"{k}={v}"]
            if prom:
                argv += ["--out", "experimental-prometheus-rw"]
            argv.append(str(REPO / "k6" / "tests" / (script or TEST_SCRIPTS[test])))
            procs.append((argv, port))

        summary_path.parent.mkdir(parents=True, exist_ok=True)
        length = duration_s if mode == "warmup" else settle_s + measure_s
        results = self._run(procs, proc_env, length + 120)
        parts = []
        for (code, err), path in zip(results, paths):
            # 0 = thresholds passed, 99 = a threshold failed (the runner judges the numbers itself).
            if code not in (0, 99) or not path.exists():
                raise K6Error(f"k6 exited {code}: {err.strip()[-2000:]}")
            parts.append(parse_summary(path, measure_s, mode))
        if n == 1:
            return parts[0]
        merged = merge_numbers(parts, measure_s, mode)
        merged_path = summary_path.with_name(summary_path.name.replace(".summary.json", ".merged.json"))
        merged_path.write_text(json.dumps(merged, indent=1))
        return merged

    def _run(self, procs, env, timeout):
        """Starts every process; split runs start paused and are resumed together once all have
        initialised their VUs. Returns [(returncode, stderr tail)] in order."""
        if self.spot:
            self.spot.check()
        running = []
        try:
            for argv, port in procs:
                log = tempfile.TemporaryFile(mode="w+")  # a file, not a pipe: a full pipe would block k6
                p = subprocess.Popen(argv, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
                running.append((p, log, port))
            if len(running) > 1:
                self._start_together(running)
            deadline = time.time() + timeout
            while any(p.poll() is None for p, _, _ in running):
                if self.spot and self.spot.tripped.is_set():
                    raise SpotInterruption(self.spot.host, self.spot.reason)
                if time.time() > deadline:
                    raise K6Error(f"k6 still running after {timeout} s")
                time.sleep(0.5)
            out = []
            for p, log, _ in running:
                log.seek(0)
                out.append((p.returncode, log.read()))
            return out
        finally:
            for p, log, _ in running:
                if p.poll() is None:
                    p.kill()
                    p.wait()
                log.close()

    def _start_together(self, running):
        deadline = time.time() + READY_TIMEOUT_S
        for p, log, port in running:
            while _status(port) != READY_STATUS:
                if p.poll() is not None:
                    log.seek(0)
                    raise K6Error(f"k6 exited {p.returncode} before starting: {log.read().strip()[-2000:]}")
                if time.time() > deadline:
                    raise K6Error(f"k6 not ready after {READY_TIMEOUT_S} s")
                time.sleep(0.05)
        body = json.dumps({"data": {"type": "status", "id": "default", "attributes": {"paused": False}}}).encode()
        for _, _, port in running:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/status", data=body, method="PATCH",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()


def _status(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/status", timeout=1) as r:
            return json.loads(r.read())["data"]["attributes"]["status"]
    except (OSError, ValueError, KeyError):
        return None


def merge_numbers(parts, measure_s, mode):
    """Combines the parse_summary() numbers of the processes of one split step.

    Counts add up. Percentiles can't be added or averaged, but a percentile of the combined
    requests always lies between the processes' own values (the combined distribution is a
    mixture of theirs), and every process sends the same request mix at the same rate. The
    merged p50/p95/p99 is the highest process value: an upper bound, never better than the
    truth. `p99_spread_ms` keeps the lowest and highest p99 to show how tight the bound is."""
    requests = sum(p["requests"] for p in parts)

    def worst(values):
        values = [v for v in values if v is not None]
        return max(values) if values else None

    def weighted(values):
        if not requests:
            return 0.0
        return sum((v or 0.0) * p["requests"] for v, p in zip(values, parts)) / requests

    endpoints = {}
    for name in sorted({name for p in parts for name in p["endpoints"]}):
        eps = [p["endpoints"].get(name, {}) for p in parts]
        endpoints[name] = {"p99_ms": worst(e.get("p99_ms") for e in eps),
                           "error_rate": weighted([e.get("error_rate") for e in eps])}
    p99s = [p["p99_ms"] for p in parts if p["p99_ms"] is not None]
    return {
        "mode": mode,
        "requests": requests,
        "iterations": sum(p["iterations"] for p in parts),
        "measured_rps": round(requests / measure_s, 2) if measure_s else None,
        "p50_ms": worst(p["p50_ms"] for p in parts),
        "p95_ms": worst(p["p95_ms"] for p in parts),
        "p99_ms": worst(p99s),
        "max_ms": worst(p["max_ms"] for p in parts),
        "error_rate": weighted([p["error_rate"] for p in parts]),
        "dropped_iterations": sum(p["dropped_iterations"] for p in parts),
        "settle_tail_p99_ms": worst(p["settle_tail_p99_ms"] for p in parts),
        "checks_failed": sum(p["checks_failed"] for p in parts),
        "endpoints": endpoints,
        "k6_processes": len(parts),
        "p99_spread_ms": [min(p99s), max(p99s)] if p99s else None,
    }


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

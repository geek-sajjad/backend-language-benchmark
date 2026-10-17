#!/usr/bin/env python3
"""k6 split check on the campaign lab: does running a step as N k6 processes (runner/benchlib/k6.py)
remove the ~20k req/s single-process limit, and does it measure the same latency as one process?

Run on loadgen from ~/bench after deploy.sh (echo server = the downstream mock on the app host,
Prometheus output on, as in the campaign):  python3 infra/k6-sizing/remote/split.py [out_dir]
Each case is one full step (15 s settle + 30 s measure). Results: <out_dir>/split.jsonl."""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "runner"))
from benchlib.campaign import Context  # noqa: E402
from benchlib.cluster import Cluster  # noqa: E402
from benchlib.config import Config  # noqa: E402

# (label, req/s, k6_max_rate_per_process): 1e9 forces a single process. Every process runs on the
# whole k6_cpuset (the first attempt pinned each process to its own cores: see summary.md).
CASES = [
    ("single-15k", 15000, 10**9),
    ("split-15k", 15000, 7500),
    ("single-25k", 25000, 10**9),
    ("split-25k", 25000, 12000),
    ("split-40k", 40000, 12000),
    ("split-55k", 55000, 12000),
]

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "results/sizing/k6-split-2026-10")
out.mkdir(parents=True, exist_ok=True)
ctx = Context(Config(), Cluster(None))
c = ctx.cluster
c.app.sh("docker rm -f echo >/dev/null 2>&1; docker run -d --name echo --network host "
         "-e LISTEN_ADDR=:8080 -e DELAY_MS=0 bench-downstream:local >/dev/null")
try:
    time.sleep(2)
    for label, rate, per in CASES:
        ctx.cfg.data["loadgen"]["k6_max_rate_per_process"] = per
        t0 = time.time()
        n = ctx.k6.run(0, out / f"{label}.summary.json", rate=rate, settle_s=15, tail_s=5, measure_s=30,
                       script="00-echo.js", tags={"testid": f"k6-split/{label}"})
        time.sleep(3)  # let the last scrapes land
        w = ctx.prom.window(t0 + 45, 30)
        row = {"label": label, "rate": rate, "processes": n.get("k6_processes", 1),
               "measured_rps": n["measured_rps"], "p50_ms": n["p50_ms"], "p99_ms": n["p99_ms"],
               "p99_spread_ms": n.get("p99_spread_ms"), "dropped": n["dropped_iterations"],
               "errors": n["error_rate"], "loadgen_cpu": w["loadgen_cpu"], "app_host_cpu": w["app_host_cpu"]}
        print(json.dumps(row), flush=True)
        with open(out / "split.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
finally:
    c.app.sh("docker rm -f echo >/dev/null 2>&1", check=False)

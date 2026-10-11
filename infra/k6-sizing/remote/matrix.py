#!/usr/bin/env python3
"""Runs on the k6-sizing loadgen (Graviton). For each variant, finds the highest rate k6 sustains
against the echo server with no dropped iterations, and records k6's CPU per request.

Usage: matrix.py <echo-base-url> <out-dir>
Variants (cores = taskset list for k6; core 3 hosts Prometheus in the remote-write variant):
  base-c1..c4   current k6/ scripts on 1-4 cores
  old-c3        k6/ before the helper optimizations (commit 66d9345)
  gogc200/400   current scripts with Go's GC target raised (k6 is a Go program)
  prealloc-c3   preAllocatedVUs = rate x 0.5 s instead of rate x 0.1 s
  rw-c3         current scripts + Prometheus remote-write, Prometheus pinned to core 3
"""
import json
import os
import subprocess
import sys
import time

BASE_URL, OUT = sys.argv[1], sys.argv[2]
HOME = os.path.expanduser("~/bench")
TCK = os.sysconf("SC_CLK_TCK")
SETTLE_S, MEASURE_S = 10, 15
RUN_S = SETTLE_S + MEASURE_S
os.makedirs(OUT, exist_ok=True)
RESULTS = open(os.path.join(OUT, "runs.jsonl"), "a")


def proc_cpu(pid):
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / TCK


def host_busy():
    f = open("/proc/stat").readline().split()[1:]
    v = list(map(int, f))
    idle = v[3] + v[4]
    return sum(v) - idle, sum(v)


def run(variant, k6dir, cores, rate, env_extra=None, rw=False):
    summ = os.path.join(OUT, f"{variant}-{rate}.summary.json")
    argv = ["taskset", "-c", cores, "k6", "run", "--quiet", "--no-color", "--no-usage-report",
            "--summary-export", summ,
            "-e", f"RATE={rate}", "-e", f"SETTLE_S={SETTLE_S}", "-e", f"MEASURE_S={MEASURE_S}",
            "-e", f"BASE_URL={BASE_URL}"]
    env = dict(os.environ, **(env_extra or {}))
    if rw:
        argv += ["--out", "experimental-prometheus-rw"]
        env.update(K6_PROMETHEUS_RW_SERVER_URL="http://127.0.0.1:9090/api/v1/write",
                   K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM="true", K6_PROMETHEUS_RW_PUSH_INTERVAL="1s")
    argv.append(f"{HOME}/{k6dir}/tests/00-echo.js")
    start = time.time()
    p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # Steady-state window: after k6 init and the start of the settle phase, before the end.
    time.sleep(10)
    c0, h0 = proc_cpu(p.pid), host_busy()
    time.sleep(12)
    c1, h1 = proc_cpu(p.pid), host_busy()
    out, _ = p.communicate()
    m = json.load(open(summ))["metrics"]
    g = lambda k, f: (m.get(k) or {}).get(f, 0) or 0
    ncores = len(expand(cores))
    rec = {
        "variant": variant, "cores": ncores, "rate": rate, "start": start, "end": time.time(),
        "requests": g("http_reqs", "count"), "achieved": round(g("http_reqs", "count") / RUN_S, 1),
        "dropped": g("dropped_iterations", "count"), "p99_ms": round(g("http_req_duration", "p(99)"), 2),
        "p50_ms": round(g("http_req_duration", "med"), 3), "failed": g("http_req_failed", "value"),
        "vus_max": g("vus_max", "value"),
        "k6_cores_busy": round((c1 - c0) / 12, 3),
        "k6_cpu_frac": round((c1 - c0) / 12 / ncores, 3),
        "k6_us_per_req": round((c1 - c0) / (12 * rate) * 1e6, 1),
        "host_cpu_frac": round((h1[0] - h0[0]) / max(1, h1[1] - h0[1]), 3),
        "exit": p.returncode,
    }
    rec["ok"] = (rec["dropped"] == 0 and rec["p99_ms"] < 100 and rec["failed"] < 0.001
                 and rec["achieved"] >= 0.98 * rate)
    RESULTS.write(json.dumps(rec) + "\n")
    RESULTS.flush()
    print(f"{variant:12s} cores {ncores} rate {rate:6d} achieved {rec['achieved']:8.0f} dropped {rec['dropped']:6.0f} "
          f"p99 {rec['p99_ms']:7.2f} k6 {rec['k6_cpu_frac']:5.0%} of its cores, {rec['k6_us_per_req']:6.1f} us/req "
          f"host {rec['host_cpu_frac']:4.0%} vus {rec['vus_max']:.0f} {'OK' if rec['ok'] else 'X'}", flush=True)
    if p.returncode not in (0, 99):
        print(out[-1500:], flush=True)
    return rec


def expand(cores):
    out = []
    for part in cores.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def search(variant, k6dir, cores, start, env_extra=None, rw=False):
    """Up x1.2 while OK; then one bisection step between the last OK and the first failure."""
    rate, best, fail = int(start), None, None
    for _ in range(8):
        r = run(variant, k6dir, cores, rate, env_extra, rw)
        if r["ok"]:
            best = rate
            rate = int(rate * 1.2)
        else:
            fail = rate
            break
    if best is None and fail is not None:  # started too high: step down
        rate = int(fail * 0.75)
        for _ in range(4):
            if run(variant, k6dir, cores, rate, env_extra, rw)["ok"]:
                best = rate
                break
            fail, rate = rate, int(rate * 0.75)
    if best and fail:
        mid = int((best + fail) / 2)
        if run(variant, k6dir, cores, mid, env_extra, rw)["ok"]:
            best = mid
    print(f"== {variant}: max sustained {best} req/s", flush=True)
    with open(os.path.join(OUT, "max.jsonl"), "a") as f:
        f.write(json.dumps({"variant": variant, "cores": len(expand(cores)), "max_rate": best}) + "\n")
    return best or 1000


per_core = search("base-c1", "k6", "0", 3000) or 3000
for n, cores in ((2, "0-1"), (3, "0-2"), (4, "0-3")):
    search(f"base-c{n}", "k6", cores, 0.8 * n * per_core)
c3 = 0.8 * 3 * per_core
search("old-c3", "k6-old", "0-2", 0.7 * c3)
search("gogc200-c3", "k6", "0-2", c3, {"GOGC": "200"})
search("gogc400-c3", "k6", "0-2", c3, {"GOGC": "400"})
search("prealloc-c3", "k6-prealloc", "0-2", c3)
subprocess.run("docker rm -f prom >/dev/null 2>&1; docker run -d --name prom --network host --cpuset-cpus 3 "
               "prom/prometheus:v3.15.0 --config.file=/etc/prometheus/prometheus.yml "
               "--web.enable-remote-write-receiver >/dev/null && sleep 5", shell=True, check=True)
search("rw-c3", "k6", "0-2", c3, rw=True)
subprocess.run("docker rm -f prom >/dev/null 2>&1", shell=True)
open(os.path.join(OUT, "done"), "w").write(str(time.time()))

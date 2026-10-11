#!/usr/bin/env python3
"""Follow-up to matrix.py: are k6's early dropped iterations caused by Go GC?
Fixed rates on 3 cores, each variant repeated 3 times in interleaved order, with GODEBUG=gctrace=1
so every GC cycle is counted (k6 is a Go program; each VU holds its own JS runtime on the heap).

Usage: repeat.py <echo-base-url> <out-dir>
"""
import json
import os
import re
import subprocess
import sys
import time

BASE_URL, OUT = sys.argv[1], sys.argv[2]
HOME = os.path.expanduser("~/bench")
TCK = os.sysconf("SC_CLK_TCK")
SETTLE_S, MEASURE_S = 10, 15
RUN_S = SETTLE_S + MEASURE_S
CORES = "0-2"
VARIANTS = {
    "gogc100": {},                                   # Go default
    "gogc200": {"GOGC": "200"},
    "gogc-off": {"GOGC": "off", "GOMEMLIMIT": "5GiB"},  # GC only near the memory limit (8 GB host)
}
RATES = [9000, 11000]
REPS = 3
os.makedirs(OUT, exist_ok=True)
RESULTS = open(os.path.join(OUT, "repeat.jsonl"), "a")
GC_LINE = re.compile(r"^gc \d+ @([\d.]+)s (\d+)%: ([\d.]+)\+([\d.]+)\+([\d.]+) ms clock")


def proc_cpu(pid):
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / TCK


def run(variant, rate, rep):
    summ = os.path.join(OUT, f"{variant}-{rate}-{rep}.summary.json")
    argv = ["taskset", "-c", CORES, "k6", "run", "--quiet", "--no-color", "--no-usage-report",
            "--summary-export", summ, "-e", f"RATE={rate}", "-e", f"SETTLE_S={SETTLE_S}",
            "-e", f"MEASURE_S={MEASURE_S}", "-e", f"BASE_URL={BASE_URL}", f"{HOME}/k6/tests/00-echo.js"]
    env = dict(os.environ, GODEBUG="gctrace=1", **VARIANTS[variant])
    p = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    time.sleep(10)
    c0 = proc_cpu(p.pid)
    time.sleep(12)
    c1 = proc_cpu(p.pid)
    _, err = p.communicate()
    gcs = [GC_LINE.match(line) for line in err.splitlines()]
    gcs = [g for g in gcs if g]
    m = json.load(open(summ))["metrics"]
    g = lambda k, f: (m.get(k) or {}).get(f, 0) or 0
    stw = [float(x.group(3)) + float(x.group(5)) for x in gcs]  # the two stop-the-world phases
    rec = {
        "variant": variant, "rate": rate, "rep": rep,
        "achieved": round(g("http_reqs", "count") / RUN_S, 1), "dropped": g("dropped_iterations", "count"),
        "p99_ms": round(g("http_req_duration", "p(99)"), 2), "vus_max": g("vus_max", "value"),
        "k6_cpu_frac": round((c1 - c0) / 12 / 3, 3), "k6_us_per_req": round((c1 - c0) / (12 * rate) * 1e6, 1),
        "gc_cycles": len(gcs), "gc_cpu_pct_last": int(gcs[-1].group(2)) if gcs else 0,
        "gc_stw_max_ms": round(max(stw), 3) if stw else 0,
        "gc_concurrent_ms_total": round(sum(float(x.group(4)) for x in gcs), 1),
    }
    RESULTS.write(json.dumps(rec) + "\n")
    RESULTS.flush()
    print(f"{variant:9s} rate {rate:6d} rep {rep} achieved {rec['achieved']:8.0f} dropped {rec['dropped']:6.0f} "
          f"p99 {rec['p99_ms']:7.2f} k6 {rec['k6_cpu_frac']:4.0%} {rec['k6_us_per_req']:6.1f} us/req "
          f"gc {rec['gc_cycles']:4d} cycles ({rec['gc_cpu_pct_last']}% cpu, stw max {rec['gc_stw_max_ms']} ms)", flush=True)
    os.remove(summ)


for rep in range(1, REPS + 1):
    for rate in RATES:
        for variant in VARIANTS:
            run(variant, rate, rep)
open(os.path.join(OUT, "done"), "w").write(str(time.time()))

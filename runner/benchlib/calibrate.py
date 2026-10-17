"""`calibrate`: prove the DB, the load generator and the downstream mock have headroom
(04 "Proving Postgres is not the bottleneck", "Proving the load generator is not the bottleneck")."""
import re
import time

from .cluster import Database, Stack
from .util import REPO, atomic_write_json, log, utc_now

PGBENCH_IMAGE = "postgres:18.6"


def pgbench(ctx, test, seconds=30, clients=64):
    c = ctx.cluster
    script = REPO / "runner" / "pgbench" / f"test{test}.sql"
    cmd = (f"docker run --rm --network host -e PGPASSWORD=bench -v {script}:/s.sql:ro {PGBENCH_IMAGE} "
           f"pgbench -h {c.db.ip} -U bench -d bench -n -M prepared -c {clients} -j 8 -T {seconds} -f /s.sql")
    start = time.time()
    out = c.loadgen.sh(cmd, timeout=seconds + 120)
    m = re.search(r"tps = ([\d.]+)", out)
    db_cpu = ctx.prom.window(start + seconds, seconds - 5)["db_cpu"]
    return {"tps": float(m.group(1)) if m else None, "clients": clients, "seconds": seconds, "db_cpu": db_cpu}


def ramp(ctx, label, base_url, start_rate, max_rate, out_dir):
    """Doubling stages, then ×1.2, until the SLO breaks, iterations are dropped or the loadgen
    CPU reaches the validity limit. Returns the highest clean measured rate. Every stage is a
    full-length step (settle_s + measure_s from [search]): 10 s stages counted k6's start-up
    hiccups as drops and put the ceiling at 46k where real steps were clean at 55k+."""
    limit = ctx.cfg["validity"]["max_loadgen_cpu"]
    settle, measure = ctx.cfg["search"]["settle_s"], ctx.cfg["search"]["measure_s"]
    rate, factor, best, stages = start_rate, 2.0, None, []
    while rate <= max_rate:
        t0 = time.time()
        n = ctx.k6.run(0, out_dir / f"{label}-{int(rate)}.summary.json", rate=rate, settle_s=settle,
                       measure_s=measure, base_url=base_url, script="00-echo.js",
                       tags={"testid": f"calibrate/{label}/{int(rate)}"})
        cpu = ctx.prom.window(t0 + settle + measure, measure)["loadgen_cpu"]
        slo = ctx.cfg["slo"]
        ok = (n["dropped_iterations"] == 0 and n["error_rate"] < slo["error_rate"]
              and n["p99_ms"] is not None and n["p99_ms"] < slo["p99_ms"] and (cpu is None or cpu < limit))
        stages.append({"rate": rate, "ok": ok, "measured_rps": n["measured_rps"], "p99_ms": n["p99_ms"],
                       "dropped": n["dropped_iterations"], "loadgen_cpu": cpu})
        log(f"  {label} {rate:>8.0f}/s → {'ok' if ok else 'limit'} (p99 {n['p99_ms']}, dropped {n['dropped_iterations']}, loadgen cpu {cpu})")
        if not ok:
            if factor == 2.0 and best:
                factor, rate = 1.2, best * 1.2
                continue
            break
        best = n["measured_rps"]
        rate *= factor
    return {"ceiling_rps": best, "stages": stages}


def calibrate(ctx):
    c, db = ctx.cluster, Database(ctx.cluster)
    out = REPO / "results" / "calibration"
    result = {"created": utc_now().isoformat(), "mode": c.mode}

    log("DB ceiling (pgbench, 64 clients, prepared statements)")
    db.reset()
    result["db_ceiling"] = {}
    for test in (2, 3, 4, 5):
        r = pgbench(ctx, test)
        result["db_ceiling"][str(test)] = r
        log(f"  test {test}: {r['tps']} req/s, db cpu {r['db_cpu']}")
    db.reset()  # test 5 wrote orders

    log("load generator ceiling (echo server on the app host)")
    for name in ctx.cfg.stacks:
        Stack(c, name).down()
    c.app.sh("docker build -q -t bench-downstream:local downstream >/dev/null")
    c.app.sh("docker rm -f echo >/dev/null 2>&1; docker run -d --name echo --network host "
             "-e LISTEN_ADDR=:8080 -e DELAY_MS=0 bench-downstream:local >/dev/null")
    try:
        time.sleep(1)
        result["k6_ceiling"] = ramp(ctx, "echo", c.base_url, 1000, ctx.cfg["search"]["probe"]["max_rate"], out)
    finally:
        c.app.sh("docker rm -f echo >/dev/null 2>&1", check=False)

    log("downstream mock ceiling")
    result["downstream_ceiling"] = ramp(ctx, "downstream", f"http://{c.db.ip}:9000", 1000, 200_000, out)

    atomic_write_json(REPO / "results" / "calibration.json", result)
    log(f"wrote {REPO / 'results' / 'calibration.json'}")
    return result

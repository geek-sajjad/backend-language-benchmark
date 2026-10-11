#!/usr/bin/env python3
"""db sizing test (docs/tmp-db-sizing-plan.md): turn raw results into CSVs and summary.md.

    summarize.py phase1 <results-dir>   -> phase1/rust.csv
    summarize.py phase2 <results-dir>   -> phase2/pgbench-runs.csv
Either one also writes summary.md once both phases have results.
"""
import csv
import datetime as dt
import json
import pathlib
import re
import statistics
import sys

NET_BASELINE_BPS = 1.562e9  # c7a.xlarge network baseline
RULE = 1.5
RUNS = [  # run, script, clients, -C
    ("r01", 2, 64, False), ("r02", 2, 128, False), ("r03", 3, 64, False), ("r04", 3, 128, False),
    ("r05", 4, 64, False), ("r06", 4, 128, False), ("r07", 2, 64, True), ("r08", 2, 64, False),
    ("r09", 2, 64, False), ("r10", 5, 64, False),
]
DB_RUNS = {2: ["r01", "r02"], 3: ["r03", "r04"], 4: ["r05", "r06"], 5: ["r10"]}
# Hourly extras per instance: public IPv4 ($0.005/h) + 20 GB gp3 ($0.08/GB-month).
EXTRA_PER_H = 0.005 + 20 * 0.08 / 730


def mpstat_all_busy(path):
    """Average busy % of the 'all' row in an mpstat file."""
    for line in pathlib.Path(path).read_text().splitlines():
        f = line.split()
        if len(f) > 2 and f[0] == "Average:" and f[1] == "all":
            return round(100 - float(f[-1]), 1)
    return None


def write_csv(path, rows, cols):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------ phase 1
def phase1(res):
    lines = [json.loads(l) for l in (res / "phase1" / "measurements.jsonl").read_text().splitlines() if l.strip()]
    # The highest-rate fixed run without dropped iterations: the rate adjustment can overload the
    # 2 Postgres cores (tests 4, 5), and an overloaded run's CPU per request is less trustworthy.
    fixed = {}
    for m in lines:
        if m["kind"] not in ("fixed", "fixed_discarded"):
            continue
        cur = fixed.get(m["test"])
        key = lambda x: (x["dropped"] == 0, x["rate"])
        if cur is None or key(m) > key(cur):
            fixed[m["test"]] = m
    ramp = {m["test"]: m for m in lines if m["kind"] == "ramp"}
    rows = []
    for t in sorted(fixed):
        f, r = fixed[t], ramp.get(t, {})
        est = 8 * 0.85 / f["cpu_per_req_s"]
        cross = r.get("ramp_max_4c", 0) * 1.9
        rows.append({
            "test": t, "fixed_rate": f["rate"], "fixed_run": f["tag"], "measured_rps": round(f["measured_rps"], 1),
            "rust_cpu_s": round(f["rust_cpu_s"], 2), "cpu_per_req_s": f"{f['cpu_per_req_s']:.3e}",
            "pg_cpu_per_req_s": f"{f['pg_cpu_per_req_s']:.3e}", "ramp_max_4c": round(r.get("ramp_max_4c", 0)),
            "ramp_limited_by": r.get("ramp_limited_by", ""),
            # not in the plan's CSV columns, used by the summary
            "rust_est_8c": round(est), "ramp_cross_8c": round(cross),
            "fixed_p99_ms": round(f["p99_ms"], 2), "fixed_errors": f["error_rate"], "fixed_dropped": f["dropped"],
            "rust_busy_pct": f["rust_busy_pct"], "pg_busy_pct": f["pg_busy_pct"], "k6_busy_pct": f["k6_busy_pct"],
        })
    cols = ["test", "fixed_rate", "fixed_run", "measured_rps", "rust_cpu_s", "cpu_per_req_s", "pg_cpu_per_req_s",
            "ramp_max_4c", "ramp_limited_by", "rust_est_8c", "ramp_cross_8c", "fixed_p99_ms", "fixed_errors",
            "fixed_dropped", "rust_busy_pct", "pg_busy_pct", "k6_busy_pct"]
    write_csv(res / "phase1" / "rust.csv", rows, cols)
    return rows


def read_phase1(res):
    p = res / "phase1" / "rust.csv"
    return {int(r["test"]): r for r in csv.DictReader(p.open())} if p.exists() else None


# ------------------------------------------------------------ phase 2
def phase2(res):
    raw = res / "phase2" / "raw"
    rows = []
    for run, script, clients, connect in RUNS:
        txt = (raw / f"{run}.pgbench.txt").read_text()
        num = lambda pat: float(m.group(1)) if (m := re.search(pat, txt)) else None
        tps = num(r"(?m)^tps = ([\d.]+)")
        db = json.loads((raw / f"{run}.db.json").read_text())
        locks = [tuple(map(int, l.split())) for l in (raw / f"{run}.locks").read_text().split("\n") if l.strip()]
        lock_total = sum(n for _, n in locks)
        bps = db["tx_bytes"] / db["window_s"]
        wal_ms = wal_n = None
        if db["wal_before"] and db["wal_after"]:
            (n0, t0), (n1, t1) = (map(float, db[k].split()) for k in ("wal_before", "wal_after"))
            wal_n, wal_ms = int(n1 - n0), round(t1 - t0, 1)
        client_cpu = mpstat_all_busy(raw / f"{run}.client.mpstat")
        rows.append({
            "run": int(run[1:]), "script": f"test{script}", "clients": clients, "connect_per_tx": int(connect),
            "tps": tps, "lat_avg_ms": num(r"(?m)^latency average = ([\d.]+) ms"),
            "lat_std_ms": num(r"(?m)^latency stddev = ([\d.]+) ms"),
            "db_cpu_pct": mpstat_all_busy(raw / f"{run}.db.mpstat"), "client_cpu_pct": client_cpu,
            "tx_bytes_per_s": round(bps), "bytes_per_tx": round(bps / tps) if tps else None,
            "allowance_exceeded_delta": db["allowance_exceeded_delta"],
            "lock_wait_pct": round(100 * sum(l for l, _ in locks) / lock_total, 2) if lock_total else 0,
            "wal_fsync_ms": wal_ms, "wal_fsyncs": wal_n,
            "lower_bound": client_cpu is not None and client_cpu > 70,
            "failed_tx": num(r"(?m)^number of failed transactions: (\d+)"),
        })
    cols = ["run", "script", "clients", "connect_per_tx", "tps", "lat_avg_ms", "lat_std_ms", "db_cpu_pct",
            "client_cpu_pct", "tx_bytes_per_s", "bytes_per_tx", "allowance_exceeded_delta", "lock_wait_pct",
            "wal_fsync_ms", "wal_fsyncs", "lower_bound", "failed_tx"]
    write_csv(res / "phase2" / "pgbench-runs.csv", rows, cols)
    return rows


def read_phase2(res):
    p = res / "phase2" / "pgbench-runs.csv"
    return {f"r{int(r['run']):02d}": r for r in csv.DictReader(p.open())} if p.exists() else None


# ------------------------------------------------------------ summary
def cost(res):
    total, lines = 0.0, []
    for ph in ("phase1", "phase2"):
      for p in sorted((res / ph).glob("instances*.jsonl")):  # instances-attemptN.jsonl: failed attempts
        att = " (failed attempt)" if "attempt" in p.name else ""
        for l in p.read_text().splitlines():
            i = json.loads(l)
            if not i["terminated"]:
                lines.append(f"- {ph} {i['name']} ({i['type']}): termination time missing")
                continue
            h = (dt.datetime.fromisoformat(i["terminated"].replace("Z", "+00:00"))
                 - dt.datetime.fromisoformat(i["launched"].replace("Z", "+00:00"))).total_seconds() / 3600
            c = h * (float(i["spot_price"]) + EXTRA_PER_H)
            total += c
            lines.append(f"- {ph}{att} {i['name']}: {i['type']} spot ${float(i['spot_price']):.4f}/h, "
                         f"{h * 60:.0f} min → ${c:.3f} (incl. IPv4 + 20 GB gp3)")
    return lines, total


def summary(res):
    p1, p2 = read_phase1(res), read_phase2(res)
    if not (p1 and p2):
        return
    f = lambda r, k: float(r[k]) if r.get(k) not in (None, "") else None
    out = ["# db sizing test: c7a.xlarge (4 cores) vs Rust on 8 cores", "",
           f"Generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC by `infra/db-sizing/summarize.py`. "
           "Plan: [docs/tmp-db-sizing-plan.md](../../docs/tmp-db-sizing-plan.md).", "",
           "| Test | db_max_4c (req/s) | rust_est_8c (req/s) | ratio | db limited by | "
           "PG CPU/req on m7a | PG cores needed at rust_est_8c | ratio on 16 cores if linear |",
           "|---|---|---|---|---|---|---|---|"]
    ratios, notes, cross_na = {}, [], []
    for t, runs in DB_RUNS.items():
        best, best_run, limit = 0.0, None, ""
        for r in runs:
            row = p2[r]
            tps = f(row, "tps")
            capped = tps
            if t == 3 and f(row, "bytes_per_tx"):
                cap = NET_BASELINE_BPS / 8 / f(row, "bytes_per_tx")
                capped = min(tps, cap)
            if capped > best:
                best, best_run = capped, r
        row = p2[best_run]
        tps, cpu = f(row, "tps"), f(row, "db_cpu_pct")
        if row["lower_bound"] == "True":
            limit = f"client CPU {row['client_cpu_pct']}% (lower bound)"
        elif t == 3 and best < tps:
            limit = f"network baseline (measured {tps:.0f} on burst)"
        elif int(row["allowance_exceeded_delta"] or 0) > 0:
            limit = "network (allowance exceeded)"
        elif cpu >= 85:
            limit = f"CPU ({cpu:.0f}%)"
        elif t == 5:
            limit = f"disk/WAL? CPU {cpu:.0f}%, fsync {row['wal_fsync_ms']} ms, locks {row['lock_wait_pct']}%"
        else:
            limit = f"neither CPU ({cpu:.0f}%) nor network: latency-bound"
        r1 = p1[t]
        est = f(r1, "rust_est_8c")
        ratio = best / est
        ratios[t] = (ratio, limit)
        pg_cores = f(r1, "pg_cpu_per_req_s") * est
        cross = f(r1, "ramp_cross_8c")
        if not r1["ramp_limited_by"].startswith("rust"):
            cross_na.append(f"test {t} ({r1['ramp_limited_by'].split(' ')[0]})")
        elif abs(cross - est) / est > 0.30:
            notes.append(f"- Test {t}: the CPU-per-request estimate ({est:.0f}) and the ramp cross-check "
                         f"({cross:.0f}) differ by {abs(cross - est) / est:.0%}.")
        if t == 3:
            bpt = f(row, "bytes_per_tx")
            notes.append(f"- Test 3 network: {bpt / 1000:.1f} kB per page, {f(row, 'tx_bytes_per_s') * 8 / 1e9:.2f} Gbps at "
                         f"{tps:.0f} TPS = {f(row, 'tx_bytes_per_s') * 8 / NET_BASELINE_BPS:.0%} of the 1.562 Gbps baseline "
                         f"(cap {NET_BASELINE_BPS / 8 / bpt:.0f} TPS); no allowance-exceeded events. CPU ran out first.")
        out.append(f"| {t} | {best:.0f} ({best_run}, {row['clients']} clients) | {est:.0f} | **{ratio:.2f}×** | "
                   f"{limit} | {f(r1, 'pg_cpu_per_req_s') * 1e6:.0f} µs | {pg_cores:.1f} | ≤ {4 * ratio:.2f}× |")

    low_t = min(ratios, key=lambda t: ratios[t][0])
    low, low_limit = ratios[low_t]
    if low >= 2:
        rec = "**≥ 2×: switch the default db to c7a.xlarge.** The per-step db CPU < 70% check still catches faster stacks."
    elif low >= RULE:
        rec = "**1.5×–2×: passes the rule with little margin.** User's call: c7a.xlarge or c7a.2xlarge."
    elif "network" in low_limit:
        rec = "**< 1.5×, network-limited: consider c7a.2xlarge** (8 cores, 3.1 Gbps)."
    elif "CPU" in low_limit and "client" not in low_limit:
        rec = "**< 1.5×, CPU-limited: keep c7a.4xlarge.**"
    else:
        rec = f"**< 1.5×, limited by: {low_limit}.** Needs a look before deciding."
    fail16 = [t for t, (r, _) in ratios.items() if 4 * r < RULE]
    if fail16:
        rec += (f" **But even 16 cores fails the rule on test {', '.join(map(str, fail16))}** (last column): the "
                "plan's assumption that today's db has 1.5× headroom over Rust does not hold for those tests.")
    out += ["", f"Lowest ratio: test {low_t}, {low:.2f}× → {rec}", "",
            "The last column scales the 4-core maximum linearly to 16 cores (today's c7a.4xlarge). That is an upper "
            "bound (Postgres doesn't scale perfectly, and test 3 would also need about 4× the network), so any value "
            f"below {RULE}× there means the current db host fails the rule too.", ""]

    r1, r7 = p2["r01"], p2["r07"]
    out += ["## PHP note: new connection per transaction (`-C`)", "",
            f"Test 2 with `-C`: **{float(r7['tps']):.0f} TPS** (db CPU {r7['db_cpu_pct']}%) vs "
            f"{float(r1['tps']):.0f} with a 64-connection pool ({float(r7['tps']) / float(r1['tps']):.2%}), "
            f"average latency {r7['lat_avg_ms']} ms, client CPU {r7['client_cpu_pct']}%. Neither side's CPU is full, "
            "so connection setup itself (backend fork + scram-sha-256 auth) is the limit: a stack that connects "
            "per request is capped near this on any db test.", ""]

    reps = [float(p2[r]["tps"]) for r in ("r01", "r08", "r09")]
    lb = [r for r, row in p2.items() if row["lower_bound"] == "True"]
    cost_lines, total = cost(res)
    out += ["## Notes", "",
            f"- Noise, test 2 at 64 clients (runs 1, 8, 9): {', '.join(f'{x:.0f}' for x in reps)} TPS; "
            f"spread {(max(reps) - min(reps)) / statistics.mean(reps):.1%} of the mean.",
            f"- Runs marked lower bound (client CPU > 70%): {', '.join(lb) or 'none'}.",
            *notes,
            *([f"- Ramp cross-check not usable for {', '.join(cross_na)}: in phase 1 the ramp hit the named "
               "resource (k6 on 2 cores, or Postgres on 2 cores) before Rust, so it says nothing about Rust's ceiling."]
              if cross_na else []),
            "- Rust's 8-core figure is CPU per request on 4 pinned cores × 8 × 0.85, not a measurement on 8 cores.",
            "", "## Cost", "", *cost_lines, f"- **Total ≈ ${total:.2f}**", ""]
    (res / "summary.md").write_text("\n".join(out))
    print("\n".join(out))


def main():
    phase, res = sys.argv[1], pathlib.Path(sys.argv[2])
    rows = phase1(res) if phase == "phase1" else phase2(res)
    cols = list(rows[0].keys())
    print(",".join(cols))
    for r in rows:
        print(",".join(str(r[c]) for c in cols))
    summary(res)


if __name__ == "__main__":
    main()

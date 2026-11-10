# 04 · Methodology

All parameters below live in [`config/bench.toml`](../config/bench.toml) and are **frozen (committed and tagged `methodology-v1`) before the first scored run**.

## SLO (pass criteria for one measurement step)

| Check | Threshold | If it fails |
|---|---|---|
| p95 latency | < 100 ms | step fails (app) |
| p99 latency | < 250 ms | step fails (app) |
| Error rate | < 1% (non-2xx, timeouts, connection errors; expected 422s in test 5 don't count) | step fails (app) |
| k6 `dropped_iterations` in the settle tail + measure window (the last 35 s) | = 0 | **run invalid**: the load generator couldn't keep up. Exception: if the step already fails p95, p99 or the error rate, dropped iterations only mean k6 ran out of VUs waiting on a slow app, so the step is a normal **fail**. Drops in the first 10 s of settle (the app's start-up spike at a new rate) don't count, like every other settle check; the runner reads the window from k6's Prometheus counter, and the step file keeps k6's total too (decided 2026-10-17, after Node test 5 dropped 71 iterations in its first seconds and 0 afterwards) |
| `ethtool` `*_allowance_exceeded` | no increase on app, db, loadgen | **run invalid**: network limit, not app |
| db host CPU (Postgres + downstream mock) | < 70% average over the step | Tests 2–5 and 9: **not invalid**, the step is flagged `db_bound` (the db may be the bottleneck, see [below](#when-postgres-is-the-bottleneck)). Tests 1, 6, 7, 8 (no Postgres work; test 6 uses the downstream mock): **run invalid**, downstream headroom violated |
| loadgen host CPU (k6 + Prometheus + Grafana) | < 70% average over the step | **run invalid**: generator headroom violated |

An invalid run gets the setup fixed and is rerun. It is never recorded as a failure of the stack.

Request timeout in k6 is 5 s. With the p99 threshold at 250 ms, anything that slow already fails the step.

## Finding `max_rps` (one search per stack × test)

Load is **open-model**: k6 `constant-arrival-rate` offers a fixed number of requests per second whether or not the app keeps up, and a saturated app shows rising latency and errors. Closed-loop tools (fixed virtual users) hide overload, which is why they aren't used here.

After a reset, each (stack, test) pair runs through four phases: **smoke → warm-up → stress probe → load steps**, with a **recovery check** after every load step.

```
0. Reset      restart the stack container (fresh process); write tests (5, 9) also recreate
              the DB from its template; wait for GET /health == 200
1. Smoke      10 s at 50 req/s. Every response must be correct.         → not scored
2. Warm-up    60 s ramping from 50 to 500 req/s (test 8: 100).           → not scored
3. Probe      doubling 10 s stages until the SLO breaks → estimate E      → not scored
4. Load steps fixed-rate steps (settle 15 s + measure 30 s) around E,    → SCORED
              each followed by a recovery check
5. Result     max_rps = highest passing step below the lowest failing one, confirmed
```

### 1. Smoke

10 s at a low fixed rate (50 req/s; for test 9 the equivalent in sessions). k6 `check()`s verify the status code and response shape of every response.

- **Any unexpected error** → the pair stops with status `setup_error`. This means a broken deployment (wrong env var, DB not reachable, missing key file), not a slow stack. It is fixed and rerun, never scored.
- The smoke run's **p50 latency is saved as the baseline** for the recovery check.

This catches a broken setup in 10 seconds instead of after 15 minutes of load steps.

### 2. Warm-up

60 s with the rate ramping from 50 to 500 req/s (test 8: 100). JIT runtimes (JVM, .NET, V8, PHP JIT) compile their hot paths, and pools and caches fill. Every stack gets exactly the same warm-up.

### 3. Stress probe (aiming, not scoring)

A series of short fixed-rate k6 runs that **doubles the rate every 10 s**, starting at 500 req/s where the warm-up ended (test 8: 100): 500 → 1,000 → 2,000 → … up to `probe_max_rate`. Each stage is a 5 s settle followed by a 5 s measure window, judged exactly like a load step's measure window. The probe stops at the **first stage whose window breaks p95, p99 or the error rate**; the rate of the stage before it is the estimate **E**. It takes at most about 1.5 minutes.

Separate stages replace the original design (one ramping run read through k6 `--out json`), because per-sample JSON output can't run at high request rates ([09 R4](09-review-findings.md#r4-k6-json-output-cannot-run-at-high-request-rates)). The k6 end-of-test summary of each stage is exact and cheap.

E is only used to **aim** the load steps. It isn't scored, because while the rate keeps rising the server never reaches a steady state and latency reacts with a delay, so E is biased. Dropped iterations during the probe are expected and allowed.

After the probe, the recovery check (below) runs. The probe always ends in overload, so this is where a restart is most likely.

### 4. Load steps (scored)

Each **step** is one k6 run at a fixed rate with two scenarios:

- `settle` (15 s): the server adjusts to the new rate (pool growth, GC heap sizing, new downstream connections). Excluded from all checks.
- `measure` (30 s): tagged `phase:measure`. All SLO and validity checks apply only to this window.

The search:

```
a. First step at 0.8 × E
b. Bracket:  pass → next rate ×1.15, until a step fails
             fail → next rate ×0.75, until a step passes (or the lowest rate fails → slo_fail)
c. Bisect between the last pass and the first fail,
   stop when (fail − pass) / pass ≤ 5%
d. max_rps = highest passing rate below the lowest failing rate
   (test 9: measured request rate, see 02-test-spec)
e. Confirm: rerun max_rps once. If it fails, step down one bisection interval
   and run again (at most 3 times). Recorded as `confirmed: true/false`.
```

A pass above a rate that already failed is noise and is not used ([09 R5](09-review-findings.md#r5-the-max_rps-search-rewards-noise)). Both rules are in `config/bench.toml` (`confirm_final`).

If E is accurate, this takes about 4–5 steps (bracket gap 15% → 7.5% → 3.75%). If E is off, the bracketing still finds the limit; it just takes a few more steps.

### Recovery check (after every load step and after the probe)

A fixed pause isn't enough after a **failed** step. An overloaded server may still be draining queues or carrying an inflated heap and extra threads. The binary search then tests a lower rate, and the stack would be judged while still recovering. Instead of a fixed pause, the runner waits until **all** of these hold:

| Condition | Why |
|---|---|
| App container CPU < 5% (average over the last 5 s) | Queued work is finished |
| 5 s probe at 20 req/s: p50 ≤ 1.5 × smoke p50 (+1 ms) | The app answers at normal speed again |
| No active queries from the app in `pg_stat_activity` | DB side is idle |
| Write tests (5, 9): `CHECKPOINT` issued and finished | Flushing earlier writes to disk doesn't land randomly inside the next measure window |

Wait at least **10 s** and at most **60 s**. If the conditions aren't met after 60 s, the runner **restarts the container and reruns the warm-up**, then continues the search. Each step records `recovery_s` and `recovery_restart: true/false` for transparency.

### Verifying the settle length

15 s of settle is a starting value, not a guess we trust blindly. For every passing step the runner compares the **p99 of the last 5 s of settle** with the **p99 of the measure window**. If they differ by more than 20%, the server was still adjusting when measurement started, and the step is flagged `settle_unstable`.

No pilot is run (decided 2026-10-11), so 15 s is used as it is. In the scored campaign the flag is only reported, never used to change a result. If many steps are flagged, that is stated with the results.

### Parameters

| Parameter | Value |
|---|---|
| Smoke | 10 s at 50 req/s |
| Warm-up | 60 s, ramp 50 → 500 req/s (test 8: 100) |
| Probe | start 500 req/s (test 8: 100), ×2 every 10 s, max 400k req/s; abort at error rate > 10% or p99 > 2 s |
| First load step | 0.8 × E |
| Bracketing | ×1.15 up after a pass, ×0.75 down after a fail |
| Lowest step | 100 req/s. If that fails, `status = slo_fail` |
| Bisection resolution | 5% (same as the tie threshold) |
| Max load steps | 15 |
| Settle / measure | 15 s / 30 s |
| Recovery | min 10 s, max 60 s, then restart + warm-up |

**Status values:**
- `ok`
- `setup_error`: the smoke test failed. This is a deployment bug: fix it and rerun. Never scored.
- `slo_fail`: failed even at the lowest step
- `crash`: the container exited
- `oom`: the container exited with `OOMKilled=true` (`docker inspect`)
A crash or OOM at a higher step still records the last passing rate as `max_rps` with status `ok`, and the event is noted in the step log. Only a stack that can't pass any step is scored as failed. This matches "highest rate at which all thresholds pass".

## Test 9 extras: common-load latency and memory

1. Run the test 9 search for **all** stacks first (sessions 1–7, one stack each, see [06](06-runner-and-results.md#sessions-one-stack-per-day)).
2. `L_common = 0.8 × min(max_rps of stacks that passed test 9)`. Test 9 offers load as sessions/s, so the runner divides by 4.65 requests per session ([02](02-test-spec.md#9--mixed-workload-user-sessions-authenticated)).
3. In one final session (session 8), for each stack in a shuffled order (seed in the manifest): restart, reset the DB, smoke, warm up, then run **120 s at `L_common`**. All 7 stacks share that session's hosts, so the latency and memory scores compare them on the same machines. Record:
   - `p99_common_ms`: k6 p99 over the 120 s
   - `peak_rss_mb`: the peak of **`anon + shmem`** from the app container's cgroup `memory.stat`, sampled every 1 s on the app host ([09 R2](09-review-findings.md#r2-the-memory-metric-probably-misses-shared-memory)). cgroup accounting counts each page once, so this covers 4 Node workers, 4 Uvicorn workers or all FPM children plus Nginx without double counting shared pages, and it includes shared memory such as PHP's OPcache segment. cAdvisor's `container_memory_rss` is the cgroup's `anon` only (verified: no shmem series on cgroup v2); it is recorded next to it as a cross-check. A stack must run as one container (`app`).

> **Decision note:** the scoring guide says "peak RSS under load during test 9". Measuring RSS at the **common load** compares every stack doing the same amount of work. Measuring it at each stack's own max rate would penalize fast stacks for carrying more traffic. The peak RSS during the max-rate search is also recorded as `peak_rss_search_mb` for transparency. See [open decisions](08-roadmap.md#open-decisions).

## Fairness rules

- Exactly one stack runs on the app host at a time. Nothing else runs there apart from `node_exporter` and `cAdvisor`, which are identical for every stack.
- Same region and AZ, AMI, kernel, sysctls, Docker version, instance type and CPU model for every stack, checked against the campaign's hardware spec at the start of every session ([06](06-runner-and-results.md#sessions-one-stack-per-day)).
- **Each stack runs in its own session on freshly provisioned hosts** (one stack per day, decided 2026-10-17), so stacks do **not** share one physical machine. Differences between physical machines of the same type (noisy neighbors on spot hosts, memory and cache placement) are accepted and stated in the video. The exception is the test 9 common-load phase, which runs all stacks in one session.
- All hosts are spot instances. A spot interruption pauses the session; it continues only on the same instance type and CPU model, and only the interrupted (stack, test) pair is rerun ([06](06-runner-and-results.md#spot-interruptions)).
- Identical smoke, warm-up, probe, step duration, recovery rules and payload sets.
- The conformance suite passes for every stack at the exact commit that gets benchmarked.

## When Postgres is the bottleneck

The db host is the same size as the app (4 cores / 8 GB), to keep the lab cheap. **Postgres is allowed to be the bottleneck** in the database tests (2–5 and 9), and the results say so instead of hiding it. In production the database is usually the wall too.

Before the campaign session, the **DB ceiling** for tests 2–5 is measured **manually**: the exact SQL each test produces runs through `pgbench` custom scripts (`runner/pgbench/`) with prepared statements, at 64 clients, from the loadgen host (the runner's optional `calibrate` command does the same). The values are written by hand to `results/calibration.json`, where the manifest and the charts read them. That gives the maximum the database itself can serve over this network. The db-sizing test measured on this host type: test 2 ≈ 130k, test 3 ≈ 7.1k (with the old 100-row page; ~13.7k expected with 50 rows, from a local pgbench ratio of 1.93×), test 4 ≈ 15.1k, test 5 ≈ 9.0k req/s ([results/sizing/db-2026-10](../results/sizing/db-2026-10/summary.md)).

| Rule | What happens |
|---|---|
| db CPU ≥ 70% during a measured step (tests 2–5, 9) | The step is **valid** and flagged `db_bound`. A result whose final `max_rps` step is `db_bound` is reported as **"DB-limited"**: the stack reached at least that rate, and the database stopped it there. |
| Scoring | Unchanged: the measured `max_rps` is scored as it is. DB-limited stacks naturally land close together, and the 5% tie rule applies as usual. |
| Charts and video | Each DB test's chart shows the calibrated DB ceiling as a line and marks DB-limited stacks. The video states which tests were DB-bound and for which stacks. |
| Lock waits above 1% of time (`pg_stat_activity`), WAL fsync saturated | Investigate; raise gp3 IOPS/throughput above the free baseline only if WAL fsync is the limit. CPU is the expected limit, not the disk. |

Stacks below the DB ceiling are still measured cleanly: for them the app is the limit, exactly as before.

The ceiling is for the test's own SQL, so a stack can be DB-limited below the line: its data layer may add statements (Spring Data and SQLAlchemy wrap every read in a transaction, Npgsql resets each pooled connection with `DISCARD ALL`), and PHP opens a new connection per request. Conformance reports each stack's SQL statements and new connections per request for tests 2–5, and [03](03-tech-stacks.md#database-work-per-request) lists them, so the video can show why DB-limited stacks stop at different rates.

## Proving the load generator is not the bottleneck

- The manual calibration also runs the echo script (`00-echo.js`, one GET per iteration) against a trivial echo server on the app host to measure **k6's own max rate** on the chosen loadgen size, with Prometheus and remote-write running and Grafana stopped, exactly as in the campaign.
- **Several k6 processes per step** (decided 2026-10-17, [results/sizing/k6-split-2026-10](../results/sizing/k6-split-2026-10/summary.md)). One k6 process dropped iterations above ~20k req/s while 70–80% of the loadgen was idle: the limit is per process, so a bigger loadgen doesn't help. A step above `k6_max_rate_per_process` (6,000 req/s, `[loadgen]` in `bench.toml`) runs as N processes with RATE/N each, all on the k6 cores, started paused and resumed together through k6's REST API. Counts add up; the merged p50/p95/p99 is the **highest** process value, an upper bound on the true percentile (a percentile of a mixture lies between its parts'), tight because every process sends the same mix at the same rate (spread 1–4% at 15–40k). Steps at or below 6,000 req/s run as one process. Measured clean (0 dropped) to 64k req/s, where loadgen CPU reaches the 70% limit; at 64k the echo server alone put the 4-core app host at 69% CPU, so the loadgen can push about as much as any stack can serve on that host. Heavier requests (test 7's 4 KB body) cost k6 more CPU; the per-step drop and CPU checks cover them.
- If any stack's test needs more than 70% of the k6 ceiling, the campaign uses a `c7a.8xlarge` loadgen from the start (chosen before provisioning). Splitting across several loadgen *hosts* is **not implemented** ([09 R4](09-review-findings.md#r4-k6-json-output-cannot-run-at-high-request-rates)).
- `dropped_iterations = 0` and loadgen CPU < 70% are validity checks on every step. k6 pre-allocates VUs for 250 ms of latency per request (the p99 SLO; at most 500 ms). With the earlier 100 ms, a 169 ms stall of the app in a step's settle phase made k6 start VUs mid-step and drop 37 of 670k iterations, invalidating a step that passed (Node, test 2, 22k req/s, 2026-10-17). k6 counts drops over the whole run, not per phase, so the pool must absorb any stall a passing app can have. **Raised 2026-11-10** (during the campaign, before the .NET session finished): a ~0.5 s .NET stall in test 4 at 4.2k req/s (p99 205–215 ms, still passing) dropped 54 iterations in the measure window twice in a row. The pool is now the larger of 250 ms and 1 s capped at 2,500 VUs per k6 process (`k6/lib/options.js`); `maxVUs` covers 1 s. The cap keeps memory and k6's per-process limit in check at high rates (a 10× pool made 40k worse, [results/sizing/k6-split-2026-10](../results/sizing/k6-split-2026-10/summary.md)). Stacks measured earlier keep their results: a bigger pool only matters at steps where k6 ran out of pre-allocated VUs; their passing steps had 0 drops (the validity check), and at their failing steps a bigger pool would only have sent the dropped requests too, adding load to an app that already missed the SLO, so no fail could have become a pass.
- k6 runs with `GOGC=200` and `GOMEMLIMIT=16GiB` (`[loadgen]` in `bench.toml`, decided 2026-10-17). On the 2–3 core Graviton of the k6 sizing test ([results/sizing/k6-2026-10](../results/sizing/k6-2026-10/)) the default collector made k6 drop iterations at only 40–60% CPU, so the GC was turned off (collect only near the limit). On the 14 k6 cores of the `c7a` loadgen that backfired: k6 makes ~13 KB of garbage per request, filled the 16 GiB within one 45 s step and then collected constantly near the limit, stalling at high rates; the 60 s warm-ups and 120 s common-load runs would hit it hardest. With `GOGC=200` k6 stays at 7–11 GiB and collects on idle cores ([results/sizing/k6-split-2026-10](../results/sizing/k6-split-2026-10/summary.md)).

## Known limitations (stated in the video)

- **One run per test.** Differences under 5% are reported as ties.
- **Each stack ran on different physical machines** (one AWS session per stack, on different days). Same instance type and CPU model, but noisy neighbors and placement can shift results by a few percent between stacks; the 5% tie rule covers part of that. Only the common-load latency and memory scores come from one shared session.
- One hardware shape: results may change on ARM (Graviton) or with more or fewer cores.
- One stack per language. A different framework would give that language a different score.
- Cloud noise: dedicated physical cores reduce it but don't eliminate it, and with one session per stack it isn't shared equally between stacks.

---

## Appendix A: reported-only metrics (considered, not run)

> **Status: decided 2026-10-11, none of these will be run.** They are not in `config/bench.toml`, the runner or the scores, and they won't be added for this campaign (see [D8 in the roadmap](08-roadmap.md#open-decisions)). They are kept here only as a record of what was considered. Each would have been shown in the video and published in the results, but never fed into `score.py`, so dropping them doesn't change the ranking.

### A.1 Startup time and idle memory

| | |
|---|---|
| What | Time from `docker compose up` (container start) to the first `GET /health` = 200, and RSS after 30 s idle with no traffic |
| Why | Matters for autoscaling, rolling deploys, containers and serverless. JVM/.NET vs Go/Rust differ a lot. |
| How | The runner already restarts the stack before every test, so it can record both on every restart and report the **median of the 8 restarts** per stack. Idle RSS comes from cAdvisor, same as `peak_rss_mb`. |
| Cost | Close to zero (about 30 s idle wait per restart → ~30 min per campaign) |
| Open questions | Do the startup-built sales dataset (test 8) and JIT warm-up count? (Proposed: yes, time to ready is what a deploy waits for.) Report image size too? |

### A.2 Overload and recovery

| | |
|---|---|
| What | How a stack behaves when traffic exceeds its capacity, and how fast it gets back to normal |
| How | After the test 9 common-load run: 60 s at `0.8 × own max_rps` → **30 s at `1.5 × own max_rps`** → 60 s back at `0.8 × own max_rps`. Record the error rate and p99 during the spike, and the **recovery time** (seconds after the spike until p99 is back under the SLO for 10 s in a row). Crash or OOM during the spike is reported. |
| Why | Some runtimes queue and recover; some collapse, time out or get OOM-killed. That can matter more than max throughput. |
| Cost | ~3 min per stack (~25 min per campaign) |
| Open questions | Spike size 1.5× or 2×? Relative to own max (fair stress per stack) or to a common rate (same absolute load)? The proposal is own max. |
| Note | `dropped_iterations` will be > 0 during the spike by design. It is not a validity failure for this metric, but loadgen CPU < 70% still applies. |

### A.3 Soak test

| | |
|---|---|
| What | Stability over time: memory growth, GC pauses and latency drift |
| How | Test 9 workload at **`0.7 × own max_rps` for 15 min**. Record RSS and p99 as time series (per minute). Report **RSS growth** (last minute vs minute 2) and **p99 drift** (worst minute vs median minute). |
| Why | A 30 s measurement window can't show leaks, slow heap growth or periodic full-GC pauses |
| Cost | ~17 min per stack (~2 h per campaign) |
| Open questions | Is 15 min long enough (30 min doubles the cost)? Use own-max or `L_common`? (Own max shows behaviour near real capacity; `L_common` gives equal work.) Should a crash or OOM here be flagged in the main results table? |

### A.4 Core scaling efficiency

| | |
|---|---|
| What | How well each runtime turns extra CPU cores into extra throughput: **threads in one process** (Go, Java, .NET, Rust) vs **one process per core** (Node cluster, Uvicorn workers, PHP-FPM) |
| How | Run the normal test 8 search (in-memory aggregation, pure CPU) a second time with the app limited to **1 core**: Docker `cpuset: "0"`, and `WORKERS=1` for multi-process stacks. Everything else stays identical. Then:<br>`scaling = max_rps(4 cores) ÷ (4 × max_rps(1 core))`<br>100% = perfect use of all 4 cores. Less means cores are lost to GC threads, lock contention, scheduler or process overhead. |
| Why | The scored tests let every stack use all 4 cores, so they show *how fast* each one is on 4 cores, but not *how well it scales*. This metric makes the threads-vs-processes difference visible on its own. |
| Notes | Thread-based runtimes detect the 1-core limit by themselves (Go `GOMAXPROCS`, JVM, .NET and Tokio all read the cpuset). PHP-FPM keeps the same `pm.max_children`, so its processes just share the one core. DB pool size per process stays the same. Test 8 uses no DB, so pool sizing doesn't matter here. |
| Cost | One extra search per stack, ~13 min each (~1.5 h per campaign) |
| Open questions | Test 8 only, or also test 7 (JSON)? Add a 2-core point to draw a scaling curve (1 → 2 → 4)? |

### Time and cost they would have added

All four together would have added about **4.5 h** of campaign time (~$1.10 at the ~$0.24/h spot estimate in [08](08-roadmap.md#budget-estimate-eu-north-1c-spot-2026-10-11-spot-prices-move-check-before-each-session)), mostly the soak tests and the core-scaling searches, as a separate runner phase (`bench.py extras`) after the scored campaign. That phase is not implemented.

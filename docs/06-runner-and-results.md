# 06 · Runner and results

`runner/bench.py` runs the whole campaign without anyone at the keyboard. It runs **on the loadgen host**, inside the VPC, and controls the other hosts over SSH (keys distributed by `infra/host-setup`). It uses only the Python standard library (`tomllib`, `subprocess`, `urllib`, `json`), so there is nothing to install apart from k6.

## Commands

| Command | What it does |
|---|---|
| `bench.py check` | Pings every host, verifies versions (Docker, k6, Postgres), sysctls and that `bench_template` exists |
| `bench.py conformance [--stacks …]` | Starts each stack and runs `conformance/check.py` against it. Fails fast. |
| `bench.py calibrate` | Measures the DB ceiling (pgbench, tests 2–5), the k6 ceiling (echo server) and the downstream mock ceiling. Writes `results/calibration.json`; `run` copies it into the manifest. |
| `bench.py run [--stacks …] [--tests …]` | The full search for each stack × test, then the test 9 common-load phase |
| `bench.py common-load` | Reruns only the common-load phase (computes `L_common` from existing test 9 results) |
| `bench.py resume <campaign-id>` | Continues an interrupted campaign and skips completed (stack, test) pairs |
| `bench.py csv <campaign-id>` | Rebuilds `results.csv` from the raw step files |

## Flow of `run`

```
for test in 1..9:                         # test-major order: one test across all stacks
  for stack in shuffled(stacks, seed):     # randomized order, seed saved in manifest
    ssh app:  docker compose -f stacks/<stack>/compose.yml down && up -d
    if test in (5, 9):                     # write tests only; read tests reuse the prewarmed DB
      ssh db: recreate bench from bench_template; VACUUM ANALYZE; pg_prewarm
    wait GET /health
    k6 smoke   (fail → status setup_error, alert, skip pair)
    k6 warm-up
    k6 stress probe (doubling 10 s stages) → estimate E;  recovery check
    search loop (bracket around 0.8×E, then bisect):
      snapshot ethtool counters (app, db, loadgen)
      k6 run k6/tests/<test>.js  -e RATE=…   # handleSummary() writes step.json
      snapshot ethtool counters again
      query Prometheus for the step window: app CPU, app RSS, db CPU, loadgen CPU, PG TPS
      docker inspect app → running? OOMKilled?
      classify step: pass | fail | INVALID(reason)
      INVALID → stop, alert, mark pair for rerun
      recovery check (CPU idle, probe latency, DB idle, CHECKPOINT for writes);
        not recovered in 60 s → restart container + warm-up
    confirm the final rate (rerun once; step down on failure)
    record result row
after all stacks: compute L_common, run common-load phase for test 9
write results.csv, run scoring/score.py → scores.txt
```

Test-major order means a mid-campaign environment change hits every stack in the same test, not one stack across all tests. Combined with the randomized order, that makes the comparison fairer.

## Reading the k6 summary

A step is **one** k6 scenario (no new VUs or connections at the start of measurement). Each iteration tags itself `phase=settle`, `settle_tail` or `measure` by elapsed time, and the summary is read per `phase`. Details: [k6/README.md](../k6/README.md), [runner/README.md](../runner/README.md).

Every k6 script uses `handleSummary()` from `k6/lib/summary.js` to write the end-of-test summary as JSON. The runner computes all step numbers from it:

| Number | Source in the summary | Note |
|---|---|---|
| Request rate | `http_reqs{phase:measure}.count ÷ measure_s` | **Don't use the submetric's `rate` field.** k6 divides the *measure-only* count by the *whole* run duration (settle + measure + graceful stop ≈ 45 s instead of 30 s), which understates the rate by about 33%. The overall `http_reqs.rate` is close to correct but includes the settle phase. |
| p95 / p99 | `http_req_duration{phase:measure}` | `p(99)` is added to `summaryTrendStats` (k6 shows only up to p95 by default) |
| Error rate | `http_req_failed{phase:measure}.rate` | Expected 422s (test 5) and the conformance-only 401/403 cases are marked as expected with `http.setResponseCallback` |
| Dropped iterations | `dropped_iterations.count` | Must be 0 |
| Per-endpoint latency (test 9) | `http_req_duration{phase:measure,name:<route>}` | Each request has a fixed `name` tag (its route template) and each session a `journey` tag |
| Settle stability | `http_req_duration{phase:settle_tail}` p99 | The last 5 s of settle, for `settle_unstable` |

k6 only includes a tagged submetric such as `http_reqs{scenario:measure}` in the summary if a threshold references it. The scripts therefore declare one for each of these, even when it is just a pass-through like `count>=0`. The real pass/fail decisions are made by the runner from these numbers, using the values in `config/bench.toml`.

## Result layout

```
results/
  2026-11-02T0930Z-a1b2c3/              # campaign id = start time + git short sha
    manifest.json                       # git sha, bench.toml copy+hash, instance types & IDs,
                                        # AMI, kernel, Docker/k6/Postgres versions, stack image
                                        # digests, shuffle seed, calibration results
    calibration.json
    results.csv                         # input for score.py (format in 05-scoring.md)
    scores.txt                          # score.py output
    <stack>/
      <test>/
        search.json                     # every step: rate, verdict, p50/p95/p99, err%, rps achieved
        steps/<rate>.summary.json       # raw k6 summary export
        steps/<rate>.metrics.json       # Prometheus snapshot for the step window
        steps/<rate>.ethtool.json       # before/after counters on app, db, loadgen
        app.log                         # container stdout/stderr (warnings/errors only)
      common-load/
        summary.json, metrics.json      # test 9 at L_common: p99_common_ms, peak_rss_mb
```

- Everything above is small (summaries, not raw per-request samples) and is **committed to git**, so viewers can audit any number.
- No raw per-request streams are written: every number comes from k6 summaries and Prometheus. `results/**/raw/` stays git-ignored for ad-hoc captures.
- Grafana snapshots of interesting moments (for the video) are exported to `results/<campaign>/grafana/`.

## Safety

- The runner never deletes a campaign folder. `resume` only appends.
- Every step file is written atomically (temp file + rename), so a crash never leaves half a result.
- A `--dry-run` flag prints the planned steps and estimated duration without sending load.

## Estimated duration

Per (stack, test): reset ~40 s (+30 s DB recreate for tests 5 and 9) + smoke 10 s + warm-up 60 s + probe ~1.5 min + ~5 load steps × (45 s + ~15 s recovery) ≈ **8–9 min**, and it is about the same for fast and slow stacks.
7 stacks × 9 tests ≈ **9 h**, plus the common-load phase (~30 min) and the occasional recovery restart. Adopting the confirmation step from [09 R5](09-review-findings.md#r5-the-max_rps-search-rewards-noise) adds one step per pair (~1 h).

Where the time went compared with the first draft (~13 h): measure window 45 → 30 s, bisection resolution 3% → 5% (one step fewer per pair), probe starting at the warm-up's end rate with 10 s stages, a 10 s smoke, and no DB recreate for tests that don't write.

Run it as **one unattended session** (start in the evening, collect in the morning). Idle hours with the hosts up cost as much as measured ones.

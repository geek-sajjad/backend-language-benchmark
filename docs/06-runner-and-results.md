# 06 · Runner and results

`runner/bench.py` runs the whole campaign without anyone at the keyboard. It runs **on the loadgen host**, inside the VPC, and controls the other hosts over SSH (keys distributed by `infra/host-setup`). It uses only the Python standard library (`tomllib`, `subprocess`, `urllib`, `json`), so there is nothing to install apart from k6.

## Commands

| Command | What it does |
|---|---|
| `bench.py check` | Pings every host, verifies versions (Docker, k6, Postgres), sysctls and that `bench_template` exists |
| `bench.py conformance [--stacks …]` | Starts each stack and runs `conformance/check.py` against it. Fails fast. Local development only: conformance passes once locally at the frozen commit and is not rerun on AWS. |
| `bench.py calibrate` | Measures the DB ceiling (pgbench, tests 2–5), the k6 ceiling (echo server) and the downstream mock ceiling. Writes `results/calibration.json`; `run` copies it into the manifest. Optional helper: calibration is done manually before the campaign session, and its results are written to `results/calibration.json` by hand ([07](07-runbook.md#2-freeze-the-methodology)). |
| `bench.py run [--stacks …] [--tests …]` | The full search for each stack × test, then the test 9 common-load phase |
| `bench.py common-load` | Reruns only the common-load phase (computes `L_common` from existing test 9 results) |
| `bench.py resume <campaign-id>` | Continues an interrupted or paused campaign and skips completed (stack, test) pairs. After a spot interruption it first checks that the hardware is identical ([Spot interruptions](#spot-interruptions)) |
| `bench.py csv <campaign-id>` | Rebuilds `results.csv` from the raw step files |

## Flow of `run`

```
for test in 2..9:                         # test-major order: one test across all stacks
  for stack in shuffled(stacks, seed):     # randomized order, seed saved in manifest
    ssh app:  docker compose -f stacks/<stack>/compose.yml build   # first time per stack: real build; later: cache hit
              record image id (must match the stack's earlier tests)  # not implemented yet: today up uses --no-build
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
      classify step: pass | fail | INVALID(reason); db CPU ≥ 70% in tests 2–5, 9 → flag db_bound (still valid)
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
                                        # AMI, kernel, CPU model, Docker/k6/Postgres versions, stack image
                                        # digests, shuffle seed, calibration results
    calibration.json                    # hand-written from the manual calibration
    paused.json                         # only while paused by a spot interruption
    current.json                        # the pair in progress (used by resume)
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

### `calibration.json`

The DB, k6 and downstream ceilings from the manual calibration, done before the campaign session ([07](07-runbook.md#2-freeze-the-methodology)). It is written **by hand** to `results/calibration.json` and committed. The optional `bench.py calibrate` writes the same shape.

```json
{
  "created": "2026-10-20T18:00:00Z",
  "mode": "manual",
  "db_ceiling": {
    "2": {"tps": 129621, "clients": 64, "seconds": 30, "db_cpu": 1.00},
    "3": {"tps": 13700,  "clients": 64, "seconds": 30, "db_cpu": 1.00},
    "4": {"tps": 15090,  "clients": 64, "seconds": 30, "db_cpu": 1.00},
    "5": {"tps": 8956,   "clients": 64, "seconds": 30, "db_cpu": 0.99}
  },
  "k6_ceiling": {"ceiling_rps": 67000},
  "downstream_ceiling": {"ceiling_rps": 120000}
}
```

The values above are only an example. Use what the manual runs measured.

| Field | Meaning | Read by |
|---|---|---|
| `created`, `mode` | When it was measured; `"manual"` for hand-written files (`calibrate` writes `"aws"` or `"local"`) | Manifest only |
| `db_ceiling."<test>".tps` | pgbench req/s for tests 2–5 (`runner/pgbench/test<N>.sql`, 64 clients, `-M prepared`, from loadgen). Keys are strings | `scoring/charts.py`: the dashed Postgres ceiling line. A test without `tps` gets no line |
| `db_ceiling."<test>".clients`, `.seconds`, `.db_cpu` | pgbench settings and the db host's CPU (0–1) during the run | Manifest only |
| `k6_ceiling.ceiling_rps` | k6's highest clean rate against the echo server, with Prometheus running and Grafana stopped. It decides the loadgen size (70% rule, [04](04-methodology.md#proving-the-load-generator-is-not-the-bottleneck)) | Manifest only |
| `downstream_ceiling.ceiling_rps` | The downstream mock's highest clean rate (must be ≥ 2× the best expected test 6 rate) | Manifest only |

`calibrate` also writes a `stages` list under `k6_ceiling` and `downstream_ceiling`. It's optional in a hand-written file.

Where it's used:
- `run` copies the file from `results/calibration.json` **on loadgen** into `manifest.json` (`"calibration"`). `deploy.sh` doesn't sync `results/`, so copy the file to loadgen by hand before `run` ([07 step 5](07-runbook.md#5-check-on-loadgen)). Without it the manifest's `calibration` is `null`, but the campaign still runs.
- `scoring/charts.py` reads the DB ceilings from the manifest copy first, then `results/<campaign-id>/calibration.json`, then the local `results/calibration.json`.

## Safety

- The runner never deletes a campaign folder. `resume` only appends.
- Every step file is written atomically (temp file + rename), so a crash never leaves half a result.
- A `--dry-run` flag prints the planned steps and estimated duration without sending load.

## Spot interruptions

All three hosts are persistent spot instances that **stop** (not terminate) when AWS reclaims them ([01](01-architecture.md#instances)). A stopped instance keeps its ID, disk, private IP and instance type; AWS starts it again when capacity returns, usually on a different physical machine. The rule is that **an interruption never mixes different hardware into one result**:

1. **Detect.** A background thread polls the spot interruption notice (`/latest/meta-data/spot/instance-action`, about 2 min warning) on all three hosts every 10 s, between and during steps. A host that fails 3 polls in a row over SSH (~30 s) counts as interrupted too. When a command fails or a step turns INVALID, the runner first polls again to check whether a host is going away, so an interruption is never reported as an INVALID setup.
2. **Pause and save.** The runner kills the running k6 process, keeps every finished (stack, test) pair as it is (step files are already written atomically), writes no `result.json` for the **interrupted pair** and writes `paused.json` (time, host, reason, stack, test). It then exits with status 3 (`paused_spot`). Nothing is scored from a pair that saw an interruption, even if the notice came after its last step. When the pair is rerun, its unfinished steps are moved to `<stack>/<test>/discarded/<time>/`.
3. **Wait.** Nothing runs until the hosts are back. When they are (`terraform apply -refresh-only`, then `./infra/host-setup/deploy.sh`, because the public IPs change), the operator runs `bench.py resume <campaign-id>`. This can be hours or another day later.
4. **Check the hardware before continuing.** `resume` compares each host with the fingerprint stored in `manifest.json`: **instance type, instance ID, CPU model (`lscpu`), core count, AMI, kernel, Docker / k6 / Postgres versions**. If anything differs, `resume` refuses (exit 3) and the campaign stays paused until identical hardware is available again. It never continues on a different machine type. The check runs on every `resume`, not only after a pause.
5. **Rerun what the move could have affected.**
   - **app host was interrupted:** it now runs on a different physical machine. Every pair of the **current test** that already finished is rerun too, so all stacks in one test are always compared on the same physical machine. Earlier tests are kept (each test is scored on its own). The runner detects the move from the host's `boot_id` (stored in the manifest), so this also works when the loadgen died before it could write `paused.json`. The current test comes from `paused.json`, or else from `current.json` (the pair in progress). Results set aside this way are renamed `result.superseded-<time>.json`.
   - **db or loadgen was interrupted:** only the interrupted pair is rerun. These hosts aren't measured, and the per-step validity checks (db and loadgen CPU, `dropped_iterations`, ethtool) keep proving their headroom.

`terraform destroy` or `-replace` during a campaign is a re-provision, not an interruption: then **all** stacks are rerun ([04 fairness rules](04-methodology.md#fairness-rules)). Every pause and the reruns it caused are listed in the manifest.

## Estimated duration

Per (stack, test): reset ~40 s (+30 s DB recreate for tests 5 and 9) + smoke 10 s + warm-up 60 s + probe ~1.5 min + ~5 load steps × (45 s + ~15 s recovery) ≈ **8–9 min**, and it is about the same for fast and slow stacks.
7 stacks × 8 tests ≈ **8 h**, plus the common-load phase (~30 min), the image builds (~30 min: one real build per stack, cache hits afterwards) and the occasional recovery restart. Adopting the confirmation step from [09 R5](09-review-findings.md#r5-the-max_rps-search-rewards-noise) adds one step per pair (~1 h).

Where the time went compared with the first draft (~13 h): measure window 45 → 30 s, bisection resolution 3% → 5% (one step fewer per pair), probe starting at the warm-up's end rate with 10 s stages, a 10 s smoke, and no DB recreate for tests that don't write.

Run it as **one unattended session** (start in the evening, collect in the morning). Idle hours with the hosts up cost as much as measured ones.

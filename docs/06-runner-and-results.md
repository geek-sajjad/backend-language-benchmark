# 06 · Runner and results

`runner/bench.py` runs the whole campaign without anyone at the keyboard. It runs **on the loadgen host**, inside the VPC, and controls the other hosts over SSH (keys distributed by `infra/host-setup`). It uses only the Python standard library (`tomllib`, `subprocess`, `urllib`, `json`), so there is nothing to install apart from k6.

## Commands

| Command | What it does |
|---|---|
| `bench.py check` | Pings every host, verifies versions (Docker, k6, Postgres), sysctls and that `bench_template` exists |
| `bench.py conformance [--stacks …]` | Starts each stack and runs `conformance/check.py` against it. Fails fast. Local development only: conformance passes once locally at the frozen commit and is not rerun on AWS. |
| `bench.py calibrate` | Measures the DB ceiling (pgbench, tests 2–5), the k6 ceiling (echo server) and the downstream mock ceiling. Writes `results/calibration.json`; `run` copies it into the manifest. Optional helper: calibration is done manually before the campaign session, and its results are written to `results/calibration.json` by hand ([07](07-runbook.md#2-freeze-the-methodology)). |
| `bench.py run --stacks <stack> [--campaign <id>] [--tests …]` | The full search for every test of one stack: one AWS session ([Sessions](#sessions-one-stack-per-day)). The first session creates the campaign; later sessions pass `--campaign <id>` and add their stack to it. It refuses a stack that is already in the campaign, and refuses to start while an earlier stack still has unfinished tests (finish that one with `resume` first) |
| `bench.py common-load <campaign-id> [--partial]` | The test 9 common-load phase for every stack, in the final session. Refuses until every configured stack has a test 9 result, since `L_common` depends on all of them (`--partial`, development only: use the stacks already in the campaign). Computes `L_common` from those results |
| `bench.py resume <campaign-id>` | Continues the current session's stack after an INVALID stop, a crash or a spot pause, and skips completed (stack, test) pairs. It first checks that the hosts match the campaign's hardware spec ([Spot interruptions](#spot-interruptions)) |
| `bench.py csv <campaign-id>` | Rebuilds `results.csv` from the raw step files |

## Sessions: one stack per day

The whole campaign is ~17 h of host time, too long to run in one sitting. It is split into **one AWS session per stack** (decided 2026-10-17): provision, run every test of that stack, copy the results to this repo, `terraform destroy`. The next stack runs in a new session, usually the next day. After the seventh stack, one more short session runs the test 9 common-load phase for all stacks. That makes **8 sessions** in total, each about 2 hours ([estimate](#estimated-duration)).

| Session | What runs | Command |
|---|---|---|
| 1–7 | One stack, tests 2–9 (search + confirm per test) | `bench.py run --stacks <stack> --campaign <id>` |
| 8 | Test 9 common load for all 7 stacks: `L_common`, `p99_common_ms`, `peak_rss_mb` | `bench.py common-load <id>` |

**What this gives up:** each stack now runs on different physical machines, provisioned on a different day. Spot hosts on different machines can differ a little in speed (noisy neighbors, memory and cache placement), and that difference now ends up between stacks instead of being shared by all of them. **This is accepted:** the instance type is the same, each vCPU is a dedicated physical core with no SMT, and the 5% tie rule absorbs small differences. It is stated in the video ([04 Known limitations](04-methodology.md#known-limitations-stated-in-the-video)).

**What stays identical across sessions** (the campaign's **hardware spec**, recorded by session 1 and checked at the start of every later session): region and AZ, instance type per role, CPU model (`lscpu`), core count, AMI (pinned with `-var ami_id=…`), kernel, Docker / k6 / Postgres versions, and the git commit (`methodology-v1`). A session whose hosts don't match refuses to start; reprovision until they do. Instance IDs and `boot_id` are recorded per session but not compared: they are expected to change. Docker CE and containerd are pinned and held in `infra/host-setup/bootstrap.sh`, so a new session can't pick up a newer Docker.

The common-load phase runs all 7 stacks in one session on purpose: the latency and memory scores then compare every stack on the same machines, at the same rate.

## Flow of `run` (one session)

```
for test in 2..9:                         # one stack per session, tests in order
    ssh app:  docker compose -f stacks/<stack>/compose.yml build   # first test: real build; later tests: cache hit
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
      classify step: pass | fail | loadgen_bound | INVALID(reason); db CPU ≥ 70% in tests 2–5, 9 → flag db_bound (still valid); loadgen CPU ≥ 70% → loadgen_bound (caps the search like a fail)
      INVALID → stop, alert, mark pair for rerun
      recovery check (CPU idle, probe latency, DB idle, CHECKPOINT for writes);
        not recovered in 60 s → restart container + warm-up
    confirm the final rate (rerun once; step down on failure)
    record result row
session 8 (common-load): compute L_common from all stacks' test 9 results, run 120 s per stack
write results.csv, run scoring/score.py → scores.txt   # after session 8; partial results.csv after each session
```

The body of each test is unchanged from the original test-major design; only the loop order changed (one stack per session instead of one test across all stacks).

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
  calibration.json                      # hand-written from the manual calibration
  sizing/                               # one-off host sizing tests (db-2026-10, k6-2026-10), not campaigns
  2026-11-02T0930Z-a1b2c3/              # campaign id = start time + git short sha (quick runs: quick-<name>)
    manifest.json                       # git sha, bench.toml copy+hash, hardware spec (instance types,
                                        # AMI, kernel, CPU model, Docker/k6/Postgres versions), calibration
                                        # results, and one entry per session: date, stack, instance IDs,
                                        # boot_ids, stack image digest, pauses
    calibration.json                    # hand-written from the manual calibration
    paused.json                         # only while paused by a spot interruption
    current.json                        # the pair in progress (used by resume)
    results.csv                         # input for score.py (format in 05-scoring.md)
    scores.txt                          # score.py output
    <stack>/
      <test>/
        search.json                     # every step: rate, verdict, p50/p95/p99, err%, rps achieved
        steps/<rate>.summary.json       # raw k6 summary export (one k6 process)
        steps/<rate>.p<i>.summary.json  # instead, per process, when the step ran as N k6 processes
        steps/<rate>.merged.json        #   plus the merged numbers, k6_processes and p99_spread_ms
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
3. **Wait, or end the session.** When the hosts are back (`terraform apply -refresh-only`, then `./infra/host-setup/deploy.sh`, because the public IPs change), the operator runs `bench.py resume <campaign-id>`. If the pause drags on, copy the results, `terraform destroy`, and continue the same stack in a new session another day (`terraform apply` with the pinned AMIs, deploy, seed, `resume`).
4. **Check the hardware before continuing.** `resume` compares each host with the campaign's hardware spec in `manifest.json`: **instance type, CPU model (`lscpu`), core count, AMI, kernel, Docker / k6 / Postgres versions**. If anything differs, `resume` refuses (exit 3) and the campaign stays paused until matching hardware is available. It never continues on a different machine type. The check runs on every `resume` and at the start of every session.
5. **Rerun only the interrupted pair**, whichever host it was. Since stacks already run on different machines ([Sessions](#sessions-one-stack-per-day)), a move of the app host to another physical machine mid-session is no different from the next session's new hosts, so finished pairs are kept. (Before 2026-10-17 an app-host move reran the whole current test for every stack; that rule only made sense while all stacks shared one machine.)

A `terraform destroy` and re-provision between or within sessions is allowed and reruns nothing, as long as the new hosts match the hardware spec. Every pause and session is listed in the manifest (a host restart after a pause starts a new session entry).

## Estimated duration

Per (stack, test): reset ~40 s (+30 s DB recreate for tests 5 and 9) + smoke 10 s + warm-up 60 s + probe ~1.5 min + ~5 load steps × (45 s + ~15 s recovery) + one confirmation step ([09 R5](09-review-findings.md#r5-the-max_rps-search-rewards-noise)) ≈ **9–10 min**, and it is about the same for fast and slow stacks.

| Session | Work | Time |
|---|---|---|
| 1–7, each | Provision, deploy, seed ~30 min · image build 1–10 min · 8 tests × ~9.5 min ≈ 75 min · copy results + destroy ~10 min | **~2 h** |
| 8 | Provision, deploy, seed ~30 min · 7 × (image build + reset + smoke + warm-up + 120 s) ≈ 50 min · copy + destroy ~10 min | **~1.5 h** |
| **Total** | | **~15.5 h of host time over 8 days** |

Splitting into sessions costs ~3.5 h more than one long session (seven extra provision/deploy/seed rounds), but each session is short enough to watch, and an interruption or INVALID stop costs at most one stack's afternoon.

Where the time went compared with the first draft (~13 h of measuring): measure window 45 → 30 s, bisection resolution 3% → 5% (one step fewer per pair), probe starting at the warm-up's end rate with 10 s stages, a 10 s smoke, and no DB recreate for tests that don't write.

Idle hours with the hosts up cost as much as measured ones: destroy as soon as a session's results are copied.

# runner

`bench.py` runs a campaign without anyone at the keyboard ([docs/06-runner-and-results.md](../docs/06-runner-and-results.md)). It runs on the loadgen host and reaches app and db over SSH. Python 3.11+ standard library only, plus k6 on `PATH`.

```bash
python3 runner/bench.py check                         # hosts, versions, sysctls, data files, Prometheus targets
python3 runner/bench.py conformance --stacks all      # conformance/check.py against each stack (local only, not run on AWS)
python3 runner/bench.py calibrate                     # → results/calibration.json (optional: calibration is done manually, docs/07)
python3 runner/bench.py run --stacks rust-axum --dry-run                  # plan + estimated duration
python3 runner/bench.py run --stacks rust-axum                            # session 1 → results/<campaign-id>/
python3 runner/bench.py run --stacks go-gin --campaign <campaign-id>      # sessions 2–7: add a stack
python3 runner/bench.py resume <campaign-id>          # after an INVALID stop, a spot pause or a crash
python3 runner/bench.py common-load <campaign-id>     # session 8: test 9 common load for every stack
python3 runner/bench.py csv <campaign-id>             # rebuild results.csv + scores.txt
```

Exit status: 0 done, 2 INVALID step (fix the cause, then `resume`), 3 paused by a spot interruption, or a later session / `resume` refused because a host differs from the campaign's hardware spec (instance type, CPU model, cores, AMI, kernel, Docker, k6, Postgres; instance IDs may differ). Details in [docs/06](../docs/06-runner-and-results.md#spot-interruptions).

A scored campaign runs **one stack per AWS session** ([docs/06](../docs/06-runner-and-results.md#sessions-one-stack-per-day)). `run` runs every test of its stacks, one stack at a time, and never the common load; `common-load` needs a test 9 result for every configured stack (`--partial` for development). Each session (new instance IDs or boot_ids) is recorded in `manifest.json` under `sessions`.

`--config` picks another `bench.toml` (only for development; a scored campaign uses the frozen one) and `--hosts` another hosts file. "all" means every configured stack that has a `stacks/<name>/compose.yml`; the others are skipped with a note.

## Hosts and local mode

`runner/hosts.toml` is written by `infra/host-setup/deploy.sh`:

```toml
mode = "aws"
ssh_user = "ubuntu"
repo_dir = "/home/ubuntu/bench"
prometheus_url = "http://127.0.0.1:9090"
[hosts.app]
private_ip = "10.42.0.10"
[hosts.db]
private_ip = "10.42.0.11"
[hosts.loadgen]
private_ip = "10.42.0.12"
```

The ethtool checks detect each host's primary interface (`ip route get`): it is `ens5` on Graviton but `enp39s0` on `c7a` with the Ubuntu 24.04 AMI. An optional `net_iface = "..."` pins one name for all hosts.

Without that file the runner is in **local mode**: every role is this machine, commands run locally, k6 isn't pinned to cores, and the ethtool and host-CPU validity checks are skipped (they're meaningless when k6, the app and Postgres share one machine). Prometheus is optional locally; without it the recovery check reads CPU from `docker stats`. Local numbers are never published.

## What a pair does

Each (stack, test) pair: reset (restart the container; tests 5 and 9 also recreate the DB) → smoke (body checks; any failure = `setup_error`) → warm-up → probe → load steps with a recovery check after each → confirmation → `result.json`.

| Piece | Behaviour |
|---|---|
| Probe | Doubling stages from `probe.start_rate`, each a short fixed-rate run: `stage_s − window_s` settle, then a `window_s` measure. E = the last stage that met the SLO. |
| Steps | First at 0.8 × E; ×1.15 after a pass, ×0.75 after a fail; then bisect to `bisect_resolution`. At most `max_steps`. |
| `max_rps` | Highest passing rate **below the lowest failing rate** (09 R5). Test 9: that step's measured request rate. |
| Confirmation | `confirm_final = true`: the final rate is rerun; if it fails, the runner steps down one bisection interval, up to 3 times (09 R5). |
| Verdict | `pass` / `fail` from p95, p99 and error rate of the measure window. **INVALID** (campaign stops, fix, `resume`): dropped iterations at an otherwise passing step, an ethtool allowance counter increase, loadgen CPU ≥ 70%, db CPU ≥ 70% outside `db_bound_tests`, or a missing Prometheus metric on AWS. In `db_bound_tests` (2–5, 9) db CPU ≥ 70% keeps the step valid and flags it `db_bound`; a result whose final step is `db_bound` is reported as DB-limited. Dropped iterations at a step that already fails the SLO only mean k6 ran out of VUs waiting on a slow app, so the step is a `fail`. |
| Crash / OOM | The step fails and the event is logged. The recovery check restarts the container and warms up again. Only a pair with no passing step gets status `crash` / `oom`. |
| Recovery | After min 10 s: app CPU < 5% of its cores, no active app queries, a 5 s probe at 20 req/s with p50 ≤ 1.5 × smoke p50 + 1 ms, then `CHECKPOINT` for write tests. Not recovered after 60 s → restart + warm-up. |
| `settle_unstable` | Passing step whose settle-tail p99 differs from the measure p99 by more than 20%. Reported only. |
| Common load | After all test 9 searches: L_common = 0.8 × the lowest passing test 9 `max_rps`; per stack: reset, smoke, warm-up, `common_load.duration_s` at L_common. `p99_common_ms` from k6; peak memory from the metric in `[memory]`. |
| Memory | `cgroup_anon_shmem` (default): anon + shmem from the app cgroup's `memory.stat`, sampled every 1 s on the app host (09 R2). cAdvisor RSS is recorded next to it. |

## Files

| File | Purpose |
|---|---|
| `bench.py` | CLI |
| `benchlib/config.py` | `bench.toml` access, test scripts |
| `benchlib/cluster.py` | Hosts (local or SSH), the stack container, the database, ethtool |
| `benchlib/k6.py` | Runs k6, parses the summary (`http_reqs{phase:measure}.count ÷ measure_s`, never the submetric's `rate`) |
| `benchlib/prom.py` | Prometheus window queries (expressions in `monitoring/README.md`) |
| `benchlib/search.py` | The per-pair search, verdicts, recovery |
| `benchlib/campaign.py` | Manifest and sessions, stack-by-stack order, hardware-spec check, resume, common load, `results.csv` |
| `benchlib/spot.py` | Spot interruption watcher (notice polling, lost SSH), host fingerprint (`SPEC_KEYS` compared, `SESSION_KEYS` only recorded) |
| `benchlib/calibrate.py` | pgbench ceilings for tests 2–5, k6 ceiling (echo server), downstream-mock ceiling |
| `pgbench/test{2..5}.sql` | The SQL each test produces, for the DB ceiling |

All result files are written atomically (temp file + rename). The runner never deletes a campaign folder; a rerun pair adds a new attempt (`search.a2.json`, `steps/a2-…`). Steps of an unfinished attempt (INVALID, interrupted, crashed) are moved to `discarded/<time>/` before the rerun. Finished results are never set aside: a spot interruption reruns only the interrupted pair.

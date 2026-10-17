# 08 · Roadmap

## Milestones

| # | Milestone | Deliverables | Done when |
|---|---|---|---|
| **M0** | Planning | This doc set, repo skeleton, `config/bench.toml`, `score.py` | Open decisions below are answered |
| **M1** | Shared foundation | `db/` schema + deterministic seed + local compose; `downstream/` mock; `conformance/` reference (splitmix64 datasets, expected outputs for tests 2–8, plus 401 cases for auth) + `check.py` | `check.py` passes against a reference implementation |
| **M2** | Seven stacks | `stacks/<name>/`: app, Dockerfile, `compose.yml`, `README.md` (versions, pool size, worker count, deviations) | All 7 pass conformance locally; versions pinned |
| **M3** | Load scripts | `k6/lib/` (SLO thresholds from config, settle/measure scenarios, payload loading); `k6/tests/01…09`; payload generator for test 7; JWT key pair + token generator for test 9 | Each script runs locally against any stack |
| **M4** | Runner | `runner/bench.py` (check, conformance, calibrate, run, resume, csv), Prometheus window queries, ethtool/docker checks, atomic result files, dry-run | Full dry run plus a real run against local compose |
| **M5** | Infra + monitoring | Terraform (VPC, one public subnet, security group, 3 hosts), host-setup scripts, Prometheus config, 3 Grafana dashboards | `terraform apply` → `bench.py check` green |
| **M6** | Manual calibration | `calibration.json` (written by hand from manual pgbench and k6 echo runs), sizing decisions. No pilot (decided 2026-10-11) | Headroom proven; methodology tagged `methodology-v1` |
| **M7** | Scored campaign | 8 AWS sessions: one per stack, then common load ([06](06-runner-and-results.md#sessions-one-stack-per-day)); `results/<campaign-id>/` complete, `scores.txt` | Publishing checklist done |
| **M8** | Video assets | Charts (per test, categories, overall, memory, cost per req), Grafana clips, B-roll of the dashboards | Charts generated from `results.csv` with a script (`scoring/charts.py`; per-test charts with the DB ceiling exist, the category, overall, memory and cost charts don't yet) |

M1 → M2 → M3 is the critical path. M4 and M5 can be built in parallel with M2.

## Open decisions

These are recommendations. Please confirm or change them **before M2** (the PHP and Go choices) and **before M6** (the measurement choices).

| # | Decision | Recommendation | Alternative |
|---|---|---|---|
| D1 | PHP runtime | **PHP-FPM + Nginx** in the main round (most deployed; Laravel's documented default) | Laravel Octane (FrankenPHP). Viewers will ask for it, so it's a good candidate for a bonus segment |
| D2 | Go stack | **Decided 2026-10-14: Gin + GORM** (consistent "framework + ORM" rule); implemented in `stacks/go-gin` | stdlib `net/http` + `pgx` + `sqlc` (what many Go devs consider idiomatic) |
| D3 | Peak RSS window | **At common load** (same work for every stack); also report the peak during the search | During the max-rate search, as the guide literally says |
| D4 | AWS region | **eu-north-1** (cheapest spot for `c7a`/`m7a` as of 2026-10-10; no Graviton spot capacity there, so every host is `c7a`) | Any region with `c7a`; must not change mid-campaign |
| D5 | "Tuned" bonus round | Plan for it after the main video, using each stack's "potential optimizations" | Skip |
| D6 | Java concurrency | **MVC + virtual threads** (Spring's recommended path for new apps) | WebFlux + R2DBC |
| D7 | Downstream delay (test 6) | **50 ms fixed** (leaves 50 ms of the p95 budget) | 20 ms, or jitter (less deterministic) |
| D8 | Reported-only metrics ([04 Appendix A](04-methodology.md#appendix-a-reported-only-metrics-considered-not-run)) | **Decided 2026-10-11: none are run.** Startup time + idle memory, overload recovery, soak test and core scaling efficiency are all dropped. None affected the scores. | Adopt any of them later as a separate, reported-only phase |
| D9 | Campaign split | **Decided 2026-10-17: one AWS session per stack** (provision → all tests of one stack → save → destroy; next stack another day), then one session for the test 9 common load. Stacks no longer share one physical machine; that noise is accepted and stated ([06](06-runner-and-results.md#sessions-one-stack-per-day)) | One ~17 h unattended session, test-major, all stacks on the same hosts |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| k6 can't generate the test 2 / test 6 rates for Rust or Go (test 1, the highest-rate test, was removed for this reason) | Max rate capped by the generator, not the app | k6 helpers optimized (~30% less CPU per request) and Go GC off (no GC stalls; ~146 µs per request, so ~4,800 req/s per k6 core at the 70% limit, ~67k req/s on 14 k6 cores; measured on Graviton up to 3 cores, so the `c7a.4xlarge` loadgen's ceiling is measured in calibration). k6 memory with GC off: filled the 16 GiB limit within one 45 s step on `c7a`, so GOGC=200 since 2026-10-17 (7–11 GiB). Calibrate with monitoring running; resize loadgen to `c7a.8xlarge` (split across hosts only as a last resort); `dropped_iterations` check; a faster generator for the high-rate tests if k6 is the limit. **Found 2026-10-17:** one k6 process drops iterations above ~20k req/s with most of the loadgen idle (per-process limit), so steps above 6k now run as several k6 processes; clean to 64k, about what the app host itself can serve ([results/sizing/k6-split-2026-10](../results/sizing/k6-split-2026-10/summary.md)) |
| Postgres saturates before the fastest stacks (expected on the 4-core db in tests 3–5) | DB-bound scores converge | **Accepted by design**: steps flagged `db_bound` instead of invalid, results marked "DB-limited", DB ceiling drawn on the charts and stated in the video ([04](04-methodology.md#when-postgres-is-the-bottleneck)). The runner flags such steps (`db_bound_tests` in `config/bench.toml`), `results.csv` carries a `db_bound` column and `score.py` lists DB-limited results. `scoring/charts.py` draws the ceiling line on tests 2–5 and hatches DB-limited bars |
| Network allowances on `c7a.xlarge` (burst, **~1.56 Gbps baseline**, half of the 8-core host the payloads were first sized for) | A network limit makes the step INVALID with no setup fix | Test 7 payload cut from 45 items (~12 KB) to 15 items (~4 KB, ~1.3 Gbps at 40k req/s); ethtool validity check; check test 2/6 packet rate (pps allowance) in calibration |
| Accusations of unfair config ("you didn't use X!") | Credibility | Selection rule + allowed-config table + "potential optimizations" lists published; each stack's README reviewed by someone fluent in it, if possible |
| Single run noise, and each stack on different physical machines (one session per stack) | Wrong order between close stacks | 5% tie rule; same instance type, CPU model and AMI checked every session; latency and memory scores from one shared session; the limitation is stated in the video |
| A session interrupted | Wasted time and money | Sessions are ~2 h, one stack each; `resume`, atomic writes, tmux; a long pause can end the session and finish the stack another day |
| Spot interruption (all hosts are spot) | Pause; hardware could change | Runner pauses and saves, continues only on matching hardware (type, CPU model, AMI, kernel); only the interrupted pair is rerun ([06](06-runner-and-results.md#spot-interruptions)). Implemented in `runner/benchlib/spot.py` and `campaign.py`: notice polling every 10 s, `paused.json` + exit 3, hardware-spec check in `resume` and at the start of every session. Tested locally with fake hosts only; not yet exercised on a real interruption |
| No pilot: `settle_s = 15` and the 5% tie threshold ([09 R8](09-review-findings.md#r8-the-tie-threshold-is-not-grounded-in-measured-noise)) aren't checked on AWS before the campaign | A settle or noise problem only shows up in the scored results | `settle_unstable` is reported per step; the tie threshold is stated as an assumption in the video (the db-sizing pgbench repeats spread 1.9%) |
| Monitoring (Prometheus + Grafana, public viewers) shares the loadgen host | Viewer bursts steal CPU from k6 | Grafana stopped during scored runs; Prometheus pinned to 2 cores, k6 to the other 14; loadgen CPU < 70% check; k6 calibrated with monitoring as in the campaign |
| Spot/AZ capacity for `c7a` | Can't provision, or long pauses | Pre-check spot placement scores and price history; pick the AZ with capacity for `c7a.xlarge` and `c7a.4xlarge` |

## Budget estimate (eu-north-1c spot, 2026-10-11; spot prices move, check before each session)

| Host | Type | ~$/h (spot) |
|---|---|---|
| app | c7a.xlarge | 0.057 |
| db + downstream mock | c7a.xlarge | 0.057 |
| loadgen + Prometheus (+ Grafana outside scored runs) | c7a.4xlarge | 0.135 |
| 3 public IPv4 + 3 × 50 GB gp3 | | ~0.033 |
| **Total** | | **≈ $0.28/h** |

| Phase | Hours |
|---|---|
| Sessions 1–7, one stack each: provision, deploy, seed ~0.5 h + image build + 8 tests ~1.3 h + collect and destroy ~0.2 h ([06](06-runner-and-results.md#estimated-duration)) | 7 × ~2 = ~14 |
| Session 8: test 9 common load for all stacks | ~1.5 |
| Reruns / buffer, only if needed (`resume` reruns only the interrupted pair) | 0–3 |
| **Total** | **~15.5 h ≈ $4.40 with no reruns, ~18.5 h ≈ $5.20 with the buffer, over 8 days** |

Not in any session (decided 2026-10-11): conformance runs once locally; calibration is done manually beforehand; there is no pilot.

No NAT gateway, load balancer, provisioned IOPS or extra volumes. Development happens locally, and AWS is torn down between sessions. Hours while the campaign is paused for a spot interruption cost nothing for the stopped host, but the running hosts and the EBS volumes keep billing: stop the other two hosts too if a pause will be long.

**If calibration forces an upsize:** a `c7a.8xlarge` loadgen adds roughly another $0.135/h (about twice the `c7a.4xlarge` price; check the spot price). Decided in calibration, before the scored run. The db is never upsized: a DB-bound result is reported, not fixed.

**What changed from the first estimate (35 h × $4.4/h ≈ $155):**

| Change | Saving |
|---|---|
| Aux host removed: Prometheus + Grafana moved to loadgen, downstream mock moved to db | −$0.41/h |
| db `m7a.8xlarge` (32 cores / 128 GiB) → `c7a.4xlarge` (16 / 32 GiB) | −$1.03/h |
| loadgen `c7a.8xlarge` → `c7a.4xlarge` | −$0.82/h |
| All hosts on spot instead of on-demand (eu-north-1c), loadgen `c7a.4xlarge` → Graviton `c7g.4xlarge` | ≈ −$1.7/h (≈ $2.1/h → $0.38/h) |
| app `m7a.2xlarge` (8 / 32 GiB) → `c7a.xlarge` (4 / 8 GiB), db `c7a.4xlarge` → `c7a.xlarge`; DB-bound results accepted | ≈ −$0.14/h (→ $0.24/h) |
| Campaign ~15 h → ~10 h (shorter steps and smoke, 5% bisection, faster probe, fewer DB recreates; see [06](06-runner-and-results.md#estimated-duration)) | −5 h |
| Calibration + pilot 6 h → 4 h, buffer 14 h → 6 h | −10 h |
| No conformance or up-front image builds on AWS, calibration done manually beforehand, no pilot (2026-10-11) | −3 h (~20 h → ~17 h with the full buffer) |
| loadgen Graviton `c7g.4xlarge` → `c7a.4xlarge`: no Graviton spot capacity in eu-north-1 (2026-10-17) | +$0.04/h (→ $0.28/h) |
| One session per stack plus a common-load session instead of one long session (2026-10-17) | +3.5 h of provisioning, −3 h of buffer (an interruption reruns one pair, not a whole test) |

The app host (`c7a.xlarge`, 4 cores / 8 GB) is the hardware rule and the only host the results depend on. The loadgen is safe only because calibration proves its headroom before anything is scored, and the per-step CPU checks keep proving it during the run. The db is allowed to be the limit and is reported as such.

## Video outline (draft)

1. **Hook:** "Same server, 7 languages, 8 tests. Which one would you put in production?"
2. **Rules:** hardware, SLO, "realistic stacks" rule, and why the geometric mean is used (30 s)
3. **The setup:** AWS diagram, Grafana clips, which tests were DB-bound and for which stacks (the DB ceiling line, and the SQL statements and connections per request that make DB-limited stacks stop at different rates, [03](03-tech-stacks.md#database-work-per-request))
4. **Test by test:** 2 → 8, short chart per test, highlight surprises (test 3 ORM hydration, test 6 concurrency models, test 8 GC). Test 6 for Python: the limit is CPU spent in httpx (its cost per call and its connection-pool bookkeeping), not asyncio, which puts Python below PHP-FPM there ([03](03-tech-stacks.md#python-fastapi--sqlalchemy-2-async--uvicorn-workers))
5. **Category winners:** database, concurrency, CPU
6. **The real-app test (9, authenticated user sessions):** throughput, p99 at common load, memory
7. **Overall ranking** with the four components visible; cost per million requests chart
8. **Caveats and potential optimizations:** tease the tuned episode
9. **Call to action:** repo link, "open a PR if your stack is misconfigured"

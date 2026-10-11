# 08 · Roadmap

## Milestones

| # | Milestone | Deliverables | Done when |
|---|---|---|---|
| **M0** | Planning | This doc set, repo skeleton, `config/bench.toml`, `score.py` | Open decisions below are answered |
| **M1** | Shared foundation | `db/` schema + deterministic seed + local compose; `downstream/` mock; `conformance/` reference (splitmix64 datasets, expected outputs for tests 2–8, plus 401 cases for auth) + `check.py` | `check.py` passes against a reference implementation |
| **M2** | Seven stacks | `stacks/<name>/`: app, Dockerfile, `compose.yml`, `README.md` (versions, pool size, worker count, deviations) | All 7 pass conformance locally; versions pinned |
| **M3** | Load scripts | `k6/lib/` (SLO thresholds from config, settle/measure scenarios, payload loading); `k6/tests/01…09`; payload generator for test 7; JWT key pair + token generator for test 9 | Each script runs locally against any stack |
| **M4** | Runner | `runner/bench.py` (check, conformance, calibrate, run, resume, csv), Prometheus window queries, ethtool/docker checks, atomic result files, dry-run | Full dry run plus a real run against local compose |
| **M5** | Infra + monitoring | Terraform (VPC, one public subnet, security group, placement group, 3 hosts), host-setup scripts, Prometheus config, 3 Grafana dashboards | `terraform apply` → `bench.py check` green |
| **M6** | Calibration + pilot | `calibration.json`, sizing decisions, pilot run | Headroom proven; methodology tagged `methodology-v1` |
| **M7** | Scored campaign | `results/<campaign-id>/` complete, `scores.txt` | Publishing checklist done |
| **M8** | Video assets | Charts (per test, categories, overall, memory, cost per req), Grafana clips, B-roll of the dashboards | Charts generated from `results.csv` with a script (`scoring/charts.py`) |

M1 → M2 → M3 is the critical path. M4 and M5 can be built in parallel with M2.

## Open decisions

These are recommendations. Please confirm or change them **before M2** (the PHP and Go choices) and **before M6** (the measurement choices).

| # | Decision | Recommendation | Alternative |
|---|---|---|---|
| D1 | PHP runtime | **PHP-FPM + Nginx** in the main round (most deployed; Laravel's documented default) | Laravel Octane (FrankenPHP). Viewers will ask for it, so it's a good candidate for a bonus segment |
| D2 | Go stack | **Gin + GORM** (consistent "framework + ORM" rule) | stdlib `net/http` + `pgx` + `sqlc` (what many Go devs consider idiomatic) |
| D3 | Peak RSS window | **At common load** (same work for every stack); also report the peak during the search | During the max-rate search, as the guide literally says |
| D4 | AWS region | **eu-north-1** (cheapest spot for `c7a`/`m7a`/`c7g` as of 2026-10-10) | Any region with `m7a`/`c7a`/`c7g`; must not change mid-campaign |
| D5 | "Tuned" bonus round | Plan for it after the main video, using each stack's "potential optimizations" | Skip |
| D6 | Java concurrency | **MVC + virtual threads** (Spring's recommended path for new apps) | WebFlux + R2DBC |
| D7 | Downstream delay (test 6) | **50 ms fixed** (leaves 50 ms of the p95 budget) | 20 ms, or jitter (less deterministic) |
| D8 | Reported-only metrics ([04 Appendix A](04-methodology.md#appendix-a-candidate-reported-only-metrics-undecided)) | **Undecided**: startup time + idle memory, overload recovery, soak test, core scaling efficiency. Decide which to keep, and their parameters, before the freeze. | Drop any or all; none affect the scores |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| k6 can't generate the test 2 / test 6 rates for Rust or Go (test 1, the highest-rate test, was removed for this reason) | Max rate capped by the generator, not the app | Calibrate with monitoring running; resize loadgen to `c7g.8xlarge` (split across hosts only as a last resort); `dropped_iterations` check; a faster generator for the high-rate tests if k6 is the limit |
| Postgres saturates before the fastest stacks (expected on the 4-core db in tests 3–5) | DB-bound scores converge | **Accepted by design**: steps flagged `db_bound` instead of invalid, results marked "DB-limited", DB ceiling drawn on the charts and stated in the video ([04](04-methodology.md#when-postgres-is-the-bottleneck)). The runner flags such steps (`db_bound_tests` in `config/bench.toml`), `results.csv` carries a `db_bound` column and `score.py` lists DB-limited results. Charts (`scoring/charts.py`, M8) still have to draw the ceiling line |
| Network allowances on `c7a.xlarge` (burst, **~1.56 Gbps baseline**, half of the 8-core host the payloads were first sized for) | A network limit makes the step INVALID with no setup fix | Test 7 payload cut from 45 items (~12 KB) to 15 items (~4 KB, ~1.3 Gbps at 40k req/s); ethtool validity check; check test 2/6 packet rate (pps allowance) in calibration |
| Accusations of unfair config ("you didn't use X!") | Credibility | Selection rule + allowed-config table + "potential optimizations" lists published; each stack's README reviewed by someone fluent in it, if possible |
| Single run noise | Wrong order between close stacks | 5% tie rule; randomized order; test-major order |
| Long campaign (~10 h) interrupted | Wasted time and money | `resume`, atomic writes, tmux |
| Spot interruption (all hosts are spot) | Pause; hardware could change | Runner pauses and saves, continues only on identical hardware (type, CPU model, AMI, kernel); app host moved → current test rerun for all stacks ([06](06-runner-and-results.md#spot-interruptions)). **Runner support not implemented yet** (interruption polling, `paused.json`, fingerprint check in `resume`) |
| Monitoring (Prometheus + Grafana, public viewers) shares the loadgen host | Viewer bursts steal CPU from k6 | Grafana stopped during scored runs; Prometheus pinned to 2 cores, k6 to the other 14; loadgen CPU < 70% check; k6 calibrated with monitoring as in the campaign |
| Spot/AZ capacity for `c7a` / `c7g` | Can't provision, or long pauses | Pre-check spot placement scores and price history; pick the AZ with capacity for all three types |

## Budget estimate (eu-north-1c spot, 2026-10-11; spot prices move, check before each session)

| Host | Type | ~$/h (spot) |
|---|---|---|
| app | c7a.xlarge | 0.057 |
| db + downstream mock | c7a.xlarge | 0.057 |
| loadgen + Prometheus (+ Grafana outside scored runs) | c7g.4xlarge (Graviton) | 0.093 |
| 3 public IPv4 + 3 × 50 GB gp3 | | ~0.033 |
| **Total** | | **≈ $0.24/h** |

| Phase | Hours |
|---|---|
| Provision, deploy, seed, build images, conformance on AWS | ~1 |
| Calibration | ~1 |
| Pilot (2 stacks × 3 tests, plus the noise repeats from [09 R8](09-review-findings.md#r8-the-tie-threshold-is-not-grounded-in-measured-noise)) | ~2 |
| Scored campaign, one unattended session | ~10 |
| Reruns / buffer (`resume` reruns only the affected pairs; spot interruptions add reruns, see [06](06-runner-and-results.md#spot-interruptions)) | ~6 |
| **Total** | **~20 h ≈ $5** |

No NAT gateway, load balancer, provisioned IOPS or extra volumes. Development happens locally, and AWS is torn down between sessions. Hours while the campaign is paused for a spot interruption cost nothing for the stopped host, but the running hosts and the EBS volumes keep billing: stop the other two hosts too if a pause will be long.

**If calibration forces an upsize:** a `c7g.8xlarge` loadgen adds ~$0.12/h, spot, same date. Decided in calibration, before the scored run. The db is never upsized: a DB-bound result is reported, not fixed.

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

The app host (`c7a.xlarge`, 4 cores / 8 GB) is the hardware rule and the only host the results depend on. The loadgen is safe only because calibration proves its headroom before anything is scored, and the per-step CPU checks keep proving it during the run. The db is allowed to be the limit and is reported as such.

## Video outline (draft)

1. **Hook:** "Same server, 7 languages, 8 tests. Which one would you put in production?"
2. **Rules:** hardware, SLO, "realistic stacks" rule, and why the geometric mean is used (30 s)
3. **The setup:** AWS diagram, Grafana clips, which tests were DB-bound and for which stacks (the DB ceiling line)
4. **Test by test:** 2 → 8, short chart per test, highlight surprises (test 3 ORM hydration, test 6 concurrency models, test 8 GC)
5. **Category winners:** database, concurrency, CPU
6. **The real-app test (9, authenticated user sessions):** throughput, p99 at common load, memory
7. **Overall ranking** with the four components visible; cost per million requests chart
8. **Caveats and potential optimizations:** tease the tuned episode
9. **Call to action:** repo link, "open a PR if your stack is misconfigured"

# 08 · Roadmap

## Milestones

| # | Milestone | Deliverables | Done when |
|---|---|---|---|
| **M0** | Planning | This doc set, repo skeleton, `config/bench.toml`, `score.py` | Open decisions below are answered |
| **M1** | Shared foundation | `db/` schema + deterministic seed + local compose; `downstream/` mock; `conformance/` reference (splitmix64 datasets, expected outputs for tests 1–8, plus 401 cases for auth) + `check.py` | `check.py` passes against a reference implementation |
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
| D4 | AWS region | **us-east-1** (cheapest, best capacity for `*7a`) | Any region with `m7a`/`c7a`; must not change mid-campaign |
| D5 | "Tuned" bonus round | Plan for it after the main video, using each stack's "potential optimizations" | Skip |
| D6 | Java concurrency | **MVC + virtual threads** (Spring's recommended path for new apps) | WebFlux + R2DBC |
| D7 | Downstream delay (test 6) | **50 ms fixed** (leaves 50 ms of the p95 budget) | 20 ms, or jitter (less deterministic) |
| D8 | Reported-only metrics ([04 Appendix A](04-methodology.md#appendix-a-candidate-reported-only-metrics-undecided)) | **Undecided**: startup time + idle memory, overload recovery, soak test, core scaling efficiency. Decide which to keep, and their parameters, before the freeze. | Drop any or all; none affect the scores |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| k6 can't generate test 1 / test 8 rates for Rust or Go | Max rate capped by the generator, not the app | Calibrate with monitoring running; resize loadgen to `c7a.8xlarge` (split across hosts only as a last resort); `dropped_iterations` check |
| Postgres saturates before the fastest stacks (test 5 writes, test 4 joins) | DB-bound scores converge | 16-core db host, calibration ceiling, upsize rule (`c7a.8xlarge`); "DB-bound" note in the video |
| Network allowances on `m7a.2xlarge` (burst, ~3.1 Gbps baseline) | Throttling misattributed to the app | Payloads sized ≤ 15 KB; ethtool validity check |
| Accusations of unfair config ("you didn't use X!") | Credibility | Selection rule + allowed-config table + "potential optimizations" lists published; each stack's README reviewed by someone fluent in it, if possible |
| Single run noise | Wrong order between close stacks | 5% tie rule; randomized order; test-major order |
| Long campaign (~10 h) interrupted | Wasted time and money | `resume`, atomic writes, tmux, on-demand instances for the scored run (spot is fine for dev sessions) |
| Monitoring (Prometheus + Grafana, public viewers) shares the loadgen host | Viewer bursts steal CPU from k6 | Monitoring pinned to 2 cores, k6 to the other 14; loadgen CPU < 70% check; k6 calibrated with monitoring on |
| Spot/AZ capacity for `m7a` | Can't provision | Pre-check capacity; on-demand; capacity reservation for the campaign window |

## Budget estimate (us-east-1 on-demand, approximate; check current pricing)

| Host | Type | ~$/h |
|---|---|---|
| app | m7a.2xlarge | 0.46 |
| db + downstream mock | c7a.4xlarge | 0.82 |
| loadgen + Prometheus + Grafana | c7a.4xlarge | 0.82 |
| 3 public IPv4 + gp3 baseline EBS | | ~0.03 |
| **Total** | | **≈ $2.1/h** |

| Phase | Hours |
|---|---|
| Provision, deploy, seed, build images, conformance on AWS | ~1 |
| Calibration | ~1 |
| Pilot (2 stacks × 3 tests, plus the noise repeats from [09 R8](09-review-findings.md#r8-the-tie-threshold-is-not-grounded-in-measured-noise)) | ~2 |
| Scored campaign, one unattended session | ~10 |
| Reruns / buffer (`resume` reruns only the affected pairs) | ~6 |
| **Total** | **~20 h ≈ $42** |

No NAT gateway, load balancer, provisioned IOPS or extra volumes. Development happens locally, and AWS is torn down between sessions.

**If calibration forces an upsize**, each one adds $0.82/h: a `c7a.8xlarge` loadgen → ~$2.9/h (~$58 total); db as well → ~$3.8/h (~$75 total). Both are decided in calibration, before the scored run.

**What changed from the first estimate (35 h × $4.4/h ≈ $155):**

| Change | Saving |
|---|---|
| Aux host removed: Prometheus + Grafana moved to loadgen, downstream mock moved to db | −$0.41/h |
| db `m7a.8xlarge` (32 cores / 128 GiB) → `c7a.4xlarge` (16 / 32 GiB) | −$1.03/h |
| loadgen `c7a.8xlarge` → `c7a.4xlarge` | −$0.82/h |
| Campaign ~15 h → ~10 h (shorter steps and smoke, 5% bisection, faster probe, fewer DB recreates; see [06](06-runner-and-results.md#estimated-duration)) | −5 h |
| Calibration + pilot 6 h → 4 h, buffer 14 h → 6 h | −10 h |

The app host stays `m7a.2xlarge`: it is the hardware rule, and the only host the results depend on. The smaller db and loadgen hosts are safe only because calibration proves their headroom before anything is scored, and the per-step CPU checks keep proving it during the run.

## Video outline (draft)

1. **Hook:** "Same server, 7 languages, 9 tests. Which one would you put in production?"
2. **Rules:** hardware, SLO, "realistic stacks" rule, and why the geometric mean is used (30 s)
3. **The setup:** AWS diagram, Grafana live, proof the DB wasn't the bottleneck
4. **Test by test:** 1 → 8, short chart per test, highlight surprises (test 3 ORM hydration, test 6 concurrency models, test 8 GC)
5. **Category winners:** raw speed, database, concurrency, CPU
6. **The real-app test (9, authenticated user sessions):** throughput, p99 at common load, memory
7. **Overall ranking** with the four components visible; cost per million requests chart
8. **Caveats and potential optimizations:** tease the tuned episode
9. **Call to action:** repo link, "open a PR if your stack is misconfigured"

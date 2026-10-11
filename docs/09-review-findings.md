# 09 · Review findings (docs 00–05)

A review of [00](00-overview.md) to [05](05-scoring.md), plus `config/bench.toml` and `scoring/score.py`, done before the methodology freeze. Each finding has the problem, where it lives and a recommended fix. Concerns about how individual stacks are configured or implemented are **out of scope** here. Those are reviewed separately against the stack code.

**Status:** fixed: R2, R4, R5, R6, R10, R11, R12 and C1, C2, C3, C7, C9 (each marked below). The rest are open. When one is resolved, update the affected doc and mark it here (`fixed in <doc>` or `rejected: <reason>`).

## Summary

| ID | Finding | Severity | Decide by |
|---|---|---|---|
| [R1](#r1-test-9-latency-score-is-dominated-by-the-downstream-delay) | Test 9 latency score is dominated by the 50 ms downstream delay | High | M6 freeze |
| [R2](#r2-the-memory-metric-probably-misses-shared-memory) | The memory metric probably misses shared memory (PHP OPcache) (**fixed in 04: cgroup anon + shmem, confirmed that cAdvisor RSS has no shmem**) | High | M4 runner |
| [R3](#r3-tests-3-and-7-can-hit-the-app-hosts-network-limit) | Tests 3 and 7 can hit the app host's network limit | High | M3 load scripts |
| [R4](#r4-k6-json-output-cannot-run-at-high-request-rates) | k6 JSON output can't run at high request rates (**fixed in 04: probe as fixed-rate stages, no `--out json`, one loadgen**) | High | M4 runner |
| [R5](#r5-the-max_rps-search-rewards-noise) | The `max_rps` search rewards noise (**fixed in 04: both rules, `confirm_final`**) | High | M6 freeze |
| [R6](#r6-test-5-does-not-define-stock-decrement-semantics) | Test 5 doesn't define stock-decrement semantics (**fixed in 02**) | High | M1 conformance |
| [R7](#r7-connect-per-request-db-load-can-make-a-stack-permanently-invalid) | Connect-per-request DB load can make a stack permanently invalid | Medium | M6 calibration |
| [R8](#r8-the-tie-threshold-is-not-grounded-in-measured-noise) | The tie threshold isn't grounded in measured noise | Medium | M6 pilot |
| [R9](#r9-a-weak-pass-can-score-the-same-as-a-failure) | A weak pass can score the same as a failure | Medium | M6 freeze |
| [R10](#r10-test-9-connection-model-is-contradictory) | Test 9 connection model is contradictory (**fixed in 02**) | Medium | M3 load scripts |
| [R11](#r11-k6-url-tags-explode-metric-cardinality) | k6 URL tags explode metric cardinality (**fixed in k6/lib**) | Medium | M3 load scripts |
| [R12](#r12-the-k6-summary-does-not-isolate-the-measure-window-by-default) | The k6 summary doesn't isolate the measure window by default (**fixed in k6/lib and 06**) | Medium | M3 load scripts |
| [R13](#r13-connection-count-depends-on-latency-and-can-unbalance-workers) | Connection count depends on latency and can unbalance workers | Low | M6 pilot |
| [R14](#r14-l_common-is-set-by-the-slowest-stack) | `L_common` is set by the slowest stack | Low | M6 freeze |
| [C1–C9](#doc-consistency-fixes) | Typos, contradictions and stale text | Low | Before M2 |

---

## Measurement and scoring design

### R1. Test 9 latency score is dominated by the downstream delay

**Where:** [05 Step 4.1](05-scoring.md#41-latency-score), [04 Test 9 extras](04-methodology.md#test-9-extras-common-load-latency-and-memory)

**Problem:** downstream calls (test 6) are 14% of test 9 requests, and none of them can finish in under 50 ms. Both p95 and p99 over all requests therefore fall inside the downstream group:

- p95 of all requests ≈ the 64th percentile of downstream calls
- p99 of all requests ≈ the 93rd percentile of downstream calls

So `p99_common_ms` is roughly "50 ms + downstream overhead" for every stack. The latency score is compressed toward 100 (for example 50/55 → 91), and it says almost nothing about the other 86% of requests. A 0.15 weight is spent on a near-constant.

The SLO itself is less affected. A stack whose other endpoints slow down still breaks p95 once more than 5% of all requests exceed 100 ms. The downstream share also gets the same 50 ms p95 budget as test 6, which matches [D7](08-roadmap.md#open-decisions).

**Recommendation:**
1. Compute `p99_common_ms` over test 9 requests **excluding** the downstream endpoint (filter on the endpoint `name` tag, see R11). Use this value for the latency score.
2. Still record the all-requests p99 and the per-endpoint p99s in the results for analysis.
3. Keep the test 9 SLO on all requests (no change), and add one sentence to [02](02-test-spec.md#9--mixed-workload-user-sessions-authenticated) explaining that p95 and p99 sit inside the downstream share.

Alternative: score the geometric mean of per-endpoint p99s. It is more informative but harder to explain on camera.

### R2. The memory metric probably misses shared memory

**Where:** [04 Test 9 extras](04-methodology.md#test-9-extras-common-load-latency-and-memory), [02 Appendix A](02-test-spec.md#appendix-a-php-in-memory-dataset)

**Problem:** on cgroup v2 (Ubuntu 24.04's default), cAdvisor's `container_memory_rss` is the cgroup's `anon` counter. Shared anonymous memory, such as PHP's OPcache segment, is accounted as `shmem`, not `anon`. If that holds, the claim "the shared OPcache segment counts once in the container's memory" is false: it counts zero times, which favors PHP. This needs verifying on the real host before relying on it.

Two smaller wording issues:
- 04 says the metric is "the sum across all processes in the container". cgroup accounting counts each page once, which is better than summing per-process RSS (no double counting of shared pages). The wording should say that.
- If a stack runs as more than one container (for example Nginx and PHP-FPM separately), `name="app"` covers only one of them.

**Recommendation:**
1. Measure memory as `anon + shmem` from the app cgroup's `memory.stat`, sampled every 1 s by the runner (or exported through a `node_exporter` textfile collector). Keep cAdvisor RSS as a cross-check.
2. Verify during M2 with the PHP container: OPcache's footprint must show up in the chosen metric.
3. Sum all containers belonging to a stack, or require one container per stack.
4. Fix the wording in 04 and 02 Appendix A to match whatever is measured.

### R3. Tests 3 and 7 can hit the app host's network limit

**Where:** [01 Network checks](01-architecture.md#instances), [02 tests 3 and 7](02-test-spec.md#3--large-result-set-get-categoriesidproductsafterafter_id), [08 Risks](08-roadmap.md#risks)

**Problem:** the docs size payloads by **response** size only, assuming the best stack does about 20k req/s. Two flows are missed:

| Flow | Estimate | `m7a.2xlarge` baseline |
|---|---|---|
| Test 3, DB → app: 100 rows with `description`, about 200 bytes per row on the wire, at 20k req/s | ≈ 3.2 Gbps inbound | ≈ 3.1 Gbps |
| Test 7, k6 → app: 12 KB request at 40k req/s (plausible for Rust or Go) | ≈ 3.9 Gbps inbound | ≈ 3.1 Gbps |

When the limit is hit, the ethtool check marks the run **invalid**, but no setup fix exists: the app instance type is fixed by the hardware rule. There is a second effect. `m7a.2xlarge` bursts up to 12.5 Gbps on network credits, so an early stack in the run order can pass on burst credits while a later one gets throttled. That makes results depend on run order.

01 also says "the payloads in tests 7 and 8 are sized to stay well below" the baseline. Test 8 has a tiny payload. It should say tests 3 and 7, and "well below" isn't accurate at the stated rates.

**Recommendation:**
1. Size payloads so that the expected best stack stays under about **50% of the baseline in each direction**, including DB → app traffic. Concretely: test 3 page size 100 → 50, and the test 7 request about 12 KB → 4 KB (20 line items). Response sizes in 02 change accordingly.
2. In calibration, run `iperf3` app ↔ loadgen and app ↔ db for 10 minutes in each direction to measure the **sustained** (post-burst) ceiling, and record it in `calibration.json`.
3. Add a status `net_bound`: if a stack's steps keep failing only on the ethtool check, its `max_rps` is the highest step that passed without allowance increases, flagged in charts as "network-bound, lower bound". This is the fallback, not the plan.
4. Fix the 01 sentence and the 08 risk row ("Payloads sized ≤ 15 KB").

### R4. k6 JSON output cannot run at high request rates

**Where:** [04 Stress probe](04-methodology.md#3-stress-probe-aiming-not-scoring), [04 Proving the load generator is not the bottleneck](04-methodology.md#proving-the-load-generator-is-not-the-bottleneck)

**Problem:** two parts of the method depend on `k6 --out json`: the probe's per-second windows and the multi-host merge of latency percentiles (`runner/merge.py`). The JSON output writes one line per metric sample, roughly ten per request. At 100k+ req/s that is over a million lines per second, which will saturate the loadgen CPU. During the probe this biases E low without anyone noticing, because dropped iterations are allowed there. For scored multi-host steps it fails the loadgen CPU check.

**Recommendation:**
1. Prefer **one larger loadgen** (`c7a.16xlarge` or `c7a.24xlarge`) over splitting across hosts. A single host keeps the end-of-test summary exact and needs no merge.
2. Build the probe windows from the Prometheus remote-write stream (already running), with `K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM=true`. The probe isn't scored, so approximate percentiles are fine.
3. If multiple loadgen hosts become unavoidable, merge native histograms and state that the percentiles are approximate, or write an xk6 output that dumps an HDR histogram per host.
4. Calibrate k6's ceiling with **exactly** the outputs used in the campaign switched on.

### R5. The `max_rps` search rewards noise

**Where:** [04 Load steps](04-methodology.md#4-load-steps-scored)

**Problem:** `max_rps = highest passing rate` assumes passes and failures are monotonic in rate. With noise, a step can pass above a rate that already failed. Taking the highest pass then rewards a lucky step. With only one run per test, nothing catches it.

**Recommendation:**
1. Define `max_rps` as the **highest passing rate below the lowest failing rate**. Passes above a failure are recorded in the step log but not used.
2. **Confirm the result:** after bisection, re-run the final passing rate once (one more 60 s step). If it fails, step down one bisection interval and confirm again. Record `confirmed: true/false`.
3. Add both rules to [04](04-methodology.md#4-load-steps-scored) and to the `[search]` section of `bench.toml`.

### R6. Test 5 does not define stock-decrement semantics

**Status:** fixed in [02 test 5](02-test-spec.md#5--write-transaction-post-orders): all six recommendations adopted; `conformance/check.py` includes the concurrency check.

**Where:** [02 test 5](02-test-spec.md#5--write-transaction-post-orders)

**Problem:** "decrement `products.stock`" can be implemented in ways that differ in correctness and cost:

- **Load, modify, save** (ORM dirty checking): loses updates under concurrency unless locked.
- **Pessimistic lock** (`SELECT … FOR UPDATE`): correct, slower.
- **Atomic update** (`SET stock = stock - :q`): correct and cheap.

Comparing stacks that picked different options is unfair, and some options are wrong. Separately, two transactions touching the same products in different orders can **deadlock**. With 100k products this is rare but not zero at thousands of transactions per second. Several response fields are also unspecified: `status`, `created_at` and how `total_cents` is computed.

**Recommendation:** add to 02:
1. Stock is decremented with an **atomic update expression** through the ORM's update API (`UPDATE products SET stock = stock - :q WHERE id = :id`). No row locks beyond what that statement takes.
2. Items are processed in **ascending `product_id` order**, so lock order is consistent and deadlocks can't happen.
3. Prices come from a plain read in the same transaction.
4. The new order has `status = "pending"`, `created_at = now()` and `total_cents = Σ unit_price_cents × quantity`. Conformance ignores `id` and `created_at` values but checks their types.
5. k6 generates distinct product ids per order (duplicates are a 422 by the validation rules).
6. Conformance adds a concurrency check: N parallel orders on the same product must decrease its stock by exactly the ordered total.

### R7. Connect-per-request DB load can make a stack permanently invalid

**Where:** [04 SLO table](04-methodology.md#slo-pass-criteria-for-one-measurement-step), [04 When Postgres is the bottleneck](04-methodology.md#when-postgres-is-the-bottleneck)

**Problem:** a stack that opens a new Postgres connection per request (the PHP default) makes Postgres start a new backend and run SCRAM authentication for every request. That cost lands on the db host. If it pushes db CPU over 70%, every step is **invalid**, and the rule "fix the setup and rerun" has no fix, because the behavior belongs to the stack. Calibration uses pgbench with 64 persistent clients, so it never measures this.

**Recommendation:**
1. Treat connection setup as **the stack's cost**, not a setup fault. The db host must still have headroom for it.
2. In calibration, also run `pgbench -C` (new connection per transaction) at the rate the connect-per-request stack is expected to reach, and size the db host so it stays under 70%.
3. Use the same Postgres config (`max_connections`, auth method) for every stack, and state it in 04.

### R8. The tie threshold is not grounded in measured noise

**Where:** [04 Known limitations](04-methodology.md#known-limitations-stated-in-the-video), [05 Interpreting differences](05-scoring.md#interpreting-differences)

**Problem:** the 5% tie threshold is chosen, not measured. Bisection alone has 5% resolution (raised from 3% to shorten the campaign), and run-to-run variation on cloud hosts can exceed 5%. It is also unclear which numbers a tie applies to: per-test scores, composite, category or overall.

**Recommendation:**
1. In the pilot, repeat a small matrix three times: one JIT stack and one compiled stack, on tests 1, 3 and 9.
2. Set `tie_threshold = max(5%, 2 × the largest observed spread)` before the freeze, and publish the pilot spread.
3. State in 05 that ties apply to each per-test score and to the overall score, judged separately.

### R9. A weak pass can score the same as a failure

**Where:** [05 Step 2](05-scoring.md#step-2-normalize-each-test-to-a-score-from-1-to-100), `scoring/score.py`

**Problem:** passing scores are clamped at a minimum of 1, which is also the failure score. In test 1, a slow stack can plausibly reach less than 1% of the best stack's rate. Its real result would then score exactly like a failure, and the charts would show different things for the same number.

**Recommendation:**
1. Remove the lower clamp for **passing** results. Any positive score works in a geometric mean.
2. Score a failure as if the stack passed at **half the lowest step rate** (50 req/s with `lowest_rate = 100`): `S_fail = 100 × 50 / best`. A failure then always scores below any possible pass, and the penalty is derived from the method instead of picked.
3. For latency and memory, keep the failure score at 1, since ratios below 1% are unrealistic there, and say so in 05.
4. Update `failure_score` in `bench.toml`, `score.py` and the worked example in 05.

### R10. Test 9 connection model is contradictory

**Where:** [02 test 9](02-test-spec.md#9--mixed-workload-user-sessions-authenticated)

**Problem:** 02 says a session sends its requests "on one keep-alive connection", and later that "each session still uses its own connection". k6 reuses a VU's connection across iterations by default, so sessions share connections unless `noVUConnectionReuse` is set. A new connection per session adds a TCP handshake per session, thousands per second at high rates. That changes the work done, and it differs from tests 1–8.

**Recommendation:** keep the k6 default (connections reused across sessions, as in tests 1–8). Rewrite both sentences in 02 to say that a session's requests go over a keep-alive connection that may be reused by later sessions, and that per-session connection setup isn't measured.

### R11. k6 URL tags explode metric cardinality

**Where:** [01 Monitoring](01-architecture.md#monitoring), [02 Endpoints](02-test-spec.md#endpoints)

**Problem:** k6 tags each request with its URL by default. With random ids (`/products/48213`), every id becomes its own time series in the Prometheus remote-write output and its own entry in k6's internal tag sets. That adds load on the loadgen, which also runs Prometheus, so the extra series cost k6 CPU twice.

**Recommendation:**
1. Give every request a fixed `name` tag (`/products/{id}`), for example with k6's `http.url` tagged template or explicit `tags: { name: … }`. Put this in a shared helper in `k6/lib/`.
2. Limit `systemTags` to the tags actually used (`name`, `method`, `status`, `scenario`, `expected_response`, plus `phase`).
3. Verify the series count in Prometheus during the pilot.

### R12. The k6 summary does not isolate the measure window by default

**Where:** [01 Monitoring](01-architecture.md#monitoring), [04 Load steps](04-methodology.md#4-load-steps-scored)

**Problem:** the scoring relies on k6's end-of-test summary, but that summary covers the whole run, settle included. Tagged sub-metrics (`phase:measure`) only appear in the summary when a threshold references them. `http_reqs` rate is computed over the whole test duration, not the measure window, which matters for test 9's "measured request rate".

**Recommendation:**
1. Define thresholds on `http_req_duration{phase:measure}`, `http_req_failed{phase:measure}` and `dropped_iterations{scenario:measure}`, all with `abortOnFail: false`, so they appear in the summary.
2. Compute the measured rate as `http_reqs{phase:measure}.count / measure_s`.
3. Document this in 04 so the scoring path is reproducible from the published raw files.

### R13. Connection count depends on latency and can unbalance workers

**Where:** [04 Fairness rules](04-methodology.md#fairness-rules)

**Problem:** under `constant-arrival-rate`, the number of busy VUs, and therefore open connections, follows Little's law (rate × latency). A fast stack may see only a few dozen long-lived connections. Multi-process stacks (Node cluster, Uvicorn workers, PHP-FPM behind Nginx) distribute work per connection, so few connections can spread load unevenly across processes and understate their throughput.

**Recommendation:** measure first, then decide.
1. Record per step: established connections on port 8080 (`ss`) and per-process CPU on the app host.
2. If the pilot shows a worker imbalance above about 20% for any multi-process stack, set a common minimum number of connections for every stack, for example by pre-allocating VUs and spreading iterations across them.

### R14. `L_common` is set by the slowest stack

**Where:** [05 Step 4.1](05-scoring.md#41-latency-score)

**Problem:** `L_common = 0.8 × the lowest passing max_rps`. If one stack is far slower, fast stacks run nearly idle during the latency and memory measurement, and the slowest stack runs at 80% of its own capacity. The method is defensible ("same work"), but the result depends heavily on which stack is the slowest.

**Recommendation:** keep the scored definition. Add a **reported-only** second point at `0.8 × the median max_rps` for stacks that can reach it, and state the dependency in the video.

---

## Doc consistency fixes

| ID | Where | Fix |
|---|---|---|
| C1 | [02](02-test-spec.md#9--mixed-workload-user-sessions-authenticated) | ~~Delete the stray `fgr` line under the test 9 heading.~~ Fixed. |
| C2 | [04 Status values](04-methodology.md#parameters) | Remove the Grafana "Plain HTTP…" paragraph pasted into the status list. It duplicates [01 Public Grafana](01-architecture.md#public-grafana-live-monitoring). **Fixed.** |
| C3 | [04 Finding max_rps](04-methodology.md#finding-max_rps-one-search-per-stack--test) | "Five phases" introduces four. Say "four phases after a reset", or list reset as a phase. **Fixed.** |
| C4 | [05 Step 1](05-scoring.md#step-1-measure-each-test) | ~~"Two more validity checks" should be three: db, loadgen **and aux** CPU < 70%.~~ Rejected: the aux host was removed (monitoring moved to loadgen, downstream mock to db), so two checks is correct again. |
| C5 | [05 Step 1](05-scoring.md#step-1-measure-each-test) | `max_rps` is defined as the offered rate, but 02 and 04 use the measured request rate for test 9. State both cases. |
| C6 | [04 Status values](04-methodology.md#parameters), [05 Step 1](05-scoring.md#step-1-measure-each-test) | Say explicitly that `crash` and `oom` are final statuses only when no step passed. Define what happens if a stack crashes or OOMs during the common-load run (recommended: latency and memory score as failures, `max_rps` unchanged). |
| C7 | `scoring/score.py` | Weights and failure score are hardcoded, and `tie_threshold` is unused. Read them from `config/bench.toml` so the frozen config is the single source of truth. **Fixed: score.py reads [scoring] and prints ties.** |
| C8 | [01 Network checks](01-architecture.md#instances), [08 Risks](08-roadmap.md#risks) | "Tests 7 and 8" should be tests 3 and 7, and the sizing claim needs updating per R3. |
| C9 | [04 Test 9 extras](04-methodology.md#test-9-extras-common-load-latency-and-memory), [02 Appendix A](02-test-spec.md#appendix-a-php-in-memory-dataset) | Update the memory-accounting wording to match the metric chosen in R2. **Fixed with R2.** |

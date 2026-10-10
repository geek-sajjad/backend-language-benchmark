# 05 · Scoring guide

This guide defines how raw k6 and monitoring results turn into a final score for each stack. It is published with the results so anyone can recompute every number. The reference implementation is [`scoring/score.py`](../scoring/score.py):

```bash
python3 scoring/score.py results/<campaign-id>/results.csv
```

**Test set:** 9 tests. Tests 1–8 are single workloads; test 9 is the authenticated mixed workload ([02-test-spec.md](02-test-spec.md)). An earlier draft had 10 tests: template rendering was removed, and "multiple queries" was replaced by "large result set".

**Setup used for all results:**

- **Hardware:** one config only, 8 dedicated CPU cores / 32 GB RAM (`m7a.2xlarge`). All stacks run on the same instance type.
- **Runs:** each test is run **once** per stack.
- **Stacks:** one stack per language (for example NestJS for Node, Laravel for PHP). The stack's score is also its language's score.

The calculation has five steps:

1. Measure each test: max req/s under the SLO
2. Normalize: score each test from 1 to 100
3. Combine tests: composite and category scores
4. Add latency and memory scores
5. Overall score per stack

---

## Step 1: Measure each test

For every stack and test, record:

| Field | Meaning |
|---|---|
| `max_rps` | Highest offered rate (req/s) at which **all** SLO thresholds pass |
| `status` | `ok`, `oom`, `crash`, or `slo_fail` (SLO not met even at the lowest step) |
| `p99_common_ms` | p99 latency at the **common load** (test 9 only, see Step 4) |
| `peak_rss_mb` | Peak memory of the app under load (test 9 only) |

The test result is simply the measured value:

```
R(stack, test) = max_rps
```

A test passes the SLO when, for the whole measurement window:

- p95 latency < 100 ms
- p99 latency < 250 ms
- error rate < 1%
- k6 `dropped_iterations` = 0 (otherwise the load generator failed, not the app)
- no network allowance counters increased (`ethtool -S`)

If the k6 or network check fails, the run is invalid. Fix the setup and rerun; don't record it as a failure of the stack. (This project adds two more validity checks, db and loadgen CPU < 70%; see [04-methodology.md](04-methodology.md).)

If the status is `oom`, `crash`, or `slo_fail`, the test result is a **failure**.

The SLO numbers are fixed in `config/bench.toml` **before** the first run and use the same values for every stack.

**Because there is only one run per test**, there is no way to detect noise from repeated measurements. Conditions are kept identical for every stack: same instance type with dedicated CPU, same warm-up, nothing else running on the app or load-generator machines.

---

## Step 2: Normalize each test to a score from 1 to 100

Raw req/s can't be combined across tests (JSON hello might reach 200,000 req/s while the slow downstream test reaches 5,000). Score each stack relative to the best stack in that test:

```
S(stack, test) = 100 × R(stack, test) / max over all stacks of R(stack, test)
```

- The best stack in each test scores **100**.
- A stack with half the best throughput scores **50**.
- A failed test scores **1** (the failure score). The minimum score is also 1, so a stack never gets 0.

Why 1 and not 0: the geometric mean in Step 3 multiplies scores, so a single 0 would make the whole composite 0 and erase every other result. A score of 1 is a heavy but finite penalty. Failures are always marked visibly in charts as well.

---

## Step 3: Combine tests

### 3.1 Composite throughput score (tests 1–8)

The **geometric mean** of the test scores:

```
Composite(stack) = ( S1 × S2 × ... × S8 ) ^ (1/8)
```

Equivalent form, easier to compute and extend with weights:

```
Composite(stack) = exp( Σ wᵢ × ln(Sᵢ) / Σ wᵢ )      (all wᵢ = 1 by default)
```

**Why the geometric mean:**

- It is the standard method for combining ratio-based benchmark scores (SPEC uses it).
- One extreme test can't dominate the total.
- It penalizes collapsing in a single test, where a median or an arithmetic mean would hide the collapse.

Example of why the median is wrong for this step:

| | Test A | Test B | Test C | Median | Arithmetic | Geometric |
|---|---|---|---|---|---|---|
| Stack 1 | 100 | 100 | 10 | 100 | 70 | **46** |
| Stack 2 | 60 | 60 | 60 | 60 | 60 | **60** |

Stack 1 collapses in test C. The median ranks it first anyway; the geometric mean correctly ranks the consistent stack higher.

Test 9 is **excluded** from the composite. It already mixes the other workloads, so including it would count them twice. It gets its own score in 3.3.

### 3.2 Category scores

Same formula (geometric mean), over a subset of tests:

| Category | Tests |
|---|---|
| Raw speed | 1, 7, 8 |
| Database | 2, 3, 4, 5 |
| Concurrency and waiting | 6 |
| CPU | 7, 8 |

These scores drive the "category winners" segment of the video.

### 3.3 Mixed workload score (test 9)

```
Mixed(stack) = S(stack, test 9)
```

This is the headline "real app" result. It is reported next to the composite, never hidden inside it.

---

## Step 4: Latency and memory scores

Both are measured during test 9 so they reflect the realistic workload. For these metrics, **lower is better**, so the ratio is inverted:

```
Score = 100 × best value / stack value
```

### 4.1 Latency score

Comparing p99 at each stack's own max rate is misleading, because fast stacks carry far more traffic at that point. All stacks are measured at one **common load** instead:

```
L_common = 0.8 × the lowest passing max_rps in test 9 (among stacks that passed)
```

Each stack runs at `L_common` for the same duration (120 s) and its p99 is recorded.

```
Latency(stack) = 100 × min(p99 of all stacks) / p99(stack)
```

A stack that failed test 9 gets the failure score 1.

### 4.2 Memory score

Uses the peak RSS under load during test 9 (measured during the common-load run; see the decision note in [04-methodology.md](04-methodology.md)):

```
Memory(stack) = 100 × min(peak_rss of all stacks) / peak_rss(stack)
```

A stack that failed test 9 (including being killed by the OOM killer) gets the failure score 1.

### 4.3 Why there is no cost score here

Every stack runs on the same instance at the same price. Requests per dollar is then exactly proportional to req/s, so a cost score would just repeat the throughput score. Cost is shown in its own chart (requests per dollar, or instances needed for a target load) using the measured req/s and the instance price, but it stays out of the formula.

---

## Step 5: Overall score

A **weighted** geometric mean of the four main components:

```
Overall(stack) = exp( Σ wₖ × ln(Componentₖ) / Σ wₖ )
```

| Component | Default weight | Reason |
|---|---|---|
| Mixed workload | 0.35 | Closest to a real app |
| Composite (tests 1–8) | 0.35 | Breadth across workloads |
| Latency | 0.15 | User-facing responsiveness |
| Memory | 0.15 | Determines how many instances fit on one host |

The weights are a judgment call. They are published, fixed before running, and the four components are always shown next to the overall score so viewers can see what drives it.

Since each language has exactly one stack, the language score is that stack's Overall score. It is always shown with the stack name, for example "Node: 38.7 (NestJS)".

---

## Worked example

Three stacks, with three composite tests to keep it short.

**Step 2: normalize**

| Stack | Test A req/s | Score | Test B req/s | Score | Test C req/s | Score |
|---|---|---|---|---|---|---|
| Go (Gin) | 50,000 | 100 | 8,000 | 100 | 3,000 | 90.9 |
| Node (NestJS) | 20,000 | 40 | 6,000 | 75 | 3,300 | 100 |
| PHP (Laravel) | 10,000 | 20 | 1,000 | 12.5 | failed (SLO) | 1 |

**Step 3: composite (geometric mean)**

- Go: (100 × 100 × 90.9)^(1/3) = **96.9**
- Node: (40 × 75 × 100)^(1/3) = **66.9**
- PHP: (20 × 12.5 × 1)^(1/3) = **6.3**, flagged: failed test C

**Step 5: overall, for Go**

With Mixed = 80, Composite = 96.9, Latency = 90, Memory = 50:

```
Overall = exp( (0.35·ln 80 + 0.35·ln 96.9 + 0.15·ln 90 + 0.15·ln 50) / 1.0 ) = 81.1
```

---

## Interpreting differences

- **Within 5% = tie.** With a single run per test there is no measured run-to-run spread, so any difference under 5% is treated as noise. Such results are shown as ties rather than ranking one stack above the other.
- **Scores are relative.** A score of 50 means "half the best stack," not "50% good." Adding or removing a stack can change everyone's scores, so the full list of stacks is published with the results.
- **DB-bound results:** if several stacks reach the measured Postgres ceiling, their scores in tests 2–5 converge. This is noted in the video instead of being presented as a tie between languages.

---

## Input format (`results.csv`)

```
stack,language,test,max_rps,status,p99_common_ms,peak_rss_mb
gin,go,2,61200,ok,,
gin,go,9,30150,ok,41.2,118
laravel,php,6,,slo_fail,,
```

`p99_common_ms` and `peak_rss_mb` are only filled for test 9 rows. The runner writes this file. A tiny sample is in `scoring/example-results.csv`.

---

## Checklist before publishing results

- [ ] SLO thresholds, failure score, and weights were fixed before the first run (git tag `methodology-v1`)
- [ ] Every stack has one valid result for each of tests 1–9
- [ ] All tests ran on the same 8-core / 32 GB dedicated-CPU instance type, under identical conditions
- [ ] No run had dropped k6 iterations or exceeded network allowances
- [ ] Failures are marked in every chart, not shown as zero
- [ ] Each language score names the stack it came from
- [ ] Raw CSV, this guide, and `score.py` are published in the repo

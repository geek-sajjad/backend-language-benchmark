# db sizing test: c7a.xlarge (4 cores) vs Rust on 8 cores

Generated 2026-10-11 07:37 UTC by `infra/db-sizing/summarize.py`. Plan: [docs/tmp-db-sizing-plan.md](../../docs/tmp-db-sizing-plan.md).

| Test | db_max_4c (req/s) | rust_est_8c (req/s) | ratio | db limited by | PG CPU/req on m7a | PG cores needed at rust_est_8c | ratio on 16 cores if linear |
|---|---|---|---|---|---|---|---|
| 2 | 129621 (r01, 64 clients) | 98370 | **1.32×** | CPU (100%) | 62 µs | 6.1 | ≤ 5.27× |
| 3 | 7074 (r03, 64 clients) | 30592 | **0.23×** | CPU (100%) | 621 µs | 19.0 | ≤ 0.92× |
| 4 | 15090 (r05, 64 clients) | 39305 | **0.38×** | CPU (100%) | 337 µs | 13.2 | ≤ 1.54× |
| 5 | 8956 (r10, 64 clients) | 33134 | **0.27×** | CPU (99%) | 505 µs | 16.7 | ≤ 1.08× |

Lowest ratio: test 3, 0.23× → **< 1.5×, CPU-limited: keep c7a.4xlarge.** **But even 16 cores fails the rule on test 3, 5** (last column): the plan's assumption that today's db has 1.5× headroom over Rust does not hold for those tests.

The last column scales the 4-core maximum linearly to 16 cores (today's c7a.4xlarge). That is an upper bound (Postgres doesn't scale perfectly, and test 3 would also need about 4× the network), so any value below 1.5× there means the current db host fails the rule too.

## PHP note: new connection per transaction (`-C`)

Test 2 with `-C`: **470 TPS** (db CPU 60.8%) vs 129621 with a 64-connection pool (0.36%), average latency 65.862 ms, client CPU 43.0%. Neither side's CPU is full, so connection setup itself (backend fork + scram-sha-256 auth) is the limit: a stack that connects per request is capped near this on any db test.

## Notes

- Noise, test 2 at 64 clients (runs 1, 8, 9): 129621, 127133, 128530 TPS; spread 1.9% of the mean.
- Runs marked lower bound (client CPU > 70%): none.
- Test 3 network: 20.3 kB per page, 1.15 Gbps at 7074 TPS = 73% of the 1.562 Gbps baseline (cap 9635 TPS); no allowance-exceeded events. CPU ran out first.
- Ramp cross-check not usable for test 2 (k6), test 3 (postgres), test 4 (postgres), test 5 (postgres): in phase 1 the ramp hit the named resource (k6 on 2 cores, or Postgres on 2 cores) before Rust, so it says nothing about Rust's ceiling.
- Rust's 8-core figure is CPU per request on 4 pinned cores × 8 × 0.85, not a measurement on 8 cores.

## Cost

- phase1 (failed attempt) rust: m7a.2xlarge spot $0.2197/h, 16 min → $0.060 (incl. IPv4 + 20 GB gp3)
- phase1 rust: m7a.2xlarge spot $0.2197/h, 12 min → $0.046 (incl. IPv4 + 20 GB gp3)
- phase2 (failed attempt) db: c7a.xlarge spot $0.0848/h, 3 min → $0.005 (incl. IPv4 + 20 GB gp3)
- phase2 (failed attempt) client: c7a.xlarge spot $0.0848/h, 3 min → $0.004 (incl. IPv4 + 20 GB gp3)
- phase2 db: c7a.xlarge spot $0.0848/h, 8 min → $0.013 (incl. IPv4 + 20 GB gp3)
- phase2 client: c7a.xlarge spot $0.0848/h, 8 min → $0.012 (incl. IPv4 + 20 GB gp3)
- **Total ≈ $0.14**

# TEMPORARY: 4-core db sizing test, execution plan

> Written for an AI agent that executes the test. The user approved the plan; see "Approval gates" for what still needs their explicit go.

## Goal

Decide whether the db host can drop from `c7a.4xlarge` (16 cores / 32 GiB) to **`c7a.xlarge` (4 cores / 8 GiB)** without limiting any stack's results.

Rule ([04-methodology.md](04-methodology.md#proving-postgres-is-not-the-bottleneck)): for each of tests 2–5,
**ratio = db max on 4 cores ÷ Rust's max on 8 cores ≥ 1.5**.

Both sides count the same work: each `runner/pgbench/test{N}.sql` script is exactly the SQL that one rust-axum request to test N runs, so pgbench TPS and Rust req/s are directly comparable.

## Hard constraints

- **Region `us-east-1`. The account quota is 8 vCPUs at a time** for both spot and on-demand (codes `L-34B43A08` and `L-1216C47A`). That's why there are two phases, run **one after the other**: phase 2 may start only after AWS reports every phase 1 instance as `terminated`.
- **Spot only**, one-time requests, `InstanceInterruptionBehavior=terminate`.
- **Never touch** the Terraform lab in `infra/terraform/`. Its region (eu-north-1) is for the real campaign. Create everything here with the AWS CLI and tag it `Project=db-sizing`.
- **Every instance self-destructs:** user-data runs `shutdown -h +60` and launch sets `--instance-initiated-shutdown-behavior terminate`. Nothing may live past 60 minutes.
- **Teardown always runs:** each phase script sets `trap teardown EXIT`, so the instances are terminated even if a step fails.
- **Commit and push:** commit nothing unless the user asks; never push.

## Approval gates

1. **Write the scripts, no AWS calls:** create the scripts below in `infra/db-sizing/`, show them to the user, and **wait for an explicit go**.
2. **Read-only checks:** before launching, run the pre-flight checks below (all read-only).
3. **Phase 1:** run it only after the user's go.
4. **Phase 2:** report phase 1's numbers, then **ask before starting phase 2**.

## Known facts (checked 2026-10-10)

| Item | Value |
|---|---|
| Default VPC (us-east-1) | `vpc-06398f75f7d4ef5a0` |
| Default subnet, us-east-1c | `subnet-0d925e710ad3ffd5c` |
| c7a.xlarge | 4 real cores (no SMT), 8 GiB, network baseline 1.562 Gbps (burst 12.5), EBS baseline 156 MB/s. Spot in us-east-1c: $0.0851/h. |
| m7a.2xlarge | 8 real cores, 32 GiB, network baseline 3.125 Gbps. Spot in us-east-1: ~$0.205/h (the cheapest, us-east-1a); price for us-east-1c not checked. |
| AMI | Ubuntu 24.04 amd64, from SSM parameter `/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id` |
| SSH key | `~/.ssh/id_ed25519.pub` (user `ubuntu`) |

### Pre-flight checks (read-only)

- **Instance types:** `aws ec2 describe-instance-type-offerings --region us-east-1 --location-type availability-zone --filters Name=location,Values=us-east-1c` must list both `m7a.2xlarge` and `c7a.xlarge`. If either is missing, pick an AZ that has both, look up that AZ's default subnet, and tell the user.
- **Prices:** check the current spot prices for both types in the chosen AZ. If either is above 2× the values above, stop and ask the user.
- **Nothing left over:** confirm no instance tagged `Project=db-sizing` is already running.

## Repo facts the scripts rely on

- **`db/compose.yml`**
  - Runs `postgres:18.6` with host networking, user/password/db `bench`.
  - Memory comes from the env vars `PG_SHARED_BUFFERS` and `PG_EFFECTIVE_CACHE_SIZE`; everything else is in `db/postgresql.conf`.
  - The first start seeds `bench_template` (1M orders, ~4M items; about 1 min on a laptop, allow up to 5 min), then creates `bench`. Wait until `pg_isready` answers **over TCP** on 127.0.0.1, not just the socket.
- **`db/reset.sql`:** recreates `bench` from the template. Run it as `PGPASSWORD=bench psql -h 127.0.0.1 -U bench -d postgres -f db/reset.sql`.
- **`stacks/rust-axum/compose.yml`**
  - Builds `bench-rust-axum:local`, container name `app`, port 8080.
  - Env: `DB_HOST` (default 127.0.0.1), `DB_POOL_SIZE` (default 64), `WORKERS` (empty = one Tokio worker per core).
  - Mounts `k6/data/jwt/public.pem`, so copy that file too.
  - `GET /health` returns 200 when it's ready.
- **`conformance/check.py [base_url]`:** must pass before measuring. Use `--skip-writes` only if test 5 setup is the problem, and say so.
- **k6 tests** are in `k6/tests/02-single-read.js`, `03-large-result.js`, `04-list-join.js` and `05-write.js`.
  - Parameters come in as `-e` variables: `BASE_URL`, `RATE` (iterations/s), `SETTLE_S`, `MEASURE_S`, `SUMMARY_FILE` (the JSON summary is written there), and `MODE=warmup` with `FROM_RATE`/`TO_RATE`/`DURATION_S`.
  - See `k6/README.md`.
- **pgbench scripts:** `runner/pgbench/test2.sql` … `test5.sql`. `runner/benchlib/calibrate.py` shows the exact pgbench invocation the campaign uses (run from the `postgres:18.6` image, `-n -M prepared`).
- **`infra/host-setup/bootstrap.sh <role>`** is the lab's host setup: Docker CE + compose, sysstat, ethtool, kernel settings, swap off, k6 on `loadgen`. Read it first, then reuse it if it works outside the Terraform lab, or copy the parts you need.

## Scripts to write (`infra/db-sizing/`)

| Script | Does |
|---|---|
| `common.sh` | Variables (region, AZ, subnet, tag, key name `db-sizing`), `ssh`/`scp` helpers, `launch <name> <type>`, `wait_ssh`, `teardown_instances`. |
| `launch` behavior | Look up the AMI. Create the key pair, the security group `db-sizing` (SSH 22 from `$(curl -s https://checkip.amazonaws.com)/32`, all traffic from the group itself) and the cluster placement group `db-sizing`, reusing any that already exist. Then `run-instances` with: spot one-time, terminate on interruption, `--instance-initiated-shutdown-behavior terminate`, 20 GB gp3 root, public IP, tags, and user-data that runs `shutdown -h +60` and installs Docker + sysstat + ethtool. |
| `phase1-rust.sh` | Phase 1 (below). Writes `results/sizing/db-2026-10/phase1/`. |
| `phase2-pg.sh` | Phase 2 (below). Writes `results/sizing/db-2026-10/phase2/`. |
| `teardown.sh` | Terminate every instance tagged `Project=db-sizing` and wait until they're `terminated`. Then delete the security group, key pair and placement group, and list what's left (must be nothing). Safe to run more than once. |

## Phase 1: Rust on 1× m7a.2xlarge (~30 min, ~$0.11)

**Why the split:** with only 8 vCPUs, Rust, Postgres and k6 must share one machine. Each gets dedicated cores, and Rust's **CPU per request** is measured, then scaled to 8 cores.

| Cores | Process | How |
|---|---|---|
| 0–3 | rust-axum, `WORKERS=4`, `DB_POOL_SIZE=64` | `docker update --cpuset-cpus 0-3 app` (or `cpuset` in a compose override) |
| 4–5 | Postgres, `PG_SHARED_BUFFERS=4GB PG_EFFECTIVE_CACHE_SIZE=12GB` | `--cpuset-cpus 4-5` |
| 6–7 | k6 | `taskset -c 6,7 k6 run …` |

**Steps:**
1. **Launch:** `launch rust m7a.2xlarge`, then `wait_ssh`.
2. **Copy the repo over:** rsync `db/`, `stacks/rust-axum/` (without `target/`), `k6/`, `conformance/` and `runner/pgbench/`.
3. **Build and seed at the same time:**
   - Run `docker compose -f stacks/rust-axum/compose.yml build`.
   - Meanwhile, start `db/compose.yml` with the memory settings above. Wait for the seed, then run `db/reset.sql` (this warms the cache with `pg_prewarm`).
4. **Start Rust and pin everything:** start Rust and apply the cpusets above, then verify with `docker inspect` that each container has the right cpuset.
5. **Check the stack:** `python3 conformance/check.py http://127.0.0.1:8080` must pass. If it fails, stop and tear down.
6. **Measure.** For each test in 2, 3, 4, 5:
   1. **Warm-up:** `MODE=warmup`, 20 s, 50 → 1000 req/s.
   2. **Fixed-rate run (the main measurement):**
      - Pick a `RATE` where Rust's cores 0–3 sit at about 50–60%, checking with `mpstat -P 0-3 1`. Start at 2000 for tests 2 and 4, 500 for test 3 and 1000 for test 5, and adjust once.
      - Use `SETTLE_S=10 MEASURE_S=30`.
      - Read `/sys/fs/cgroup/system.slice/docker-<id>.scope/cpu.stat` `usage_usec` just before and after the measure window. Find the path with `docker inspect -f '{{.Id}}' app`.
      - Take the completed request count from the k6 summary's measure-phase metrics.
      - **cpu_per_req (s) = Δusage_usec / 1e6 / measured requests.** Also record Postgres's container CPU and the k6 cores' CPU.
   3. **Ramp (cross-check):**
      - Start at the fixed rate and double it every 10 s step (`SETTLE_S=5 MEASURE_S=5`). After the first failure, step ×1.2 from the last pass.
      - A step fails if p99 ≥ 250 ms, errors ≥ 1%, or there are dropped iterations.
      - Record the last rate that passed, and which resource was full at that point: Rust cores, Postgres cores, or k6 cores.
   4. **Reset after test 5:** run `db/reset.sql`, because test 5 writes orders.
7. **Collect:** copy the results back, then **tear down the instance and wait for `terminated`**. Keep the security group and key pair for phase 2.

**Output: `phase1/rust.csv`**, columns `test, fixed_rate, measured_rps, rust_cpu_s, cpu_per_req_s, pg_cpu_per_req_s, ramp_max_4c, ramp_limited_by`.

**Rust's 8-core estimate per test:**
- **`rust_est_8c = 8 × 0.85 / cpu_per_req_s`** (main method).
- Cross-check: `ramp_max_4c × 1.9`, which is only a lower bound if k6 or Postgres was the resource that ran out.
- If the two differ by more than 30%, say so in the summary.

## Phase 2: Postgres on 2× c7a.xlarge (~30 min, ~$0.10)

Start only after every phase 1 instance is `terminated`, **and only after the user's go**.

1. **Launch:** `launch db c7a.xlarge` and `launch client c7a.xlarge`, in the same placement group.
2. **Seed the db:** copy `db/` and start it with `PG_SHARED_BUFFERS=2GB PG_EFFECTIVE_CACHE_SIZE=6GB`, leaving `postgresql.conf` otherwise unchanged. Wait for the seed, run `db/reset.sql`, and record row counts and `pg_database_size('bench')` (expect about 0.5 GB).
3. **Prepare the client:** copy `runner/pgbench/`. Run pgbench from the `postgres:18.6` image against the db's **private IP**, using `-n -M prepared -j 4 -T 30`.
4. **Run these in order:**

| # | Script | Clients | Extra | Purpose |
|---|---|---|---|---|
| 0 | test2, then test3 | 64 | `-T 15` | Warm-up, not recorded |
| 1 | test2 | 64 | | db maximum at the campaign's pool size |
| 2 | test2 | 128 | | Headroom above 64 |
| 3 | test3 | 64 | | Also gives bytes per page |
| 4 | test3 | 128 | | |
| 5 | test4 | 64 | | |
| 6 | test4 | 128 | | |
| 7 | test2 | 64 | `-C` (new connection per transaction) | PHP-FPM connects on every request |
| 8 | test2 | 64 | | Repeat of run 1, for noise |
| 9 | test2 | 64 | | Repeat of run 1, for noise |
| 10 | test5 | 64 | | Writes; runs last, so no reset is needed |

5. **Record for every run:**
   - **pgbench:** TPS, average latency, latency stddev.
   - **On the db:**
     - `mpstat -P ALL 1`: average busy % per core.
     - `/proc/net/dev` `ens5` tx bytes before and after: bytes/s and **bytes per transaction**.
     - `ethtool -S ens5 | grep allowance_exceeded` before and after: any increase means the network limit was hit.
     - `pg_stat_activity` sampled every second: share of backends with `wait_event_type='Lock'`.
     - For run 10, `pg_stat_io` rows with `object='wal'`, before and after: fsync count and time.
   - **On the client:** `mpstat` average. Above 70% means pgbench was the bottleneck; mark that run "lower bound".
6. **Collect and tear down:** copy the results back, then run **`teardown.sh`** and confirm nothing tagged `Project=db-sizing` is left.

**Output: `phase2/pgbench-runs.csv`**, columns `run, script, clients, connect_per_tx, tps, lat_avg_ms, lat_std_ms, db_cpu_pct, client_cpu_pct, tx_bytes_per_s, bytes_per_tx, allowance_exceeded_delta, lock_wait_pct, wal_fsync_ms`.

**Network limit for test 3:** `net_cap_tps = 1.562e9 / 8 / bytes_per_tx`. A 30 s run can ride on the burst allowance, so for test 3 use **`db_max = min(measured TPS, net_cap_tps)`**.

## Comparison and decision (`results/sizing/db-2026-10/summary.md`)

The summary has one table row per test (2–5), with these columns:
- `db_max_4c`: the best TPS at 64 or 128 clients, with the network limit applied for test 3
- `rust_est_8c`
- `ratio`
- what limited the db: CPU, network or disk
- Postgres CPU per request on m7a, from phase 1, as a cross-check

Add the test 2 `-C` result next to the PHP note.

| Lowest ratio across tests 2–5 | Recommendation |
|---|---|
| **≥ 2×** | Switch the default db to c7a.xlarge. The campaign's per-step db CPU < 70% check still catches stacks faster than Rust. |
| **1.5× – 2×** | Passes the rule with little margin. User's call: c7a.xlarge or c7a.2xlarge. |
| **< 1.5×**, limited by network (test 3) or the `-C` run | Consider c7a.2xlarge (8 cores, 3.1 Gbps) |
| **< 1.5×**, limited by CPU | Keep c7a.4xlarge |

**Also note in the summary:**
- the noise between runs 1, 8 and 9
- any run marked "lower bound"
- spot prices paid and the total cost

Don't change Terraform, `db/postgresql.conf` or the docs. Report the recommendation, and the user decides.

## Cost

| Phase | Machines | ~Time | ~Cost |
|---|---|---|---|
| 1 | m7a.2xlarge spot + IP + disk | 30 min | $0.11 |
| 2 | 2× c7a.xlarge spot + IPs + disks | 30 min | $0.10 |
| **Total** | | **~1 h** | **≈ $0.25** (worst case with both 60-min self-destructs ≈ $0.40) |

Data transfer is free: one AZ, private IPs.

## Not covered by this test

- **Rust at full scale:** Rust is never measured using all 8 cores at once. The figure is CPU per request on 4 dedicated cores, scaled up.
- **Downstream mock:** the limit for tests 6 and 9 needs the full lab.
- **Faster stacks:** a stack faster than Rust on database tests is covered by the 1.5× margin and the campaign's per-step db CPU check.

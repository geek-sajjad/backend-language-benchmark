# 01 · Architecture

## Topology

```
                     AWS region, one AZ · VPC 10.42.0.0/16 · one public subnet 10.42.0.0/24
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │  Internet gateway (free)                    all three hosts in the same AZ       │
 │                                                                                  │
 │   ┌──────────────────┐  HTTP   ┌───────────────┐   SQL    ┌──────────────────┐   │
 │   │ loadgen          │ ──────► │ app           │ ───────► │ db               │   │
 │   │ k6 + runner      │         │ 1 stack at a  │          │ Postgres 18      │   │
 │   │ + Prometheus     │         │ time (Docker) │   HTTP   │ + downstream     │   │
 │   │ (+ Grafana *)    │         │ c7a.xlarge    │ ───────► │   mock (test 6)  │   │
 │   │ c7a.4xlarge 16c  │         │ 4 cores/8GiB  │          │ c7a.xlarge 4c/8G │   │
 │   └───────┬──────────┘         └───────▲───────┘          └────────▲─────────┘   │
 │           │                            │                           │             │
 │           └──────── scrape (1 s) ──────┴───────────────────────────┘             │
 └──────────────────────────────────────────────────────────────────────────────────┘
 * Grafana is stopped during calibration and the scored campaign (see Monitoring)
```

## Network: deliberately minimal

A benchmark lab lives for a few days and holds no sensitive data, so the network is kept to what the measurement actually needs:

| Choice | Why |
|---|---|
| **One public subnet, no private subnet, no NAT gateway** | A NAT gateway costs per hour *plus* per GB processed (every `docker pull`, `apt` and image build goes through it) and adds nothing to the measurement. Each host gets a public IPv4 (a small hourly charge) and reaches the internet through the free internet gateway. |
| **Benchmark traffic uses private IPs only** | Same VPC, same AZ, private addresses: free data transfer and no internet path in the loop. The runner and every compose file are configured with private IPs. |
| **Security group instead of network isolation** | Ingress: **Grafana (3000 on loadgen) from the internet**; SSH (22) **from anywhere** (`0.0.0.0/0`, key-only login; decided 2026-10-17 because the operator's VPN changes its exit IP often; `operator_ip` narrows it from a fixed IP); all benchmark ports (app, Postgres, downstream, Prometheus, exporters) only from the security group itself. |
| **Plain SSH, no SSM / bastion / VPN** | The runner already uses SSH between hosts. |
| **No cluster placement group** (decided 2026-10-13) | A cluster placement group would give the lowest, steadiest latency between hosts, but AWS rejects spot instances that **stop** on interruption inside one (`InvalidParameterCombination`), and the stop + resume rules in [06](06-runner-and-results.md#spot-interruptions) depend on stop. The hosts share one AZ and subnet instead. The app–db latency this adds is the same for every stack, and the pause/resume design is kept. |
| **No load balancer, no Elastic IPs, no CloudWatch detailed monitoring** | Not needed. k6 talks to the app directly; Prometheus does the monitoring. |

## Instances

| Role | Instance | vCPU / RAM | Why |
|---|---|---|---|
| **app** | `c7a.xlarge` | 4 / 8 GiB | The hardware rule: 4 cores / 8 GB, chosen to keep the whole lab cheap (a smaller app also needs smaller db and loadgen hosts). On AMD 7th-gen (`*7a`), **each vCPU is a full physical core with no SMT**, so "4 cores" really means 4 cores. Intel `c7i.xlarge` would give 2 cores with hyperthreading. This is the one host whose size the results depend on, so it never changes within a campaign. |
| **db** | `c7a.xlarge` | 4 / 8 GiB | Same size as the app, on purpose: **the db is allowed to be the bottleneck** in the database tests, and the results say so ([04](04-methodology.md#when-postgres-is-the-bottleneck)). The db-sizing test on this exact type ([results/sizing/db-2026-10](../results/sizing/db-2026-10/summary.md)) measured these Postgres ceilings: test 2 ≈ 130k, test 3 ≈ 7.1k (100-row page; ~13.7k expected now that the page is 50 rows), test 4 ≈ 15.1k, test 5 ≈ 9.0k req/s, so fast stacks will be DB-bound in tests 3–5 and 9. The dataset (~0.5 GB) fits fully in `shared_buffers`. Also runs the Go **downstream mock** for test 6: in test 6 Postgres is idle, and in test 9 the mock handles only ~14% of requests, mostly sleeping. |
| **loadgen** | `c7a.4xlarge` | 16 / 32 GiB | Runs k6, the runner **and Prometheus** (plus Grafana outside scored runs). **Same AMD `c7a` family as app and db** (decided 2026-10-17): Graviton (`c7g.4xlarge`, $0.093/h vs $0.135/h on 2026-10-11) was cheaper, but eu-north-1 spot had no Graviton capacity. Each vCPU is a physical core (no SMT). `c` is already the family with the least RAM per core (2 GiB); no current family goes lower. Monitoring is pinned to 2 cores (`cpuset: "14-15"`) and k6 to the other 14 (`taskset -c 0-13`), with Go's garbage collector off up to a 16 GiB memory limit ([04](04-methodology.md#proving-the-load-generator-is-not-the-bottleneck)). The manual calibration (done before the campaign session) measures k6's ceiling *with monitoring running exactly as in the campaign*. If the best expected rate of any test is above 70% of that ceiling, the campaign session is provisioned with `c7a.8xlarge` instead. One bigger loadgen is preferred over splitting across hosts ([09 R4](09-review-findings.md#r4-k6-json-output-cannot-run-at-high-request-rates)). |

There is no separate monitoring or aux host. Prometheus scraping ~5 targets every second and k6's remote-write (now to `localhost`) use well under one core, which doesn't justify a fourth instance. Both stay off the measured app host.

**Only the scored campaign needs this full set.** Stacks, k6 scripts and the runner are developed locally with Docker. The campaign is split into **one session per stack** (decided 2026-10-17): the three hosts are provisioned, run every test of one stack and are destroyed, and the next stack gets freshly provisioned hosts another day ([06](06-runner-and-results.md#sessions-one-stack-per-day)). Instance types, AZ and AMIs stay the same in every session; the physical machines don't.

**All three hosts run on spot instances, for everything:** dev sessions, the manual calibration and the scored campaign (`use_spot = true`, persistent request, `stop` on interruption). On an interruption the runner pauses, keeps everything finished so far and continues only on matching hardware (same instance type, CPU model and AMI). The rules are in [06 · Spot interruptions](06-runner-and-results.md#spot-interruptions).

**Storage:** one gp3 root volume per host at the free baseline (3,000 IOPS, 125 MB/s), no extra data volumes. The ~0.5 GB dataset is served from RAM, so the disk only matters for WAL writes in tests 5 and 10. Postgres group commit batches those fsyncs. Calibration measures the write ceiling of test 5 ([04](04-methodology.md#when-postgres-is-the-bottleneck)). On the 4-core db host, CPU runs out before the disk (db-sizing test: 99% CPU at the test 5 ceiling), so IOPS/throughput are raised **only if** WAL fsync turns out to be the limit.

**OS:** Ubuntu 24.04 LTS, the same amd64 image on every host (pin `ami_id` for the scored campaign). Kernel tuning is applied identically by `infra/host-setup/bootstrap.sh`:
`net.core.somaxconn=65535`, `net.ipv4.ip_local_port_range="1024 65535"`, `net.ipv4.tcp_tw_reuse=1`, `fs.file-max`, `fs.nr_open`, `nofile` ulimit 1,048,576, and `net.ipv4.ip_local_reserved_ports` for the service ports (3000, 5432, 8080, 8081, 9000, 9090, 9100, 9187), so an outgoing connection can never take a port a service is about to bind. Swap, `unattended-upgrades`, the `apt-daily` timers and the SSM agent are off, so no background job runs during a measurement.

**Network checks:** before and after every measurement step, the runner records `ethtool -S <primary interface> | grep allowance_exceeded` on app, db and loadgen (the interface is detected per host: `enp39s0` on `c7a` with the Ubuntu 24.04 AMI). Any increase makes the step invalid, because the network limit was hit and not the app's. `c7a.xlarge` has a burstable network: **baseline ≈ 1.56 Gbps** (measured in the db-sizing test), bursts up to 12.5 Gbps while credits last. That is half of what the payloads were originally sized for, so test 7's request was cut from 12 KB to ~4 KB (about 1.3 Gbps at 40k req/s). Test 3's db → app traffic was 73% of the db's baseline at the db's ceiling with 100-row pages (measured); with 50-row pages it is about 10 KB per page, ~1.1 Gbps at the expected ~13k pages/s. See the network risk in [08](08-roadmap.md#risks).

## Software on each host

| Host | Containers (all `network_mode: host`) |
|---|---|
| app | the current stack (one at a time), `node_exporter`, `cAdvisor` (tuned: 1 s housekeeping, unneeded collectors disabled) |
| db | `postgres:18`, `downstream` mock, `postgres_exporter` (connected to the `postgres` database, so a `bench` reset doesn't drop it), `node_exporter` |
| loadgen | k6 (binary, not containerized, so Docker adds no overhead; pinned to cores 0–13), runner, `prometheus` + `grafana` (pinned to cores 14–15), `node_exporter` |

Why `network_mode: host`: Docker's bridge network and userland proxy add NAT and copy overhead per packet. Each runtime handles that overhead differently, which would add noise. Host networking removes it for every stack.

Why containers at all: they give reproducible builds, pinned runtime versions, a single `docker compose up` per stack, and per-container memory accounting through cgroups for the RSS metric.

**Container limits on app:** no CPU limit (the stack gets all 4 cores). Memory limit is `7g` (leaves ~1 GB for the OS, `node_exporter` and `cAdvisor`), so if the stack exceeds it the OOM is clearly attributed to the container (`status=oom`) and the host itself doesn't swap. Swap is disabled on every host.

## Monitoring

- **Prometheus** scrapes every 1 s during runs: `node_exporter` (CPU, network, memory and ENA allowance counters via `--collector.ethtool` on every host), `cAdvisor` (container RSS and CPU on app) and `postgres_exporter` (TPS, connections, locks, cache hit ratio). The memory score uses the app cgroup's `anon + shmem`, sampled by the runner ([04](04-methodology.md#test-9-extras-common-load-latency-and-memory)). Ports and queries: [monitoring/README.md](../monitoring/README.md).
- k6 pushes live metrics with `--out experimental-prometheus-rw` for the Grafana dashboards shown in the video. **Scoring never uses these live metrics.** It uses k6's end-of-test summary JSON, which is exact.
- **Grafana is stopped during calibration and the scored campaign** (`docker compose stop grafana` on loadgen), so dashboard queries can't take CPU from k6. Prometheus keeps recording everything. Afterwards Grafana is started again to browse the recorded runs and capture the video clips from the stored data. During dev sessions it can stay on. During a scored run, progress is followed in the runner's console (tmux).
- **Grafana** dashboards are provisioned from `monitoring/grafana/dashboards/`:
  - *Run overview:* offered vs achieved rate, p95/p99, errors, and dropped iterations for the current step
  - *App host:* CPU per core, container RSS, GC- and thread-related metrics where exposed
  - *Headroom:* db CPU, Postgres TPS, locks, loadgen CPU, network allowance counters. **This is the dashboard that proves the app was the bottleneck.**

## Public Grafana (live monitoring)

Grafana on the loadgen host is reachable from the internet at `http://<loadgen-public-ip>:3000` (printed by `terraform output grafana_url`) whenever it is running: during dev sessions and after the scored runs. It is stopped while they run (see above).

| Setting | Value | Why |
|---|---|---|
| Admin login | `admin` + a random password generated by Terraform (`terraform output -raw grafana_admin_password`) | The default `admin/admin` gets scanned and taken over within minutes of an IP going public |
| Viewing | Anonymous access **on**, role `Viewer`, limited to the `Benchmark` org | Anyone with the link can watch the dashboards but can't edit, explore raw data or add data sources. Set `grafana_public_view = false` to require a login instead. |
| Sign-up, Explore for viewers | Off | No accounts created by strangers and no arbitrary PromQL from viewers |
| Prometheus | **Not exposed** | Grafana is the only public entry point and proxies its queries |
| Dashboard refresh | Minimum 5 s (`min_refresh_interval`) | Many viewers at 1 s refresh would add Prometheus query load on the loadgen host, which also runs k6 |
| Source IPs | `grafana_allowed_cidrs`, default `0.0.0.0/0` | Narrow it to a list of IPs if the dashboard shouldn't be open to everyone |

HTTPS is optional. To enable it, put Caddy in front of Grafana on 443 with an automatic Let's Encrypt certificate (`<ip>.sslip.io` works without owning a domain). That is a free container rather than a load balancer. Plain HTTP is acceptable here because the public view carries no credentials. Log in as admin through an SSH tunnel (`ssh -L 3000:localhost:3000 loadgen`) or over HTTPS, never over plain HTTP.

Grafana is off while anything is scored, so viewers can't affect a measurement. The loadgen CPU < 70% validity check still covers Prometheus, and the core pinning keeps it off k6's cores.

## Infrastructure as code

- `infra/terraform/`: VPC, one public subnet, internet gateway, one security group, SSH key pair and the three instances (no placement group, see Network). Instance types, region, operator IP, `grafana_allowed_cidrs`, `grafana_public_view` and `use_spot` are variables. The Grafana admin password is a `random_password` resource.
- `infra/host-setup/`: cloud-init and idempotent scripts for Docker, sysctl, ulimits, exporters, Postgres config and seeding.
- **Lifecycle (every session):** `terraform apply` with the pinned AMIs → run one stack (or, in the last session, the common load) → copy results to this repo → `terraform destroy`. Nothing is kept running between sessions.

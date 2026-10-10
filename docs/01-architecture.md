# 01 · Architecture

## Topology

```
                     AWS region, one AZ · VPC 10.42.0.0/16 · one public subnet 10.42.0.0/24
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │  Internet gateway (free)              cluster placement group "bench" (free)     │
 │                                                                                  │
 │   ┌──────────────────┐  HTTP   ┌───────────────┐   SQL    ┌──────────────────┐   │
 │   │ loadgen          │ ──────► │ app           │ ───────► │ db               │   │
 │   │ k6 + runner      │         │ 1 stack at a  │          │ Postgres 18      │   │
 │   │ + Prometheus     │         │ time (Docker) │   HTTP   │ + downstream     │   │
 │   │ + Grafana        │         │ m7a.2xlarge   │ ───────► │   mock (test 6)  │   │
 │   │ c7a.4xlarge      │         │ 8 cores/32GiB │          │ c7a.4xlarge      │   │
 │   └───────┬──────────┘         └───────▲───────┘          └────────▲─────────┘   │
 │           │                            │                           │             │
 │           └──────── scrape (1 s) ──────┴───────────────────────────┘             │
 └──────────────────────────────────────────────────────────────────────────────────┘
```

## Network: deliberately minimal

A benchmark lab lives for a few days and holds no sensitive data, so the network is kept to what the measurement actually needs:

| Choice | Why |
|---|---|
| **One public subnet, no private subnet, no NAT gateway** | A NAT gateway costs per hour *plus* per GB processed (every `docker pull`, `apt` and image build goes through it) and adds nothing to the measurement. Each host gets a public IPv4 (a small hourly charge) and reaches the internet through the free internet gateway. |
| **Benchmark traffic uses private IPs only** | Same VPC, same AZ, private addresses: free data transfer and no internet path in the loop. The runner and every compose file are configured with private IPs. |
| **Security group instead of network isolation** | Ingress: **Grafana (3000 on loadgen) from the internet**; SSH (22) **only from the operator's IP**; all benchmark ports (app, Postgres, downstream, Prometheus, exporters) only from the security group itself. |
| **Plain SSH, no SSM / bastion / VPN** | The runner already uses SSH between hosts. |
| **Cluster placement group** | Free, and gives low, consistent latency between hosts. It is the one network feature that matters for the results. |
| **No load balancer, no Elastic IPs, no CloudWatch detailed monitoring** | Not needed. k6 talks to the app directly; Prometheus does the monitoring. |

## Instances

| Role | Instance | vCPU / RAM | Why |
|---|---|---|---|
| **app** | `m7a.2xlarge` | 8 / 32 GiB | Matches the required 8 cores / 32 GB exactly. On AMD 7th-gen (`*7a`), **each vCPU is a full physical core with no SMT**, so "8 cores" really means 8 cores. Intel `m7i.2xlarge` would give 4 cores with hyperthreading. This is the one host whose size the results depend on, so it never shrinks. |
| **db** | `c7a.4xlarge` | 16 / 32 GiB | 2× the app's cores. The dataset (~0.5 GB) fits fully in `shared_buffers` and OS cache, so the extra RAM of `m7a` buys nothing. Also runs the Go **downstream mock** for test 6: in test 6 Postgres is idle, and in test 9 the mock handles only ~14% of requests, mostly sleeping. The db CPU < 70% check covers both. Calibration ([04](04-methodology.md#proving-postgres-is-not-the-bottleneck)) must prove headroom. If it doesn't, go to `c7a.8xlarge`. |
| **loadgen** | `c7a.4xlarge` | 16 / 32 GiB | Runs k6, the runner, **and Prometheus + Grafana**. Monitoring is pinned to 2 cores (`cpuset: "14-15"`) and k6 to the other 14 (`taskset -c 0-13`), so a Grafana viewer burst can't take CPU from k6 mid-step. Calibration measures k6's ceiling *with monitoring running*. If the best expected test 1 rate is above 70% of that ceiling, resize this host to `c7a.8xlarge` before the scored run (stop → change type → start; the app host is untouched). One bigger loadgen is preferred over splitting across hosts ([09 R4](09-review-findings.md#r4-k6-json-output-cannot-run-at-high-request-rates)). |

There is no separate monitoring or aux host. Prometheus scraping ~5 targets every second and k6's remote-write (now to `localhost`) use well under one core, which doesn't justify a fourth instance. Both stay off the measured app host.

**Only the scored campaign needs this full set.** Stacks, k6 scripts and the runner are developed locally with Docker. Short dev sessions on AWS (debugging a stack on the real app host) can use spot instances. Calibration, pilot and the scored campaign run on on-demand instances so no host is reclaimed mid-campaign.

**Storage:** one gp3 root volume per host at the free baseline (3,000 IOPS, 125 MB/s), no extra data volumes. The ~0.5 GB dataset is served from RAM, so the disk only matters for WAL writes in tests 5 and 10. Postgres group commit batches those fsyncs. Calibration measures the write ceiling of test 5 ([04](04-methodology.md#proving-postgres-is-not-the-bottleneck)), and IOPS/throughput are raised **only if** that ceiling is too close to the best stack's rate.

**OS:** Ubuntu 24.04 LTS with the same AMI on every host (pin `ami_id` for the scored campaign). Kernel tuning is applied identically by `infra/host-setup/bootstrap.sh`:
`net.core.somaxconn=65535`, `net.ipv4.ip_local_port_range="1024 65535"`, `net.ipv4.tcp_tw_reuse=1`, `fs.file-max`, `fs.nr_open`, `nofile` ulimit 1,048,576, and `net.ipv4.ip_local_reserved_ports` for the service ports (3000, 5432, 8080, 8081, 9000, 9090, 9100, 9187), so an outgoing connection can never take a port a service is about to bind. Swap, `unattended-upgrades`, the `apt-daily` timers and the SSM agent are off, so no background job runs during a measurement.

**Network checks:** before and after every measurement step, the runner records `ethtool -S ens5 | grep allowance_exceeded` on app, db and loadgen. Any increase makes the step invalid, because the network limit was hit and not the app's. `m7a.2xlarge` has a burstable network (baseline ≈ 3.1 Gbps). The payloads in tests 7 and 8 are sized to stay well below that ([02](02-test-spec.md)).

## Software on each host

| Host | Containers (all `network_mode: host`) |
|---|---|
| app | the current stack (one at a time), `node_exporter`, `cAdvisor` (tuned: 1 s housekeeping, unneeded collectors disabled) |
| db | `postgres:18`, `downstream` mock, `postgres_exporter` (connected to the `postgres` database, so a `bench` reset doesn't drop it), `node_exporter` |
| loadgen | k6 (binary, not containerized, so Docker adds no overhead; pinned to cores 0–13), runner, `prometheus` + `grafana` (pinned to cores 14–15), `node_exporter` |

Why `network_mode: host`: Docker's bridge network and userland proxy add NAT and copy overhead per packet. Each runtime handles that overhead differently, which would add noise. Host networking removes it for every stack.

Why containers at all: they give reproducible builds, pinned runtime versions, a single `docker compose up` per stack, and per-container memory accounting through cgroups for the RSS metric.

**Container limits on app:** no CPU limit (the stack gets all 8 cores). Memory limit is `30g`, so if the stack exceeds it the OOM is clearly attributed to the container (`status=oom`) and the host itself doesn't swap. Swap is disabled on every host.

## Monitoring

- **Prometheus** scrapes every 1 s during runs: `node_exporter` (CPU, network, memory and ENA allowance counters via `--collector.ethtool` on every host), `cAdvisor` (container RSS and CPU on app) and `postgres_exporter` (TPS, connections, locks, cache hit ratio). The memory score uses the app cgroup's `anon + shmem`, sampled by the runner ([04](04-methodology.md#test-9-extras-common-load-latency-and-memory)). Ports and queries: [monitoring/README.md](../monitoring/README.md).
- k6 pushes live metrics with `--out experimental-prometheus-rw` for the Grafana dashboards shown in the video. **Scoring never uses these live metrics.** It uses k6's end-of-test summary JSON, which is exact.
- **Grafana** dashboards are provisioned from `monitoring/grafana/dashboards/`:
  - *Run overview:* offered vs achieved rate, p95/p99, errors, and dropped iterations for the current step
  - *App host:* CPU per core, container RSS, GC- and thread-related metrics where exposed
  - *Headroom:* db CPU, Postgres TPS, locks, loadgen CPU, network allowance counters. **This is the dashboard that proves the app was the bottleneck.**

## Public Grafana (live monitoring)

Grafana on the loadgen host is reachable from the internet at `http://<loadgen-public-ip>:3000` (printed by `terraform output grafana_url`), so the runs can be watched live from anywhere.

| Setting | Value | Why |
|---|---|---|
| Admin login | `admin` + a random password generated by Terraform (`terraform output -raw grafana_admin_password`) | The default `admin/admin` gets scanned and taken over within minutes of an IP going public |
| Viewing | Anonymous access **on**, role `Viewer`, limited to the `Benchmark` org | Anyone with the link can watch the dashboards but can't edit, explore raw data or add data sources. Set `grafana_public_view = false` to require a login instead. |
| Sign-up, Explore for viewers | Off | No accounts created by strangers and no arbitrary PromQL from viewers |
| Prometheus | **Not exposed** | Grafana is the only public entry point and proxies its queries |
| Dashboard refresh | Minimum 5 s (`min_refresh_interval`) | Many viewers at 1 s refresh would add Prometheus query load on the loadgen host, which also runs k6 |
| Source IPs | `grafana_allowed_cidrs`, default `0.0.0.0/0` | Narrow it to a list of IPs if the dashboard shouldn't be open to everyone |

HTTPS is optional. To enable it, put Caddy in front of Grafana on 443 with an automatic Let's Encrypt certificate (`<ip>.sslip.io` works without owning a domain). That is a free container rather than a load balancer. Plain HTTP is acceptable here because the public view carries no credentials. Log in as admin through an SSH tunnel (`ssh -L 3000:localhost:3000 loadgen`) or over HTTPS, never over plain HTTP.

Viewer traffic is measured too: the loadgen CPU < 70% validity check covers Prometheus and Grafana load, and the core pinning keeps it off k6's cores, so a burst of viewers can't silently slow the load generator.

## Infrastructure as code

- `infra/terraform/`: VPC, one public subnet, internet gateway, one security group, SSH key pair, placement group and the three instances. Instance types, region, operator IP, `grafana_allowed_cidrs`, `grafana_public_view` and `use_spot` are variables. The Grafana admin password is a `random_password` resource.
- `infra/host-setup/`: cloud-init and idempotent scripts for Docker, sysctl, ulimits, exporters, Postgres config and seeding.
- **Lifecycle:** `terraform apply` → run the campaign → `terraform destroy`. Nothing is kept running between sessions. Results are copied to this repo before destroying.

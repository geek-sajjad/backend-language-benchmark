# monitoring

Prometheus, Grafana and the exporters ([docs/01-architecture.md](../docs/01-architecture.md#monitoring)).
Prometheus scrapes every 1 s; k6 pushes its live metrics into it with remote write. **Scoring never
uses these metrics** (it uses k6's end-of-test summary); the runner uses Prometheus only for the
validity checks (db/loadgen CPU, network allowances) and the recovery check (app container CPU).

## What runs where

All containers use `network_mode: host`.

| Host | Service | Port | Container | Started by |
|---|---|---|---|---|
| app | stack under test | 8080 | `app` | `stacks/<name>/compose.yml` |
| app | node_exporter (+ ethtool collector) | 9100 | `node-exporter` | `exporters/app.yml` |
| app | cAdvisor (1 s housekeeping, only cpu/memory/network/oom/process/pressure) | 8081 | `cadvisor` | `exporters/app.yml` |
| db | Postgres 18 | 5432 | `bench-db` | `db/compose.yml` |
| db | downstream mock | 9000 | `downstream` | `downstream/compose.yml` |
| db | node_exporter (+ ethtool collector) | 9100 | `node-exporter` | `exporters/db.yml` |
| db | postgres_exporter | 9187 | `postgres-exporter` | `exporters/db.yml` |
| loadgen | node_exporter (+ ethtool collector) | 9100 | `node-exporter` | `exporters/loadgen.yml` |
| loadgen | Prometheus (remote-write receiver, 15 d retention) | 9090 | `prometheus` | `compose.yml` |
| loadgen | Grafana | 3000 | `grafana` | `compose.yml` |

Pinned images: `prom/prometheus:v3.15.0`, `grafana/grafana:13.2.3`, `prom/node-exporter:v1.12.1`,
`ghcr.io/google/cadvisor:v0.60.6`, `prometheuscommunity/postgres-exporter:v0.20.1`.

On loadgen, Prometheus, Grafana and node_exporter are pinned to `MONITORING_CPUSET` (default
`14-15`), so they stay off k6's cores (`k6_cpuset = "0-13"` in `config/bench.toml`).

## Files

The data flows in one direction: **exporters** expose metrics on each host → **Prometheus** (on loadgen)
scrapes them every second and also receives k6's pushed metrics → **Grafana** (on loadgen) draws them.

### `compose.yml` (top level)

Starts the loadgen-side services: `prometheus`, `grafana` and `grafana-init`. All three are pinned to
`MONITORING_CPUSET` so they stay off k6's cores.

- **prometheus** runs with the remote-write receiver on (k6 pushes here) and the lifecycle API on
  (reload without restart). Data lives in the `prometheus-data` volume.
- **grafana** has no data volume. The datasource and dashboards are provisioned from this repo, and
  the admin password and public-view switch come from environment variables. All `GF_*` settings
  live here (see [Grafana settings](#grafana-settings-docs01-public-grafana)).
- **grafana-init** is a one-shot that waits for Grafana to be healthy, renames org 1 to `Benchmark`
  and exits. Anonymous viewing needs an org with that name to exist.

### `exporters/`: producers of metrics, one compose file per host

Each file runs on its own host and exposes metrics over HTTP. Prometheus never starts them.

| File | Runs | What it exposes |
|---|---|---|
| `app.yml` | node_exporter `:9100`, cAdvisor `:8081` | Host CPU, memory and network; per-container CPU and memory for the `app` container. cAdvisor is the source of the recovery check (app container CPU < 0.05) |
| `db.yml` | node_exporter `:9100`, postgres_exporter `:9187` | Host metrics for the db machine; Postgres stats (TPS, connections, locks, cache hit ratio) |
| `loadgen.yml` | node_exporter `:9100` | Host metrics for the load generator, so we can prove it was not the bottleneck. Pinned to `MONITORING_CPUSET` |

Every node_exporter also runs the ethtool collector, which reads the ENA `*_allowance_exceeded`
counters. They show whether AWS throttled the network during a step (a validity check).

### `prometheus/`: what to scrape and how

| Path | Job |
|---|---|
| `prometheus.yml` | Scrape every 1 s, keep 15 days. Three jobs: `node`, `cadvisor`, `postgres`. cAdvisor output is filtered to only the series the runner and dashboards use |
| `targets/node.json` | file_sd list of the node_exporters (one entry per host, with its `host` label) |
| `targets/cadvisor.json` | file_sd list for cAdvisor (app host only) |
| `targets/postgres.json` | file_sd list for postgres_exporter (db host only) |
| `gen-targets.sh` | `gen-targets.sh APP_IP DB_IP LOADGEN_IP` rewrites the three target files with real private IPs. `infra/host-setup/deploy.sh` runs it on AWS |

The committed target files point at `127.0.0.1` (local development, one machine plays all three
hosts). Running `gen-targets.sh 127.0.0.1 127.0.0.1 127.0.0.1` reproduces them exactly.

### `grafana/`: dashboards, provisioned from files

| Path | Job |
|---|---|
| `provisioning/datasources/prometheus.yml` | Defines the Prometheus datasource (uid `prometheus`) that every dashboard queries |
| `provisioning/dashboards/dashboards.yml` | Dashboard provider: loads every JSON in `dashboards/`, rescans every 30 s, read-only (`allowUiUpdates: false`, no deletion) |
| `build_dashboards.py` | Generates the three dashboard JSON files (stdlib only). **Edit this, re-run it, and commit both**; never edit the JSON by hand |
| `dashboards/run-overview.json` | *Run overview* (home dashboard): current state, throughput, latency (k6 native histogram), errors and load-generator health. Meant to be shown on screen during a run |
| `dashboards/app-host.json` | *App host*: CPU, memory and network of the machine running the stack under test |
| `dashboards/headroom.json` | *Headroom*: validity checks (is the app the bottleneck?), db and loadgen CPU, Postgres internals, network and ENA allowance counters |
| `init-org.sh` | Script run by `grafana-init`; renames org 1 to `Benchmark` through Grafana's API. Safe to re-run |

Every target has a `host` label (`app`, `db`, `loadgen`). Prometheus re-reads the target files every
10 s; after editing `prometheus.yml`, `curl -X POST http://127.0.0.1:9090/-/reload`.

## Local (one machine plays all three hosts)

Only one node_exporter can bind :9100, so start one and let the three `host` labels in
`targets/node.json` all point at it (all three CPU queries then show this machine).

```bash
docker compose -f monitoring/exporters/app.yml up -d                     # node-exporter + cadvisor
docker compose -f monitoring/exporters/db.yml up -d postgres-exporter    # not its node-exporter
MONITORING_CPUSET=6-7 docker compose -f monitoring/compose.yml up -d     # cores that exist here
```

Grafana: http://localhost:3000 (anonymous viewer; admin/admin). Prometheus: http://localhost:9090.

Stop: `docker compose -f monitoring/compose.yml down` and `docker compose -f monitoring/exporters/<host>.yml down`
(`down -v` on `compose.yml` also deletes the Prometheus data).

## AWS

```bash
# app host
docker compose -f monitoring/exporters/app.yml up -d
# db host
docker compose -f monitoring/exporters/db.yml up -d
# loadgen host
docker compose -f monitoring/exporters/loadgen.yml up -d
monitoring/prometheus/gen-targets.sh <app-private-ip> <db-private-ip> <loadgen-private-ip>
GRAFANA_ADMIN_PASSWORD="$(terraform output -raw grafana_admin_password)" \
GRAFANA_PUBLIC_VIEW=true \
  docker compose -f monitoring/compose.yml up -d
```

- `GRAFANA_ADMIN_PASSWORD` must be set on AWS (the local default is `admin`). Grafana keeps no data
  volume: everything is provisioned, and the password is applied on every start.
- `GRAFANA_PUBLIC_VIEW=false` turns anonymous viewing off (login required).
- Only port 3000 is opened to the internet; Prometheus (9090) and the exporters stay private.
- The ENA counters (`node_ethtool_{bw_in,bw_out,pps,conntrack,linklocal}_allowance_exceeded`) only
  exist on ENA NICs; locally the allowance panels show no data.

### Grafana settings (docs/01 "Public Grafana")

Set through `GF_*` variables in `compose.yml`: org `Benchmark`, anonymous role `Viewer`, sign-up and org
creation off, viewers can't edit (so no Explore for them; admins keep it), `min_refresh_interval` 5s,
version hidden, update checks and news off. Dashboards are read-only (`allowUiUpdates: false`).

## k6 remote write

The runner starts k6 with:

```bash
K6_PROMETHEUS_RW_SERVER_URL=http://127.0.0.1:9090/api/v1/write \
K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM=true \
K6_PROMETHEUS_RW_PUSH_INTERVAL=1s \
k6 run --out experimental-prometheus-rw --tag testid=rust-axum/3/a1-14-step-12000 --tag stack=rust-axum --tag test=3 --tag phase=measure ...
```

Metrics arrive as `k6_http_reqs_total`, `k6_http_req_duration_seconds` (native histogram),
`k6_http_req_failed_rate`, `k6_iterations_total`, `k6_vus`, `k6_vus_max`, `k6_dropped_iterations_total`, ...
Request metrics carry `testid stack test phase name method status scenario expected_response`;
`k6_vus`/`k6_vus_max` only carry the global tags (`testid stack test phase`), no `name`/`scenario`.
`k6_dropped_iterations_total` only appears once k6 has dropped an iteration, so queries use `or vector(0)`.
k6 adds a label `k6_feature_native_histograms="true"` to every series.

The dashboards' rate window (`$window`, default 10s) must cover at least two pushes; with k6's default
push interval (5s) pick 30s.

## PromQL the runner uses

| What | Query |
|---|---|
| app container CPU (cores), recovery check < 0.05 | `sum(rate(container_cpu_usage_seconds_total{name="app"}[5s]))` |
| app container RSS (bytes) | `container_memory_rss{name="app"}` |
| host CPU busy fraction (validity < 0.70 for db, loadgen) | `1 - avg(rate(node_cpu_seconds_total{job="node",host="db",mode="idle"}[30s]))` (same with `host="loadgen"`, `host="app"`) |
| Postgres TPS | `sum(rate(pg_stat_database_xact_commit{datname="bench"}[5s]))` |
| network allowance (validity: no increase) | `sum by (host, counter) (increase(label_replace({__name__=~"node_ethtool_.+_allowance_exceeded",job="node"}, "counter", "$1", "__name__", "node_ethtool_(.+)_allowance_exceeded")[<step>s:1s]))` |

`increase()` drops the metric name, so a plain `increase({__name__=~"..._allowance_exceeded"})` fails
with "vector cannot contain metrics with the same labelset"; the `label_replace` subquery keeps the
counter's name as a `counter` label. Alternatively, read the raw counters at step start and end and diff.

Notes:
- `container_memory_rss` is cgroup v2 `anon`. cAdvisor exposes no `shmem` series, so shared memory
  (PHP OPcache) is not in it; see [09-review-findings R2](../docs/09-review-findings.md). Measuring
  `anon + shmem` needs `memory.stat` read directly (runner or a node_exporter textfile collector).
- postgres_exporter connects to the `postgres` database with `application_name=postgres_exporter`,
  not to `bench`, so `db/reset.sql` (`DROP DATABASE bench WITH (FORCE)`) never kills it. Exclude
  that application_name when checking `pg_stat_activity` for active queries.

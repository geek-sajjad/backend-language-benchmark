# infra/host-setup

Turns the three hosts from [`../terraform`](../terraform) into the lab of [docs/01-architecture.md](../../docs/01-architecture.md). Two layers:

1. **On first boot** (cloud-init, no operator action): [`cloud-init.yaml.tftpl`](cloud-init.yaml.tftpl) installs [`bootstrap.sh`](bootstrap.sh) as `/usr/local/sbin/bench-bootstrap.sh` and runs it with the host's role.
2. **From the operator machine**: `deploy.sh`, `seed-db.sh`, `build-images.sh`, `fetch-results.sh`. They read IPs and secrets from `terraform -chdir=infra/terraform output -json`, so Terraform (≥ 1.9), `ssh`, `rsync`, `python3` and `openssl` are required locally. All scripts are idempotent.

```bash
./infra/host-setup/deploy.sh                       # repo, SSH, hosts.toml, exporters, downstream, monitoring
./infra/host-setup/seed-db.sh                      # Postgres 18 + seed on db, prints row counts
./infra/host-setup/build-images.sh [stack ...]     # builds on app, records image ids
./infra/host-setup/fetch-results.sh <campaign-id> [--no-raw]
```

## bootstrap.sh (all hosts, as root)

| What | Detail |
|---|---|
| Packages | `ethtool rsync git jq tmux sysstat python3 python3-jwt python3-cryptography postgresql-client` (Ubuntu 24.04: Python 3.12, psql 16) |
| Docker | Docker CE + buildx + compose plugin from Docker's apt repo; `ubuntu` in the `docker` group; `daemon.json` with default `nofile` 1048576 and capped json-file logs |
| sysctl (`/etc/sysctl.d/90-bench.conf`) | `net.core.somaxconn=65535`, `net.ipv4.ip_local_port_range=1024 65535`, `net.ipv4.tcp_tw_reuse=1`, `fs.file-max=4194304`, `fs.nr_open=1048576`, and `net.ipv4.ip_local_reserved_ports=3000,5432,8080,8081,9000,9090,9100,9187` |
| nofile 1048576 | `limits.d` for `ubuntu`/`root`, systemd `DefaultLimitNOFILE`, `LimitNOFILE` for docker and containerd |
| Swap | `swapoff -a`, swap lines in `/etc/fstab` commented out |
| Quiet host | `unattended-upgrades`, `apt-daily*` timers and the SSM agent snap disabled; no package upgrade at boot (hosts stay on what the AMI ships) |
| k6 (loadgen only) | Pinned release tarball from GitHub (`k6_version`, default 2.2.0), SHA-256 verified, installed to `/usr/local/bin/k6` |
| Marker | `/var/lib/bench/bootstrap.done` with role, kernel, Docker, compose and k6 versions |

The reserved ports are an addition to docs/01: with the ephemeral range starting at 1024, an outgoing connection (e.g. the app's DB pool) could otherwise take port 8080 or 9100 while a container restarts.

Log: `/var/log/cloud-init-output.log`. Re-run by hand: `sudo /usr/local/sbin/bench-bootstrap.sh <role> <k6_version> <k6_sha256>`.

## deploy.sh

1. Generates the test JWT pair locally if missing (`k6/data/jwt/gen-keys.sh`).
2. Waits for SSH, cloud-init and the bootstrap marker on all hosts.
3. Rsyncs the repo to `/home/ubuntu/bench` on every host, skipping git-ignored files (`target/`, Terraform state, ...) except `k6/data/jwt/public.pem` (all hosts) and `private.pem` (loadgen only). `results/` and `runner/hosts.toml` on the hosts are never overwritten. No `--delete`: files removed locally stay on the hosts. Checks that `public.pem` on app and loadgen matches the local one.
4. Sets up intra-cluster SSH (below).
5. Writes `runner/hosts.toml` on loadgen from `terraform output hosts_toml` and runs `monitoring/prometheus/gen-targets.sh APP_IP DB_IP LOADGEN_IP`.
6. Starts `monitoring/exporters/{app,db,loadgen}.yml`, `downstream/compose.yml` on db, and `monitoring/compose.yml` on loadgen with `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_PUBLIC_VIEW` and `MONITORING_CPUSET` (default `14-15`).
7. From loadgen, checks every target over the private network: node-exporter 9100 (all), cAdvisor 8081 (app), postgres-exporter 9187 and downstream 9000 (db), Prometheus `/-/ready`, Grafana `/api/health`.

Postgres is not started by deploy; `seed-db.sh` does that.

### Intra-cluster SSH (runner on loadgen → app, db)

`deploy.sh` generates `~/.ssh/id_ed25519` **on loadgen** (once) and appends its public key to `~/.ssh/authorized_keys` of `ubuntu` on app, db and loadgen itself. It writes `~/.ssh/config` on loadgen (`Host 10.42.0.*`, user `ubuntu`, that key) and pre-fills `known_hosts` with `ssh-keyscan` of the three private IPs, then verifies `ssh ubuntu@<private-ip> true` to each. The private key never leaves loadgen, is not in Terraform state or `user_data`, and disappears with the instance. The security group already allows port 22 between members.

## seed-db.sh

Starts `db/compose.yml` on db with `PG_SHARED_BUFFERS=8GB PG_EFFECTIVE_CACHE_SIZE=24GB` (override via the same env vars locally), waits until `bench` answers over TCP and the container is healthy (TCP matters: during the init scripts the entrypoint's temporary server listens on the Unix socket only), then prints the `bench*` databases, row counts per table, effective settings and DB size, and checks that loadgen reaches Postgres on the private IP. With an existing `bench-pgdata` volume it only restarts the container. Wipe: `docker compose -f db/compose.yml down -v` on db.

## build-images.sh

On app, runs `docker compose -f stacks/<s>/compose.yml build` for every `stacks/*/compose.yml` (or the stacks given as arguments), one at a time. Writes `results/image-digests.json` on app and copies it to loadgen at the same path (`/home/ubuntu/bench/results/image-digests.json`) for the runner's manifest:

```json
{
  "stacks": {
    "rust-axum": {
      "compose": "stacks/rust-axum/compose.yml",
      "images": { "bench-rust-axum:local": "sha256:..." },
      "created": "...", "built_at": "...", "build_s": 312
    }
  },
  "git_commit": "...", "git_dirty": false, "docker_version": "...", "arch": "x86_64", "host": "bench-app", "updated_at": "..."
}
```

The ids are local image ids (`docker image inspect .Id`, the config digest): images are built on the host and never pushed, so there are no registry digests. Building a subset updates only those entries.

## fetch-results.sh

`rsync`s `results/<campaign-id>/` from loadgen into this checkout (incremental; `--no-raw` skips `raw/`, which is git-ignored anyway) and copies `results/image-digests.json` next to it.

## Environment knobs

| Variable | Used by | Meaning |
|---|---|---|
| `TERRAFORM` | all | Terraform command, default `terraform`. A Docker wrapper works if the repo is mounted at the same path: `TERRAFORM="docker run --rm -v $PWD:$PWD -w $PWD hashicorp/terraform:1.16.5"` |
| `BENCH_TF_OUTPUT_JSON` | all | Use a saved `terraform output -json` file instead of calling Terraform |
| `BENCH_SSH_KEY` | all | Operator private key (default: ssh-agent / `~/.ssh` defaults) |
| `MONITORING_CPUSET` | deploy | Cores for Prometheus + Grafana on loadgen, default `14-15` |
| `PG_SHARED_BUFFERS`, `PG_EFFECTIVE_CACHE_SIZE` | seed-db | Default `8GB`, `24GB` |

Operator-side host keys are pinned per deployment in `infra/host-setup/.state/known_hosts-<loadgen-instance-id>` (git-ignored), because AWS recycles public IPs between sessions.

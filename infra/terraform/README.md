# infra/terraform

The AWS lab from [docs/01-architecture.md](../../docs/01-architecture.md): one VPC (`10.42.0.0/16`), one public subnet (`10.42.0.0/24`) with an internet gateway (no NAT), one security group, an SSH key pair and three Ubuntu 24.04 hosts on the same AMI, all in one AZ. There is no cluster placement group: AWS rejects stop-on-interruption spot instances in one (`InvalidParameterCombination`), and the runner relies on stop ([docs/01](../../docs/01-architecture.md#network-deliberately-minimal)). Every resource is tagged `project = "lang-bench"`.

| Role | Default type | Private IP |
|---|---|---|
| app | `c7a.xlarge` | `10.42.0.10` |
| db | `c7a.xlarge` | `10.42.0.11` |
| loadgen | `c7g.4xlarge` (arm64) | `10.42.0.12` |

Each host gets one encrypted gp3 root volume at the free baseline (3000 IOPS, 125 MB/s), IMDSv2, no detailed monitoring. Its `user_data` is [`../host-setup/cloud-init.yaml.tftpl`](../host-setup/cloud-init.yaml.tftpl), which installs and runs [`../host-setup/bootstrap.sh`](../host-setup/bootstrap.sh) (Docker, sysctls, limits, swap off, k6 on loadgen).

Pinned: Terraform `>= 1.9` (validated with 1.16.5), `hashicorp/aws = 6.68.0`, `hashicorp/random = 3.9.1`. Commit `.terraform.lock.hcl`.

## Usage

```bash
cd infra/terraform
terraform init
terraform apply -var region=eu-north-1 -var operator_ip=$(curl -s ifconfig.me)/32
terraform output                 # IPs, grafana_url, hosts_toml, ami_id ...
../host-setup/deploy.sh          # then seed-db.sh, build-images.sh (see ../host-setup/README.md)
terraform destroy                # at the end of every session
```

Terraform must be installed on the operator machine: the `infra/host-setup/` scripts read `terraform -chdir=infra/terraform output -json`. AWS credentials come from the usual AWS CLI/SDK chain (`AWS_PROFILE`, env vars, SSO).

## Variables

| Variable | Default | Notes |
|---|---|---|
| `region` | `eu-north-1` | |
| `availability_zone` | `eu-north-1c` | Cheapest c7a/m7a spot AZ on 2026-10-10. Empty = first AZ (alphabetical) that offers all three instance types. Must be in `region` |
| `app_type` | `c7a.xlarge` | The measured host (4 cores / 8 GB); never change it within a campaign |
| `db_type` | `c7a.xlarge` | Never upsized: DB-bound results are reported ([docs/04](../../docs/04-methodology.md#when-postgres-is-the-bottleneck)) |
| `loadgen_type` | `c7g.4xlarge` | Graviton (arm64): not measured, cheaper per core on spot. `c7g.8xlarge` if calibration demands it |
| `operator_ip` | **required** | CIDR allowed to SSH, e.g. `203.0.113.7/32` |
| `grafana_allowed_cidrs` | `["0.0.0.0/0"]` | Who may reach Grafana on 3000. `[]` = SSH tunnel only |
| `grafana_public_view` | `true` | Anonymous Viewer access; `false` = login required (applied by `deploy.sh`) |
| `use_spot` | `true` | Persistent spot, stop on interruption, for every session including the scored campaign ([docs/06](../../docs/06-runner-and-results.md#spot-interruptions)). `false` = on-demand |
| `ssh_public_key_path` | `~/.ssh/id_ed25519.pub` | Installed for `ubuntu` on every host |
| `ami_id` | `""` | x86_64 hosts (app, db). Empty = latest Canonical Ubuntu 24.04 amd64. Pin it (from `terraform output ami_id`) for the scored campaign |
| `ami_id_arm64` | `""` | arm64 hosts (the Graviton loadgen). Empty = latest Ubuntu 24.04 arm64. Pin it together with `ami_id` (`terraform output ami_id_arm64`), from the same Canonical release |
| `root_volume_gb` | `50` | Room for 7 stack images + build cache on app |
| `k6_version` / `k6_sha256` | `2.2.0` / map `{amd64, arm64}` of the release tarball checksums | Change together; the loadgen gets the one for its architecture |

## Outputs

`public_ips`, `private_ips`, `instance_ids`, `instance_types`, `host_arch` (maps keyed by `app`/`db`/`loadgen`), `ami_id`, `ami_id_arm64`, `availability_zone`, `ssh_commands`, `grafana_url`, `grafana_admin_password` (sensitive: `terraform output -raw grafana_admin_password`), `grafana_public_view`, and `hosts_toml`, the exact content of `runner/hosts.toml` that `deploy.sh` writes on loadgen.

## Behaviour worth knowing

- **Resizing** (`terraform apply -var loadgen_type=c7g.8xlarge`) stops and starts only that instance in place. Its **public IP changes** (no Elastic IPs); private IPs never do. Re-run `../host-setup/deploy.sh` afterwards (it restarts the services and rewrites the targets; idempotent).
- **Stale outputs:** after a stop/start outside Terraform (console, spot interruption), run `terraform apply -refresh-only` so `public_ips` and `grafana_url` are current.
- **AMI and user_data changes are ignored** on existing instances (`lifecycle.ignore_changes`), so a new Ubuntu release or a cloud-init edit never replaces hosts mid-campaign. To apply one on purpose: `terraform apply -replace='aws_instance.host["app"]'` (and pin `ami_id` / `ami_id_arm64` so all hosts stay on the same image).
- **Capacity:** all three instances must fit in one AZ. `InsufficientInstanceCapacity` (Terraform retries it silently for minutes; CloudTrail shows it) → retry later, try another `availability_zone`, or use a capacity reservation for the campaign window ([08 risks](../../docs/08-roadmap.md#risks)). The spot vCPU quota ("All Standard Spot Instance Requests", x86 and Graviton together) must cover 24 vCPU (40 with a `c7g.8xlarge` loadgen).
- **Cost:** ≈ $0.24/h on spot for the three hosts plus public IPv4 and gp3 ([08 budget](../../docs/08-roadmap.md#budget-estimate-eu-north-1c-spot-2026-10-11-spot-prices-move-check-before-each-session)); spot prices move. Nothing here costs money once destroyed.

## Validate without AWS

```bash
docker run --rm -v "$PWD":/w -w /w/infra/terraform hashicorp/terraform:1.16.5 fmt -check
docker run --rm -v "$PWD":/w -w /w/infra/terraform hashicorp/terraform:1.16.5 init -backend=false
docker run --rm -v "$PWD":/w -w /w/infra/terraform hashicorp/terraform:1.16.5 validate
```

# infra/terraform

The AWS lab from [docs/01-architecture.md](../../docs/01-architecture.md): one VPC (`10.42.0.0/16`), one public subnet (`10.42.0.0/24`) with an internet gateway (no NAT), one security group, an SSH key pair, a cluster placement group `bench` and three Ubuntu 24.04 hosts on the same AMI. Every resource is tagged `project = "lang-bench"`.

| Role | Default type | Private IP |
|---|---|---|
| app | `m7a.2xlarge` | `10.42.0.10` |
| db | `c7a.4xlarge` | `10.42.0.11` |
| loadgen | `c7a.4xlarge` | `10.42.0.12` |

Each host gets one encrypted gp3 root volume at the free baseline (3000 IOPS, 125 MB/s), IMDSv2, no detailed monitoring. Its `user_data` is [`../host-setup/cloud-init.yaml.tftpl`](../host-setup/cloud-init.yaml.tftpl), which installs and runs [`../host-setup/bootstrap.sh`](../host-setup/bootstrap.sh) (Docker, sysctls, limits, swap off, k6 on loadgen).

Pinned: Terraform `>= 1.9` (validated with 1.16.5), `hashicorp/aws = 6.68.0`, `hashicorp/random = 3.9.1`. Commit `.terraform.lock.hcl`.

## Usage

```bash
cd infra/terraform
terraform init
terraform apply -var region=us-east-1 -var operator_ip=$(curl -s ifconfig.me)/32
terraform output                 # IPs, grafana_url, hosts_toml, ami_id ...
../host-setup/deploy.sh          # then seed-db.sh, build-images.sh (see ../host-setup/README.md)
terraform destroy                # at the end of every session
```

Terraform must be installed on the operator machine: the `infra/host-setup/` scripts read `terraform -chdir=infra/terraform output -json`. AWS credentials come from the usual AWS CLI/SDK chain (`AWS_PROFILE`, env vars, SSO).

## Variables

| Variable | Default | Notes |
|---|---|---|
| `region` | `us-east-1` | |
| `availability_zone` | `""` | Empty = first AZ (alphabetical) that offers all three instance types |
| `app_type` | `m7a.2xlarge` | The measured host; never change it within a campaign |
| `db_type` | `c7a.4xlarge` | `c7a.8xlarge` if calibration demands it |
| `loadgen_type` | `c7a.4xlarge` | `c7a.8xlarge` if calibration demands it |
| `operator_ip` | **required** | CIDR allowed to SSH, e.g. `203.0.113.7/32` |
| `grafana_allowed_cidrs` | `["0.0.0.0/0"]` | Who may reach Grafana on 3000. `[]` = SSH tunnel only |
| `grafana_public_view` | `true` | Anonymous Viewer access; `false` = login required (applied by `deploy.sh`) |
| `use_spot` | `false` | Persistent spot, stop on interruption. Dev sessions only |
| `ssh_public_key_path` | `~/.ssh/id_ed25519.pub` | Installed for `ubuntu` on every host |
| `ami_id` | `""` | Empty = latest Canonical Ubuntu 24.04 amd64. Pin it (from `terraform output ami_id`) for the scored campaign |
| `root_volume_gb` | `50` | Room for 7 stack images + build cache on app |
| `k6_version` / `k6_sha256` | `2.2.0` / checksum of the linux-amd64 tarball | Change together |

## Outputs

`public_ips`, `private_ips`, `instance_ids`, `instance_types` (maps keyed by `app`/`db`/`loadgen`), `ami_id`, `availability_zone`, `ssh_commands`, `grafana_url`, `grafana_admin_password` (sensitive: `terraform output -raw grafana_admin_password`), `grafana_public_view`, and `hosts_toml`, the exact content of `runner/hosts.toml` that `deploy.sh` writes on loadgen.

## Behaviour worth knowing

- **Resizing** (`terraform apply -var loadgen_type=c7a.8xlarge`) stops and starts only that instance in place. Its **public IP changes** (no Elastic IPs); private IPs never do. Re-run `../host-setup/deploy.sh` afterwards (it restarts the services and rewrites the targets; idempotent).
- **Stale outputs:** after a stop/start outside Terraform (console, spot interruption), run `terraform apply -refresh-only` so `public_ips` and `grafana_url` are current.
- **AMI and user_data changes are ignored** on existing instances (`lifecycle.ignore_changes`), so a new Ubuntu release or a cloud-init edit never replaces hosts mid-campaign. To apply one on purpose: `terraform apply -replace='aws_instance.host["app"]'` (and pin `ami_id` so all three hosts stay on the same image).
- **Capacity:** a cluster placement group must fit all three instances in one AZ. `InsufficientInstanceCapacity` → retry later, try another `availability_zone`, or use a capacity reservation for the campaign window ([08 risks](../../docs/08-roadmap.md#risks)). The on-demand vCPU quota must cover 40 vCPU (56 with an 8xlarge, 72 with both).
- **Cost:** ≈ $2.1/h for the three hosts plus public IPv4 and gp3 ([08 budget](../../docs/08-roadmap.md#budget-estimate-us-east-1-on-demand-approximate-check-current-pricing)). Nothing here costs money once destroyed.

## Validate without AWS

```bash
docker run --rm -v "$PWD":/w -w /w/infra/terraform hashicorp/terraform:1.16.5 fmt -check
docker run --rm -v "$PWD":/w -w /w/infra/terraform hashicorp/terraform:1.16.5 init -backend=false
docker run --rm -v "$PWD":/w -w /w/infra/terraform hashicorp/terraform:1.16.5 validate
```

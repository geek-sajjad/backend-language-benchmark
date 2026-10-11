variable "region" {
  description = "AWS region for the whole lab."
  type        = string
  default     = "eu-north-1"
}

variable "availability_zone" {
  description = "AZ for the subnet. eu-north-1c had the cheapest c7a/m7a spot prices on 2026-10-10. Empty = the first AZ (alphabetically) that offers all three instance types."
  type        = string
  default     = "eu-north-1c"
}

variable "app_type" {
  description = "Instance type of the measured app host. The results depend on it: never change it within a campaign."
  type        = string
  default     = "c7a.xlarge"
}

variable "db_type" {
  description = "Instance type of the db host (Postgres + downstream mock). Same size as the app: the db may be the bottleneck, and DB-bound results are reported (docs/04)."
  type        = string
  default     = "c7a.xlarge"
}

variable "loadgen_type" {
  description = "Instance type of the loadgen host (k6, runner, Prometheus, Grafana). Graviton (arm64): the loadgen isn't measured, and 16 Graviton cores cost less on spot than 8 AMD cores. Upsize to c7g.8xlarge if calibration demands it."
  type        = string
  default     = "c7g.4xlarge"
}

variable "operator_ip" {
  description = "CIDR allowed to SSH (22) into all hosts, e.g. \"203.0.113.7/32\"."
  type        = string

  validation {
    condition     = can(cidrhost(var.operator_ip, 0))
    error_message = "operator_ip must be a CIDR, e.g. 203.0.113.7/32."
  }
}

variable "grafana_allowed_cidrs" {
  description = "CIDRs allowed to reach Grafana (3000 on loadgen). Empty list = Grafana only through an SSH tunnel."
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = alltrue([for c in var.grafana_allowed_cidrs : can(cidrhost(c, 0))])
    error_message = "Every entry of grafana_allowed_cidrs must be a CIDR."
  }
}

variable "grafana_public_view" {
  description = "Anonymous read-only (Viewer) access to the dashboards. false = login required. Passed to monitoring/compose.yml by deploy.sh."
  type        = bool
  default     = true
}

variable "use_spot" {
  description = "Run all three hosts as (persistent, stop-on-interruption) spot instances. Used for everything, including the scored campaign; the runner pauses on an interruption (docs/06). false = on-demand."
  type        = bool
  default     = true
}

variable "ssh_public_key_path" {
  description = "Operator's SSH public key, installed for the ubuntu user on every host."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ami_id" {
  description = "AMI for the x86_64 hosts (app, db). Empty = latest Canonical Ubuntu 24.04 LTS amd64 (hvm-ssd-gp3). Pin it for the scored campaign."
  type        = string
  default     = ""
}

variable "ami_id_arm64" {
  description = "AMI for arm64 (Graviton) hosts, i.e. loadgen. Empty = latest Canonical Ubuntu 24.04 LTS arm64 (hvm-ssd-gp3). Pin it together with ami_id, from the same Canonical release."
  type        = string
  default     = ""
}

variable "root_volume_gb" {
  description = "gp3 root volume size per host (GiB). IOPS/throughput stay at the free baseline (3000 / 125 MB/s)."
  type        = number
  default     = 50
}

variable "k6_version" {
  description = "k6 release installed on loadgen (GitHub release tarball, checksum-verified in host-setup/bootstrap.sh)."
  type        = string
  default     = "2.2.0"
}

variable "k6_sha256" {
  description = "SHA-256 of k6-v<k6_version>-linux-<arch>.tar.gz per architecture (from the release's checksums.txt). Change it together with k6_version."
  type        = map(string)
  default = {
    amd64 = "b5a8003c86f35f5cd5ceef1490312c48e587696c94d998cefc6d7b3b4cb1597d"
    arm64 = "4ecd64cadcc792402d16293836115480419c4447c032858f564852d98f1bf54c"
  }
}

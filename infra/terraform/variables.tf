variable "region" {
  description = "AWS region for the whole lab."
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = "AZ for the subnet. Empty = the first AZ (alphabetically) that offers all three instance types."
  type        = string
  default     = ""
}

variable "app_type" {
  description = "Instance type of the measured app host. The results depend on it: never change it within a campaign."
  type        = string
  default     = "m7a.2xlarge"
}

variable "db_type" {
  description = "Instance type of the db host (Postgres + downstream mock). Upsize to c7a.8xlarge if calibration demands it."
  type        = string
  default     = "c7a.4xlarge"
}

variable "loadgen_type" {
  description = "Instance type of the loadgen host (k6, runner, Prometheus, Grafana). Upsize to c7a.8xlarge if calibration demands it."
  type        = string
  default     = "c7a.4xlarge"
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
  description = "Run all three hosts as (persistent, stop-on-interruption) spot instances. Dev sessions only: calibration, pilot and the scored campaign use on-demand."
  type        = bool
  default     = false
}

variable "ssh_public_key_path" {
  description = "Operator's SSH public key, installed for the ubuntu user on every host."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ami_id" {
  description = "AMI for all three hosts. Empty = latest Canonical Ubuntu 24.04 LTS amd64 (hvm-ssd-gp3). Pin it for the scored campaign."
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
  description = "SHA-256 of k6-v<k6_version>-linux-amd64.tar.gz (from the release's checksums.txt). Change it together with k6_version."
  type        = string
  default     = "b5a8003c86f35f5cd5ceef1490312c48e587696c94d998cefc6d7b3b4cb1597d"
}

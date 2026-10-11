locals {
  name = "lang-bench"

  # Fixed private IPs, so hosts.toml, Prometheus targets and the docs stay stable across sessions.
  hosts = {
    app     = { type = var.app_type, private_ip = "10.42.0.10" }
    db      = { type = var.db_type, private_ip = "10.42.0.11" }
    loadgen = { type = var.loadgen_type, private_ip = "10.42.0.12" }
  }

  # app and db are x86 (c7a/m7a); loadgen may be Graviton (c7g, arm64). Each host gets the
  # Ubuntu image for its own architecture.
  host_arch = { for role, h in local.hosts : role => contains(data.aws_ec2_instance_type.by_type[h.type].supported_architectures, "arm64") ? "arm64" : "x86_64" }
  ami_ids = {
    x86_64 = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu["x86_64"].id
    arm64  = var.ami_id_arm64 != "" ? var.ami_id_arm64 : try(data.aws_ami.ubuntu["arm64"].id, "")
  }

  # First AZ that offers all three instance types (cluster placement groups live in one AZ).
  common_azs        = sort(tolist(setintersection([for o in data.aws_ec2_instance_type_offerings.by_type : toset(o.locations)]...)))
  availability_zone = var.availability_zone != "" ? var.availability_zone : try(local.common_azs[0], "")
}

# ---------------------------------------------------------------- lookups

data "aws_ec2_instance_type" "by_type" {
  for_each      = toset([var.app_type, var.db_type, var.loadgen_type])
  instance_type = each.value
}

data "aws_ami" "ubuntu" {
  # Only the architectures in use whose AMI isn't pinned.
  for_each = setsubtract(toset(values(local.host_arch)), compact([
    var.ami_id != "" ? "x86_64" : "",
    var.ami_id_arm64 != "" ? "arm64" : "",
  ]))
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-${each.value == "arm64" ? "arm64" : "amd64"}-server-*"]
  }
  filter {
    name   = "architecture"
    values = [each.value]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_ec2_instance_type_offerings" "by_type" {
  for_each      = toset([var.app_type, var.db_type, var.loadgen_type])
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [each.value]
  }
}

# ---------------------------------------------------------------- network

resource "aws_vpc" "bench" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "bench" {
  vpc_id = aws_vpc.bench.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.bench.id
  cidr_block              = "10.42.0.0/24"
  availability_zone       = local.availability_zone
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public" }

  lifecycle {
    precondition {
      condition     = local.availability_zone != ""
      error_message = "No AZ in ${var.region} offers all of ${var.app_type}, ${var.db_type} and ${var.loadgen_type}. Set availability_zone or pick other types."
    }
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.bench.id
  tags   = { Name = "${local.name}-public" }

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.bench.id
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------- security group

resource "aws_security_group" "bench" {
  name        = local.name
  description = "lang-bench: SSH from operator, Grafana from allowed CIDRs, everything else intra-SG only"
  vpc_id      = aws_vpc.bench.id
  tags        = { Name = local.name }
}

resource "aws_vpc_security_group_ingress_rule" "ssh_operator" {
  security_group_id = aws_security_group.bench.id
  description       = "SSH from the operator"
  cidr_ipv4         = var.operator_ip
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "grafana" {
  for_each = toset(var.grafana_allowed_cidrs)

  security_group_id = aws_security_group.bench.id
  description       = "Grafana (loadgen only listens on 3000)"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 3000
  to_port           = 3000
}

# Benchmark traffic, SQL, downstream, scraping and intra-cluster SSH: members of the SG only.
resource "aws_vpc_security_group_ingress_rule" "intra" {
  security_group_id            = aws_security_group.bench.id
  description                  = "All traffic between lab hosts"
  referenced_security_group_id = aws_security_group.bench.id
  ip_protocol                  = "-1"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.bench.id
  description       = "Outbound (apt, docker pull, GitHub)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------------------------------------------------------------- compute

resource "aws_key_pair" "operator" {
  key_name   = local.name
  public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
}

resource "aws_placement_group" "bench" {
  name     = "bench"
  strategy = "cluster"
}

resource "random_password" "grafana_admin" {
  length  = 24
  special = false # passed through env/shell; alphanumeric avoids quoting issues
}

resource "aws_instance" "host" {
  for_each = local.hosts

  ami                         = local.ami_ids[local.host_arch[each.key]]
  instance_type               = each.value.type
  subnet_id                   = aws_subnet.public.id
  private_ip                  = each.value.private_ip
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.bench.id]
  key_name                    = aws_key_pair.operator.key_name
  placement_group             = aws_placement_group.bench.id
  monitoring                  = false # no CloudWatch detailed monitoring
  ebs_optimized               = true

  user_data = templatefile("${path.module}/../host-setup/cloud-init.yaml.tftpl", {
    role          = each.key
    k6_version    = var.k6_version
    k6_sha256     = var.k6_sha256[local.host_arch[each.key] == "arm64" ? "arm64" : "amd64"]
    bootstrap_b64 = filebase64("${path.module}/../host-setup/bootstrap.sh")
  })

  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        spot_instance_type             = "persistent"
        instance_interruption_behavior = "stop"
      }
    }
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    iops                  = 3000
    throughput            = 125
    encrypted             = true
    delete_on_termination = true
    tags                  = { Name = "${local.name}-${each.key}", role = each.key, project = "lang-bench" }
  }

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = { Name = "${local.name}-${each.key}", role = each.key }

  lifecycle {
    # A new Ubuntu AMI release or an edit to cloud-init must never replace hosts mid-campaign
    # (e.g. during `terraform apply -var loadgen_type=...`). Use `terraform apply -replace=...` deliberately.
    ignore_changes = [ami, user_data]
  }

  depends_on = [aws_route_table_association.public]
}

output "public_ips" {
  description = "Public IPv4 per role (SSH from the operator machine; change on every stop/start)."
  value       = { for role, i in aws_instance.host : role => i.public_ip }
}

output "private_ips" {
  description = "Private IPv4 per role (all benchmark traffic)."
  value       = { for role, i in aws_instance.host : role => i.private_ip }
}

output "instance_ids" {
  description = "EC2 instance id per role."
  value       = { for role, i in aws_instance.host : role => i.id }
}

output "instance_types" {
  description = "Instance type per role (for the campaign manifest)."
  value       = { for role, i in aws_instance.host : role => i.instance_type }
}

output "ami_id" {
  description = "AMI of the x86_64 hosts (app, db). Pass it back as -var ami_id=... to keep it for later sessions."
  value       = local.ami_ids["x86_64"]
}

output "ami_id_arm64" {
  description = "AMI of the arm64 hosts (loadgen on Graviton), empty if none. Pass it back as -var ami_id_arm64=..."
  value       = local.ami_ids["arm64"]
}

output "host_arch" {
  description = "CPU architecture per role (for the campaign manifest)."
  value       = local.host_arch
}

output "availability_zone" {
  value = aws_subnet.public.availability_zone
}

output "ssh_commands" {
  description = "Convenience SSH commands from the operator machine."
  value       = { for role, i in aws_instance.host : role => "ssh ubuntu@${i.public_ip}" }
}

output "grafana_url" {
  description = "Public live dashboards on loadgen."
  value       = "http://${aws_instance.host["loadgen"].public_ip}:3000"
}

output "grafana_admin_password" {
  description = "Grafana admin password (user admin). Log in through ssh -L 3000:localhost:3000, never over plain HTTP."
  value       = random_password.grafana_admin.result
  sensitive   = true
}

output "grafana_public_view" {
  description = "Anonymous Viewer access on/off (consumed by deploy.sh)."
  value       = var.grafana_public_view
}

output "hosts_toml" {
  description = "Contents of runner/hosts.toml on loadgen (written by infra/host-setup/deploy.sh)."
  value       = <<-EOT
    mode = "aws"                       # "local" for a single-machine dev setup
    ssh_user = "ubuntu"
    repo_dir = "/home/ubuntu/bench"
    prometheus_url = "http://127.0.0.1:9090"
    net_iface = "${local.net_iface}"
    [hosts.app]
    private_ip = "${aws_instance.host["app"].private_ip}"
    [hosts.db]
    private_ip = "${aws_instance.host["db"].private_ip}"
    [hosts.loadgen]
    private_ip = "${aws_instance.host["loadgen"].private_ip}"
  EOT
}

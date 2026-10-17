#!/usr/bin/env bash
# Host bootstrap, run as root by cloud-init on first boot (see cloud-init.yaml.tftpl).
# Idempotent: safe to re-run by hand, e.g. after editing it:
#   sudo /usr/local/sbin/bench-bootstrap.sh <app|db|loadgen> <k6_version> <k6_sha256>
# Identical on all three hosts except k6, which is installed on loadgen only.
set -euo pipefail

ROLE="${1:?role (app|db|loadgen)}"
K6_VERSION="${2:-2.2.0}"
K6_SHA256="${3:-}"
# Docker packages, pinned: the campaign runs one session per stack on freshly provisioned hosts,
# and `bench.py` refuses a session whose Docker version differs from the first one (docs/06
# "Sessions: one stack per day"). Same versions for amd64 and arm64. To bump, pick the versions
# from `apt-cache madison docker-ce`, and only between campaigns.
DOCKER_VERSION="5:29.9.1-1~ubuntu.24.04~noble"
CONTAINERD_VERSION="2.4.2-2~ubuntu.24.04~noble"
BUILDX_VERSION="0.38.0-1~ubuntu.24.04~noble"
COMPOSE_VERSION="5.6.0-1~ubuntu.24.04~noble"

case "$ROLE" in app | db | loadgen) ;; *) echo "unknown role: $ROLE" >&2; exit 1 ;; esac

export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -y -o DPkg::Lock::Timeout=600 -o Dpkg::Options::=--force-confold)
log() { echo "[bench-bootstrap:$ROLE] $*"; }

# ------------------------------------------------------------ background noise off
# Nothing may upgrade packages or restart services in the middle of a measurement.
log "disabling unattended upgrades and apt timers"
systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl disable --now snap.amazon-ssm-agent.amazon-ssm-agent.service 2>/dev/null || true # no SSM, plain SSH

# ------------------------------------------------------------ packages
log "installing base packages"
"${APT[@]}" update
"${APT[@]}" install ca-certificates curl gnupg ethtool rsync git jq tmux sysstat \
  python3 python3-jwt python3-cryptography postgresql-client

# Docker CE + compose plugin from Docker's apt repository.
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  log "installing Docker CE"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
    >/etc/apt/sources.list.d/docker.list
  "${APT[@]}" update
  "${APT[@]}" install "docker-ce=$DOCKER_VERSION" "docker-ce-cli=$DOCKER_VERSION" \
    "containerd.io=$CONTAINERD_VERSION" "docker-buildx-plugin=$BUILDX_VERSION" \
    "docker-compose-plugin=$COMPOSE_VERSION"
  apt-mark hold docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
usermod -aG docker ubuntu

# ------------------------------------------------------------ kernel / limits (docs/01 "OS")
log "applying sysctls and limits"
cat >/etc/sysctl.d/90-bench.conf <<'EOF'
# lang-bench (infra/host-setup/bootstrap.sh) - identical on every host
net.core.somaxconn = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
fs.file-max = 4194304
fs.nr_open = 1048576
# With the ephemeral range starting at 1024, keep outgoing connections off the service ports
# (otherwise e.g. a db-pool connection could grab 8080 while the app container restarts).
net.ipv4.ip_local_reserved_ports = 3000,5432,8080,8081,9000,9090,9100,9187
EOF
sysctl --system >/dev/null

cat >/etc/security/limits.d/90-bench.conf <<'EOF'
ubuntu soft nofile 1048576
ubuntu hard nofile 1048576
root   soft nofile 1048576
root   hard nofile 1048576
EOF

mkdir -p /etc/systemd/system.conf.d /etc/systemd/user.conf.d \
  /etc/systemd/system/docker.service.d /etc/systemd/system/containerd.service.d
printf '[Manager]\nDefaultLimitNOFILE=1048576\n' >/etc/systemd/system.conf.d/90-bench.conf
printf '[Manager]\nDefaultLimitNOFILE=1048576\n' >/etc/systemd/user.conf.d/90-bench.conf
printf '[Service]\nLimitNOFILE=1048576\n' >/etc/systemd/system/docker.service.d/90-bench.conf
printf '[Service]\nLimitNOFILE=1048576\n' >/etc/systemd/system/containerd.service.d/90-bench.conf

# Default container ulimit (compose files also set ulimits.nofile) and bounded logs on the root volume.
cat >/etc/docker/daemon.json <<'EOF'
{
  "default-ulimits": { "nofile": { "Name": "nofile", "Soft": 1048576, "Hard": 1048576 } },
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" }
}
EOF
systemctl daemon-reload
systemctl restart containerd docker
systemctl enable docker containerd >/dev/null

# ------------------------------------------------------------ swap off (docs/01)
log "disabling swap"
swapoff -a
sed -ri '/^[^#].*\sswap\s/s/^/#/' /etc/fstab

# ------------------------------------------------------------ k6 (loadgen only, pinned + checksum)
if [[ "$ROLE" == loadgen ]]; then
  if [[ "$(k6 version 2>/dev/null || true)" != "k6 v${K6_VERSION} "* ]]; then
    log "installing k6 v${K6_VERSION}"
    k6_arch="$(dpkg --print-architecture)"  # amd64, or arm64 on a Graviton loadgen
    tarball="k6-v${K6_VERSION}-linux-${k6_arch}.tar.gz"
    tmp="$(mktemp -d)"
    curl -fsSL --retry 5 -o "$tmp/$tarball" \
      "https://github.com/grafana/k6/releases/download/v${K6_VERSION}/${tarball}"
    if [[ -n "$K6_SHA256" ]]; then
      echo "${K6_SHA256}  $tmp/$tarball" | sha256sum -c -
    else
      log "WARNING: no k6 checksum given, skipping verification"
    fi
    tar -xzf "$tmp/$tarball" -C "$tmp"
    install -m 0755 "$tmp/k6-v${K6_VERSION}-linux-${k6_arch}/k6" /usr/local/bin/k6
    rm -rf "$tmp"
  fi
  k6 version
fi

# ------------------------------------------------------------ repo dir + marker
install -d -o ubuntu -g ubuntu /home/ubuntu/bench /home/ubuntu/bench/results
install -d /var/lib/bench
{
  echo "role=$ROLE"
  echo "date=$(date -u +%FT%TZ)"
  echo "kernel=$(uname -r)"
  echo "docker=$(docker version --format '{{.Server.Version}}')"
  echo "compose=$(docker compose version --short)"
  [[ "$ROLE" == loadgen ]] && echo "k6=$(k6 version | awk '{print $2}')"
  true
} >/var/lib/bench/bootstrap.done
log "done"
cat /var/lib/bench/bootstrap.done

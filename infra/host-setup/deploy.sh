#!/usr/bin/env bash
# Deploys the repo and the always-on services to the three hosts created by infra/terraform.
# Idempotent: re-run it after any local change, after a stop/start or a resize.
#
#   ./infra/host-setup/deploy.sh
#
# Steps:
#   1. test JWT key pair: generate locally if missing (k6/data/jwt/gen-keys.sh)
#   2. wait for SSH + cloud-init (bootstrap.sh) on all hosts
#   3. rsync the repo to /home/ubuntu/bench on every host (git-ignored files skipped, except
#      k6/data/jwt/public.pem everywhere and private.pem on loadgen only; results/ is never touched)
#   4. intra-cluster SSH: loadgen gets its own key, app/db/loadgen accept it (runner -> hosts)
#   5. loadgen: runner/hosts.toml (terraform output hosts_toml) + Prometheus file_sd targets
#   6. start exporters on all hosts, the downstream mock on db, Prometheus + Grafana on loadgen
#   7. smoke-check every scrape target from loadgen
set -euo pipefail
# shellcheck source=infra/host-setup/lib.sh
source "$(dirname "$0")/lib.sh"

require_cmds rsync openssl python3 ssh ssh-keygen

# ------------------------------------------------------------ preflight
for f in monitoring/compose.yml monitoring/prometheus/gen-targets.sh downstream/compose.yml \
  monitoring/exporters/app.yml monitoring/exporters/db.yml monitoring/exporters/loadgen.yml; do
  [[ -f "$REPO_ROOT/$f" ]] || die "missing $f (needed by deploy)"
done
load_outputs
APP_IP="$(priv_ip app)"
DB_IP="$(priv_ip db)"
LOADGEN_IP="$(priv_ip loadgen)"

# ------------------------------------------------------------ 1. JWT test keys
JWT_DIR="$REPO_ROOT/k6/data/jwt"
if [[ ! -f "$JWT_DIR/private.pem" ]]; then
  log "generating test JWT key pair"
  "$JWT_DIR/gen-keys.sh"
elif [[ ! -f "$JWT_DIR/public.pem" ]]; then
  openssl pkey -in "$JWT_DIR/private.pem" -pubout -out "$JWT_DIR/public.pem"
  chmod 644 "$JWT_DIR/public.pem"
fi

# ------------------------------------------------------------ 2. hosts ready
for role in $ROLES; do wait_for_host "$role"; done

# ------------------------------------------------------------ 3. repo
RSYNC_COMMON=(
  -az
  --exclude=/results/ # written on the hosts (results, image digests); fetch-results.sh pulls them
  --exclude=/runner/hosts.toml
  --exclude=/infra/host-setup/.state/
  --exclude=.terraform/ --exclude='*.tfstate' --exclude='*.tfstate.*' --exclude='*.tfvars'
  --include=/k6/data/jwt/public.pem
)
for role in $ROLES; do
  log "$role: rsync repo -> $REMOTE_REPO"
  keyrule=(--exclude=/k6/data/jwt/private.pem)
  [[ "$role" == loadgen ]] && keyrule=(--include=/k6/data/jwt/private.pem)
  # No --delete: files removed locally linger on the hosts (rebuild hosts for a clean slate).
  rsync "${RSYNC_COMMON[@]}" "${keyrule[@]}" --filter=':- .gitignore' \
    -e "$(ssh_cmd_string)" "$REPO_ROOT/" "$SSH_USER@$(pub_ip "$role"):$REMOTE_REPO/"
done

local_pub_sha="$(openssl dgst -sha256 -r "$JWT_DIR/public.pem" | cut -d' ' -f1)"
for role in app loadgen; do
  remote_sha="$(rssh "$role" "sha256sum $REMOTE_REPO/k6/data/jwt/public.pem" | cut -d' ' -f1)"
  [[ "$remote_sha" == "$local_pub_sha" ]] || die "$role: k6/data/jwt/public.pem differs from local copy"
done
rssh loadgen "test -f $REMOTE_REPO/k6/data/jwt/private.pem" || die "loadgen: private.pem missing"

# ------------------------------------------------------------ 4. intra-cluster SSH
log "loadgen: cluster SSH key"
LOADGEN_PUBKEY="$(rbash loadgen <<'EOF'
set -euo pipefail
[[ -f ~/.ssh/id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C bench-loadgen -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
EOF
)"
for role in $ROLES; do
  rbash "$role" "$LOADGEN_PUBKEY" <<'EOF'
set -euo pipefail
install -d -m 700 ~/.ssh
touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
grep -qxF "$1" ~/.ssh/authorized_keys || echo "$1" >>~/.ssh/authorized_keys
EOF
done
rbash loadgen "$APP_IP" "$DB_IP" "$LOADGEN_IP" <<'EOF'
set -euo pipefail
cat >~/.ssh/config <<'CFG'
# written by infra/host-setup/deploy.sh: runner -> lab hosts over private IPs
Host 10.42.0.*
  User ubuntu
  IdentityFile ~/.ssh/id_ed25519
  StrictHostKeyChecking accept-new
  ServerAliveInterval 30
CFG
chmod 600 ~/.ssh/config
touch ~/.ssh/known_hosts
for ip in "$@"; do
  ssh-keygen -R "$ip" >/dev/null 2>&1 || true
  ssh-keyscan -t ed25519 "$ip" 2>/dev/null >>~/.ssh/known_hosts
  ssh -o BatchMode=yes "ubuntu@$ip" true || { echo "loadgen cannot SSH to $ip" >&2; exit 1; }
done
echo "loadgen -> $* : SSH ok"
EOF

# ------------------------------------------------------------ 5. runner config + scrape targets
log "loadgen: runner/hosts.toml + Prometheus targets"
tf_get hosts_toml | rssh loadgen "cat >$REMOTE_REPO/runner/hosts.toml"
rssh loadgen "cd $REMOTE_REPO && bash monitoring/prometheus/gen-targets.sh $APP_IP $DB_IP $LOADGEN_IP"

# ------------------------------------------------------------ 6. services
log "db: exporters + downstream mock"
rbash db <<EOF
set -euo pipefail
cd $REMOTE_REPO
docker compose -f monitoring/exporters/db.yml up -d
docker compose -f downstream/compose.yml up -d --build
EOF

log "app: exporters"
rbash app <<EOF
set -euo pipefail
cd $REMOTE_REPO
docker compose -f monitoring/exporters/app.yml up -d
EOF

log "loadgen: exporters + Prometheus + Grafana"
GRAFANA_PW="$(tf_get grafana_admin_password)"
GRAFANA_PUBLIC="$(tf_get grafana_public_view)"
# The password travels in the script text (stdin), not on a command line.
rbash loadgen <<EOF
set -euo pipefail
cd $REMOTE_REPO
export MONITORING_CPUSET=$(printf '%q' "${MONITORING_CPUSET:-14-15}")  # also pins the loadgen exporters
docker compose -f monitoring/exporters/loadgen.yml up -d
export GRAFANA_ADMIN_PASSWORD=$(printf '%q' "$GRAFANA_PW")
export GRAFANA_PUBLIC_VIEW=$GRAFANA_PUBLIC
docker compose -f monitoring/compose.yml up -d
EOF

# ------------------------------------------------------------ 7. smoke check (from loadgen, private IPs)
log "checking scrape targets from loadgen"
rbash loadgen "$APP_IP" "$DB_IP" "$LOADGEN_IP" <<'EOF'
set -euo pipefail
app=$1 db=$2 lg=$3
targets="$app:9100 $app:8081 $db:9100 $db:9187 $db:9000 $lg:9100 127.0.0.1:9090 127.0.0.1:3000"
deadline=$((SECONDS + 120))
for t in $targets; do
  until (exec 3<>"/dev/tcp/${t%:*}/${t#*:}") 2>/dev/null; do
    ((SECONDS < deadline)) || { echo "NOT listening: $t" >&2; exit 1; }
    sleep 2
  done
  echo "ok  $t"
done
until curl -fsS http://127.0.0.1:9090/-/ready >/dev/null; do
  ((SECONDS < deadline)) || { echo "Prometheus not ready" >&2; exit 1; }
  sleep 2
done
curl -fsS http://127.0.0.1:3000/api/health >/dev/null && echo "ok  grafana /api/health"
EOF

log "deploy done"
echo "  Grafana:  $(tf_get grafana_url)   (admin password: terraform -chdir=infra/terraform output -raw grafana_admin_password)"
echo "  Next:     ./infra/host-setup/seed-db.sh && ./infra/host-setup/build-images.sh"
echo "  SSH:      ssh ubuntu@$(pub_ip loadgen)"

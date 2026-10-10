# shellcheck shell=bash
# shellcheck disable=SC2034  # variables are used by the scripts that source this file
# Shared helpers for the operator-side scripts in infra/host-setup/. Sourced, not executed.
#
# Inputs (environment, all optional):
#   TERRAFORM             terraform command (default "terraform"); may be a wrapper with spaces, e.g.
#                         "docker run --rm -v $PWD:$PWD -w $PWD hashicorp/terraform:1.16.5"
#                         (mount the repo at the SAME path: -chdir uses the absolute host path)
#   BENCH_TF_OUTPUT_JSON  path to a saved `terraform output -json`; skips calling terraform
#   BENCH_SSH_KEY         private key for the operator -> host SSH (default: ssh-agent / ~/.ssh defaults)

HOST_SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HOST_SETUP_DIR/../.." && pwd)"
STATE_DIR="$HOST_SETUP_DIR/.state"
REMOTE_REPO=/home/ubuntu/bench
SSH_USER=ubuntu
ROLES="app db loadgen"

log() { printf '\033[1m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

require_cmds() {
  local c
  for c in "$@"; do command -v "$c" >/dev/null || die "'$c' is required on this machine"; done
}

# tf_get <output> [key...]  -> prints the (nested) value of a terraform output
tf_get() {
  python3 -c '
import json, sys
v = json.load(sys.stdin)[sys.argv[1]]["value"]
for k in sys.argv[2:]:
    v = v[k]
sys.stdout.write(str(v).lower() if isinstance(v, bool) else str(v))
' "$@" <<<"$TF_JSON"
}

pub_ip() { tf_get public_ips "$1"; }
priv_ip() { tf_get private_ips "$1"; }

# Reads terraform outputs once and prepares SSH options. Call before any remote helper.
load_outputs() {
  require_cmds python3 ssh
  if [[ -n "${BENCH_TF_OUTPUT_JSON:-}" ]]; then
    TF_JSON="$(cat "$BENCH_TF_OUTPUT_JSON")"
  else
    local tf
    read -r -a tf <<<"${TERRAFORM:-terraform}"
    command -v "${tf[0]}" >/dev/null ||
      die "terraform not found (install Terraform >= 1.9, or set TERRAFORM / BENCH_TF_OUTPUT_JSON)"
    TF_JSON="$("${tf[@]}" -chdir="$REPO_ROOT/infra/terraform" output -json)" ||
      die "terraform output failed (did you run terraform apply?)"
  fi
  tf_get public_ips loadgen >/dev/null 2>&1 || die "terraform outputs are empty: run terraform apply first"

  # Host keys are pinned per deployment (keyed by the loadgen instance id): public IPs get
  # recycled by AWS between sessions, so one long-lived known_hosts file would go stale.
  mkdir -p "$STATE_DIR"
  KNOWN_HOSTS="$STATE_DIR/known_hosts-$(tf_get instance_ids loadgen)"
  SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$KNOWN_HOSTS"
    -o ConnectTimeout=10 -o ServerAliveInterval=30 -o LogLevel=ERROR)
  if [[ -n "${BENCH_SSH_KEY:-}" ]]; then SSH_OPTS+=(-i "$BENCH_SSH_KEY"); fi
}

# rssh <role> <command...>   run a command on a host (as ubuntu)
rssh() {
  local role=$1
  shift
  # shellcheck disable=SC2029  # client-side expansion is intended
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$(pub_ip "$role")" "$@"
}

# rbash <role> [args...] <<'EOF' ... EOF   run a bash script from stdin on a host with args.
# Secrets belong in the script text (stdin), never in args (args are visible in `ps`).
# The script is spooled to a private temp file first, so commands inside it that read stdin
# (ssh, docker ...) cannot swallow the rest of the script the way they would with `bash -s`.
rbash() {
  local role=$1 a quoted=""
  shift
  for a in "$@"; do quoted+=" $(printf '%q' "$a")"; done
  # shellcheck disable=SC2016  # expanded remotely
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$(pub_ip "$role")" \
    'f=$(mktemp) && cat >"$f" && bash "$f"'"$quoted"' </dev/null; rc=$?; rm -f "$f"; exit $rc'
}

# String form of the ssh command for rsync -e.
ssh_cmd_string() {
  local s="ssh" o
  for o in "${SSH_OPTS[@]}"; do s+=" $(printf '%q' "$o")"; done
  printf '%s' "$s"
}

# wait_for_host <role>: SSH reachable, cloud-init finished, bootstrap marker present.
wait_for_host() {
  local role=$1 deadline=$((SECONDS + 900)) status
  log "$role ($(pub_ip "$role")): waiting for SSH"
  until rssh "$role" true 2>/dev/null; do
    ((SECONDS < deadline)) || die "$role: SSH not reachable after 15 min (operator_ip correct? key loaded?)"
    sleep 5
  done
  log "$role: waiting for cloud-init"
  status="$(rssh "$role" 'cloud-init status --wait >/dev/null 2>&1; cloud-init status' || true)"
  if ! rssh "$role" 'test -f /var/lib/bench/bootstrap.done'; then
    rssh "$role" 'sudo tail -n 40 /var/log/cloud-init-output.log' >&2 || true
    die "$role: bootstrap did not finish ($status). Fix, then: sudo /usr/local/sbin/bench-bootstrap.sh ..."
  fi
  log "$role: ready ($(rssh "$role" 'tr "\n" " " </var/lib/bench/bootstrap.done'))"
}

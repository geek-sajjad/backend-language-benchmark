# TEMPORARY db sizing test (docs/tmp-db-sizing-plan.md). Sourced by the other scripts.
# Everything lives in us-east-1, tagged Project=db-sizing, created with the AWS CLI only
# (never touches infra/terraform/).
set -euo pipefail

export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
AZ="${AZ:-us-east-1c}"
SUBNET="${SUBNET:-subnet-0d925e710ad3ffd5c}"
VPC="${VPC:-vpc-06398f75f7d4ef5a0}"
TAG=db-sizing
KEY_NAME=db-sizing
SG_NAME=db-sizing
PG_NAME=db-sizing
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
AMI_PARAM=/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
K6_VERSION=2.2.0
K6_SHA256=b5a8003c86f35f5cd5ceef1490312c48e587696c94d998cefc6d7b3b4cb1597d
SELF_DESTRUCT_MIN=60

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
STATE="$HERE/.state"
RESULTS="$REPO/results/sizing/db-2026-10"
mkdir -p "$STATE"

log() { echo "[db-sizing $(date +%T)] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ------------------------------------------------------------ ssh helpers (by instance name)
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$STATE/known_hosts"
  -o ConnectTimeout=10 -o ServerAliveInterval=15 -o LogLevel=ERROR
  # One multiplexed connection per host: port 22 is open to the internet, and scanner traffic made
  # sshd drop new handshakes (MaxStartups) at the end of phase 1 attempt 2.
  -o ControlMaster=auto -o ControlPath="$HOME/.ssh/cm-dbs-%C" -o ControlPersist=60m)
ip_of() { cat "$STATE/$1.public_ip"; }
private_ip_of() { cat "$STATE/$1.private_ip"; }
rsh() { local n="$1"; shift; ssh "${SSH_OPTS[@]}" "ubuntu@$(ip_of "$n")" "$@"; }
rsync_to() { # rsync_to <name> <dest-dir> <src>... (paths relative to the repo root)
  local n="$1" dest="$2"; shift 2
  (cd "$REPO" && rsync -az --relative --exclude target/ --exclude __pycache__/ -e "ssh ${SSH_OPTS[*]}" \
    "$@" "ubuntu@$(ip_of "$n"):$dest/")
}
rsync_from() { # rsync_from <name> <remote-dir> <local-dir>, retried: the results matter most
  local i
  for i in 1 2 3 4 5; do
    rsync -az -e "ssh ${SSH_OPTS[*]}" "ubuntu@$(ip_of "$1"):$2/" "$3/" && return 0
    log "rsync from $1 failed (attempt $i), retrying"
    sleep 5
  done
  return 1
}

# ------------------------------------------------------------ shared resources (idempotent)
ensure_shared() {
  if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
    log "importing key pair $KEY_NAME from $SSH_KEY.pub"
    aws ec2 import-key-pair --key-name "$KEY_NAME" --public-key-material "fileb://$SSH_KEY.pub" \
      --tag-specifications "ResourceType=key-pair,Tags=[{Key=Project,Value=$TAG}]" >/dev/null
  fi

  SG_ID="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC" \
    --query 'SecurityGroups[0].GroupId' --output text)"
  if [[ "$SG_ID" == None || -z "$SG_ID" ]]; then
    log "creating security group $SG_NAME"
    SG_ID="$(aws ec2 create-security-group --group-name "$SG_NAME" --description "db sizing test (temporary)" \
      --vpc-id "$VPC" --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=$TAG}]" \
      --query GroupId --output text)"
    aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
      --ip-permissions "IpProtocol=-1,UserIdGroupPairs=[{GroupId=$SG_ID}]" >/dev/null
  fi
  # SSH from anywhere (key-only, hosts self-destruct after 60 min): the user's egress IP rotates
  # between addresses per connection, so a /32 of "my IP" breaks.
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 22 --cidr 0.0.0.0/0 \
    >/dev/null 2>&1 || true # already there
  echo "$SG_ID" >"$STATE/sg_id"

  if ! aws ec2 describe-placement-groups --group-names "$PG_NAME" >/dev/null 2>&1; then
    log "creating cluster placement group $PG_NAME"
    aws ec2 create-placement-group --group-name "$PG_NAME" --strategy cluster \
      --tag-specifications "ResourceType=placement-group,Tags=[{Key=Project,Value=$TAG}]" >/dev/null
  fi
}

# ------------------------------------------------------------ launch <name> <type> <bootstrap-role>
user_data() { # role
  cat <<EOF
#!/bin/bash
# Self-destruct first, before anything that could fail.
shutdown -h +$SELF_DESTRUCT_MIN
# Port 22 is open to the internet: don't let scanners exhaust sshd's unauthenticated slots.
printf 'MaxStartups 200:30:400\\nLoginGraceTime 20\\n' >/etc/ssh/sshd_config.d/90-db-sizing.conf
systemctl restart ssh || true
echo '$(base64 -w0 "$REPO/infra/host-setup/bootstrap.sh")' | base64 -d >/usr/local/sbin/bench-bootstrap.sh
chmod 0755 /usr/local/sbin/bench-bootstrap.sh
/usr/local/sbin/bench-bootstrap.sh $1 $K6_VERSION $K6_SHA256 >/var/log/bench-bootstrap.log 2>&1
EOF
}

launch() {
  local name="$1" type="$2" role="${3:-db}"
  ensure_shared
  local ami; ami="$(aws ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text)"
  local tags="{Key=Project,Value=$TAG},{Key=Name,Value=db-sizing-$name}"
  log "launching $name ($type spot, $AZ, $ami)"
  local id
  id="$(aws ec2 run-instances --image-id "$ami" --instance-type "$type" --key-name "$KEY_NAME" \
    --instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}' \
    --instance-initiated-shutdown-behavior terminate \
    --placement "AvailabilityZone=$AZ,GroupName=$PG_NAME" \
    --network-interfaces "DeviceIndex=0,SubnetId=$SUBNET,Groups=$(cat "$STATE/sg_id"),AssociatePublicIpAddress=true,DeleteOnTermination=true" \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20,VolumeType=gp3,DeleteOnTermination=true}' \
    --tag-specifications "ResourceType=instance,Tags=[$tags]" "ResourceType=volume,Tags=[$tags]" \
      "ResourceType=spot-instances-request,Tags=[$tags]" "ResourceType=network-interface,Tags=[$tags]" \
    --user-data "$(user_data "$role")" \
    --query 'Instances[0].InstanceId' --output text)"
  echo "$id" >"$STATE/$name.id"
  echo "$type" >"$STATE/$name.type"
  aws ec2 wait instance-running --instance-ids "$id"
  aws ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text >"$STATE/$name.public_ip"
  aws ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].PrivateIpAddress' \
    --output text >"$STATE/$name.private_ip"
  aws ec2 describe-spot-price-history --instance-types "$type" --availability-zone "$AZ" \
    --product-descriptions Linux/UNIX --start-time "$(date -u +%FT%TZ)" \
    --query 'SpotPriceHistory[0].SpotPrice' --output text >"$STATE/$name.spot_price"
  date -u +%FT%TZ >"$STATE/$name.launched"
  log "$name is $id at $(ip_of "$name") (private $(private_ip_of "$name")), spot \$$(cat "$STATE/$name.spot_price")/h"
}

# Wait for SSH, then for user-data (bootstrap) to finish; refuse to continue without the self-destruct.
wait_ssh() {
  local name="$1" deadline=$((SECONDS + 300))
  until rsh "$name" true 2>/dev/null; do
    ((SECONDS < deadline)) || die "$name: no SSH after 5 min"
    sleep 5
  done
  log "$name: SSH up, waiting for bootstrap"
  rsh "$name" 'cloud-init status --wait >/dev/null; test -f /var/lib/bench/bootstrap.done' ||
    die "$name: bootstrap failed (see /var/log/bench-bootstrap.log)"
  # Bootstrap added ubuntu to the docker group; drop the multiplexed connection opened before
  # that, so the next login (and every command sharing it) has the group.
  ssh "${SSH_OPTS[@]}" -O exit "ubuntu@$(ip_of "$name")" 2>/dev/null || true
  rsh "$name" 'test -f /run/systemd/shutdown/scheduled' || die "$name: self-destruct not scheduled"
  rsh "$name" 'id -nG | grep -qw docker' || die "$name: ubuntu is not in the docker group"
  log "$name: ready, self-destruct at $(rsh "$name" "date -u -d @\$(( \$(sed -n 's/^USEC=//p' /run/systemd/shutdown/scheduled) / 1000000 )) +%T")Z"
}

# ------------------------------------------------------------ teardown
tagged_instances() {
  aws ec2 describe-instances --filters "Name=tag:Project,Values=$TAG" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
    --query 'Reservations[].Instances[].InstanceId' --output text
}

teardown_instances() {
  local ids; ids="$(tagged_instances)"
  if [[ -n "$ids" ]]; then
    log "terminating $ids"
    # shellcheck disable=SC2086
    aws ec2 terminate-instances --instance-ids $ids >/dev/null
    # shellcheck disable=SC2086
    aws ec2 wait instance-terminated --instance-ids $ids
  fi
  local reqs
  reqs="$(aws ec2 describe-spot-instance-requests --filters "Name=tag:Project,Values=$TAG" "Name=state,Values=open,active" \
    --query 'SpotInstanceRequests[].SpotInstanceRequestId' --output text)"
  # shellcheck disable=SC2086
  [[ -n "$reqs" ]] && aws ec2 cancel-spot-instance-requests --spot-instance-request-ids $reqs >/dev/null
  for f in "$STATE"/*.launched; do
    [[ -e "$f" && ! -e "${f%.launched}.terminated" ]] && date -u +%FT%TZ >"${f%.launched}.terminated"
  done
  [[ -z "$(tagged_instances)" ]] || die "instances still alive: $(tagged_instances)"
  log "no db-sizing instances left"
}

# Copy launch/termination times and spot prices into a results dir (for the cost line in the summary).
save_meta() { # save_meta <out-dir> <name>...
  local out="$1" n; shift
  for n in "$@"; do
    printf '{"name":"%s","id":"%s","type":"%s","az":"%s","spot_price":"%s","launched":"%s","terminated":"%s"}\n' \
      "$n" "$(cat "$STATE/$n.id")" "$(cat "$STATE/$n.type")" "$AZ" "$(cat "$STATE/$n.spot_price")" \
      "$(cat "$STATE/$n.launched")" "$(cat "$STATE/$n.terminated" 2>/dev/null)"
  done >"$out/instances.jsonl"
}

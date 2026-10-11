#!/usr/bin/env bash
# k6 sizing test: how many req/s k6 sustains per Graviton core, and what limits it.
#   echo:    c7a.xlarge (4 cores), the Go downstream mock with DELAY_MS=0 (target, must not limit)
#   loadgen: c7g.xlarge (4 Graviton cores), k6 on 1-4 cores (remote/matrix.py)
# Fits the current 8-vCPU quota. Results: results/k6-sizing-2026-10/. Hosts self-destruct after
# SELF_DESTRUCT_MIN; teardown.sh runs at the end (and on error).
source "$(dirname "$0")/common.sh"
trap 'log "error: tearing down"; teardown_instances' ERR

# SCRIPT=matrix.py (default: max rate per variant) or SCRIPT=repeat.py (GC follow-up, results in repeat/)
SCRIPT="${SCRIPT:-matrix.py}"
OUT_DIR="$RESULTS"; [[ "$SCRIPT" == matrix.py ]] || OUT_DIR="$RESULTS/${SCRIPT%.py}"
mkdir -p "$OUT_DIR"
# Reuse hosts from an earlier attempt if they are still running (REUSE=1).
reuse() { [[ "${REUSE:-0}" == 1 && -f "$STATE/$1.id" ]] &&
  [[ "$(aws ec2 describe-instances --instance-ids "$(cat "$STATE/$1.id")" --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null)" == running ]]; }
reuse echo || launch echo c7a.xlarge app amd64
reuse loadgen || launch loadgen c7g.xlarge loadgen arm64
wait_ssh echo
wait_ssh loadgen
# Primary interface: ens5 on Graviton, enp39s0 on c7a with the current Ubuntu AMI.
IFACE='$(ip -o route get 1.1.1.1 | sed -n "s/.* dev \([^ ]*\).*/\1/p")'

log "echo: build and start the downstream mock (DELAY_MS=0)"
rsync_to echo /home/ubuntu/bench downstream
rsh echo 'cd ~/bench && docker build -q -t bench-downstream:local downstream >/dev/null && docker rm -f echo >/dev/null 2>&1;
  docker run -d --name echo --network host --restart unless-stopped -e LISTEN_ADDR=:8080 -e DELAY_MS=0 bench-downstream:local >/dev/null'
ECHO_URL="http://$(private_ip_of echo):8080"
rsh loadgen "for i in \$(seq 1 30); do curl -sf $ECHO_URL/health >/dev/null && exit 0; sleep 1; done; exit 1" ||
  die "echo server not reachable from loadgen"

log "loadgen: k6 scripts (current, pre-optimization, prealloc variant), Prometheus image"
rsync_to loadgen /home/ubuntu/bench k6/lib k6/tests infra/k6-sizing/remote
tmp="$(mktemp -d)"
git -C "$REPO" archive 66d9345 k6/lib k6/tests | tar -x -C "$tmp"
mv "$tmp/k6" "$tmp/k6-old"
rsync -az -e "ssh ${SSH_OPTS[*]}" "$tmp/k6-old" "ubuntu@$(ip_of loadgen):/home/ubuntu/bench/"
rm -rf "$tmp"
rsh loadgen 'cd ~/bench && rm -rf k6-prealloc && cp -r k6 k6-prealloc &&
  sed -i "s/Math.ceil(rate \* perIteration \* 0.1)/Math.ceil(rate * perIteration * 0.5)/" k6-prealloc/lib/options.js &&
  grep -q "perIteration \* 0.5" k6-prealloc/lib/options.js && docker pull -q prom/prometheus:v3.15.0 >/dev/null'

log "hardware and counters before"
for n in echo loadgen; do
  rsh "$n" "lscpu | grep -E 'Model name|^CPU\(s\)'; nproc; uname -r; echo iface $IFACE; ethtool -S $IFACE | grep allowance" >"$OUT_DIR/$n-before.txt"
done
rsh loadgen 'k6 version' >>"$OUT_DIR/loadgen-before.txt"
rsh echo 'nohup mpstat 5 >/tmp/mpstat.log 2>&1 </dev/null &'

log "running the matrix on loadgen (about 40-60 min)"
rsh loadgen "cd ~/bench && rm -rf out && nohup python3 infra/k6-sizing/remote/$SCRIPT $ECHO_URL out >matrix.log 2>&1 </dev/null &"
until rsh loadgen 'test -f ~/bench/out/done'; do
  sleep 30
  rsh loadgen 'pgrep -f infra/k6-sizing/remote/ >/dev/null' || { rsh loadgen 'tail -20 ~/bench/matrix.log'; die "$SCRIPT died"; }
  rsh loadgen 'tail -1 ~/bench/matrix.log' >&2 || true
done

log "collecting results"
rsync_from loadgen /home/ubuntu/bench/out "$OUT_DIR"
rsh loadgen 'cat ~/bench/matrix.log' >"$OUT_DIR/matrix.log"
rsh echo 'cat /tmp/mpstat.log' >"$OUT_DIR/echo-mpstat.log"
for n in echo loadgen; do
  rsh "$n" "ethtool -S $IFACE | grep allowance" >"$OUT_DIR/$n-after.txt"
done
rm -f "$OUT_DIR"/*.summary.json  # one per run; runs.jsonl has what matters

teardown_instances
save_meta "$OUT_DIR" echo loadgen
log "done: $OUT_DIR"

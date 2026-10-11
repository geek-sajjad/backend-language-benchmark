#!/usr/bin/env bash
# Phase 2: Postgres maximum on 2× c7a.xlarge (docs/tmp-db-sizing-plan.md). ~30 min, ~$0.10.
# Start only after every phase 1 instance is terminated.
source "$(dirname "$0")/common.sh"
OUT="$RESULTS/phase2"
mkdir -p "$OUT"
[[ -z "$(tagged_instances)" ]] || die "db-sizing instances still exist: $(tagged_instances)"
rm -f "$STATE"/db.* "$STATE"/client.*

on_exit() {
  local rc=$?
  set +e
  [[ -s "$STATE/db.public_ip" ]] && rsync_from db bench/out "$OUT"
  [[ -s "$STATE/client.public_ip" ]] && rsync_from client bench/out "$OUT"
  "$HERE/teardown.sh"
  [[ -s "$STATE/db.id" ]] && save_meta "$OUT" db client
  [[ $rc == 0 ]] && python3 "$HERE/summarize.py" phase2 "$RESULTS" # after save_meta, for the cost lines
  exit "$rc"
}
trap on_exit EXIT

launch db c7a.xlarge db
launch client c7a.xlarge db # bootstrap role db = Docker + sysstat + ethtool, no k6
wait_ssh db
wait_ssh client
rsync_to db bench db/ infra/db-sizing/remote/
rsync_to client bench runner/pgbench/ infra/db-sizing/remote/
rsh client 'docker pull -q postgres:18.6' >/dev/null &
pull=$!
rsh db 'bash ~/bench/infra/db-sizing/remote/phase2-db.sh setup'
wait "$pull"
DB_IP="$(private_ip_of db)"

# run <label> <test> <clients> <seconds> [-C] [wal]; recorded runs sample the db 4 s in, for seconds - 8.
run() {
  local label="$1" test="$2" clients="$3" secs="$4" connect="${5:-}" wal="${6:-}"
  log "run $label: test$test, $clients clients, ${secs}s ${connect}"
  rsh client "bash ~/bench/infra/db-sizing/remote/phase2-client.sh pgbench $label $DB_IP $test $clients $secs $connect" &
  local pg=$!
  if [[ "$label" != warmup* ]]; then
    sleep 4
    rsh db "bash ~/bench/infra/db-sizing/remote/phase2-db.sh sample $label $((secs - 8)) $wal"
  fi
  wait "$pg"
}

run warmup-t2 2 64 15
run warmup-t3 3 64 15
run r01 2 64 30
run r02 2 128 30
run r03 3 64 30
run r04 3 128 30
run r05 4 64 30
run r06 4 128 30
run r07 2 64 30 -C
run r08 2 64 30
run r09 2 64 30
rsh db 'bash ~/bench/infra/db-sizing/remote/phase2-db.sh wal-timing-on'
run r10 5 64 30 "" wal

rsync_from db bench/out "$OUT"
rsync_from client bench/out "$OUT"
log "phase 2 done; results in $OUT. Teardown runs now."

#!/usr/bin/env bash
# Phase 1: Rust CPU per request on 1× m7a.2xlarge (docs/tmp-db-sizing-plan.md). ~30 min, ~$0.11.
source "$(dirname "$0")/common.sh"
OUT="$RESULTS/phase1"
mkdir -p "$OUT"
[[ -z "$(tagged_instances)" ]] || die "db-sizing instances already exist: $(tagged_instances)"
rm -f "$STATE"/rust.*

on_exit() {
  local rc=$?
  set +e
  [[ -s "$STATE/rust.public_ip" ]] && rsync_from rust bench/out "$OUT" # partial results too
  teardown_instances # keeps the security group, key pair and placement group for phase 2
  [[ -s "$STATE/rust.id" ]] && save_meta "$OUT" rust
  [[ $rc == 0 ]] && python3 "$HERE/summarize.py" phase1 "$RESULTS" # after save_meta, for the cost lines
  exit "$rc"
}
trap on_exit EXIT

launch rust m7a.2xlarge loadgen # bootstrap role loadgen = Docker + k6 + psql + python3-jwt
wait_ssh rust
rsync_to rust bench db/ stacks/rust-axum/ k6/ conformance/ runner/pgbench/ downstream/ infra/db-sizing/remote/
H='bash ~/bench/infra/db-sizing/remote/phase1-host.sh'
rsh rust "$H setup"
rsh rust "$H conformance" || die "conformance failed, tearing down"
rsh rust "$H measure"
rsync_from rust bench/out "$OUT"
log "phase 1 done; results in $OUT"

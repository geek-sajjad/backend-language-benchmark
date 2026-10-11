#!/usr/bin/env bash
# Phase 2, runs ON the client c7a.xlarge: pgbench <label> <db-ip> <test> <clients> <seconds> [-C]
set -euo pipefail
OUT=~/bench/out/raw
mkdir -p "$OUT"

pgbench() {
  local label="$1" db="$2" test="$3" clients="$4" secs="$5" connect="${6:-}"
  # Client CPU over roughly the window the db samples (3 s in, secs - 6 long).
  (sleep 3; LC_ALL=C mpstat 1 $((secs - 6)) >"$OUT/$label.client.mpstat") &
  local mp=$!
  # shellcheck disable=SC2086
  docker run --rm --network host -e PGPASSWORD=bench -v ~/bench/runner/pgbench/test$test.sql:/s.sql:ro postgres:18.6 \
    pgbench -h "$db" -U bench -d bench -n -M prepared -c "$clients" -j 4 -T "$secs" -P 5 $connect -f /s.sql \
    >"$OUT/$label.pgbench.txt" 2>&1
  wait "$mp"
  grep -E '^(tps|latency|number of (transactions|failed))' "$OUT/$label.pgbench.txt" | sed "s/^/  [$label] /"
}

"$@"

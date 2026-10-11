#!/usr/bin/env bash
# Phase 2, runs ON the db c7a.xlarge: setup | sample <label> <seconds> [wal] | wal-timing-on
set -euo pipefail
cd ~/bench
OUT=~/bench/out/raw
mkdir -p "$OUT"
export PGPASSWORD=bench PGHOST=127.0.0.1 PGUSER=bench
log() { echo "[phase2-db $(date +%T)] $*" >&2; }
IFACE=$(ip route show default | awk '{print $5; exit}')

setup() {
  log "starting Postgres (shared_buffers 2GB, effective_cache_size 6GB) and waiting for the seed"
  PG_SHARED_BUFFERS=2GB PG_EFFECTIVE_CACHE_SIZE=6GB docker compose -f db/compose.yml up -d
  local i
  for i in $(seq 1 180); do
    pg_isready -q -h 127.0.0.1 -U bench -d bench && break
    [[ $i == 180 ]] && { log "seed did not finish in 15 min"; exit 1; }
    sleep 5
  done
  log "seeded after ~$((i * 5)) s; reset + prewarm"
  psql -q -d postgres -f db/reset.sql >/dev/null
  psql -d bench -c "SELECT
      (SELECT count(*) FROM categories) AS categories, (SELECT count(*) FROM products) AS products,
      (SELECT count(*) FROM customers) AS customers, (SELECT count(*) FROM orders) AS orders,
      (SELECT count(*) FROM order_items) AS order_items,
      pg_size_pretty(pg_database_size('bench')) AS bench_size, pg_database_size('bench') AS bench_bytes,
      current_setting('shared_buffers') AS shared_buffers, current_setting('effective_cache_size') AS effective_cache_size,
      version()" -x | tee ~/bench/out/db-info.txt
  { echo "iface=$IFACE"; nproc; free -g; } >>~/bench/out/db-info.txt
}

tx_bytes() { sed -n "s/^ *$IFACE: *//p" /proc/net/dev | awk '{print $9}'; }
allowance() { ethtool -S "$IFACE" | awk '/allowance_exceeded/ {s += $2} END {print s + 0}'; }
wal_io() { psql -Atd bench -c "SELECT coalesce(sum(fsyncs), 0) || ' ' || coalesce(sum(fsync_time), 0) FROM pg_stat_io WHERE object = 'wal'"; }

# Samples the db host for <seconds>; the caller starts it ~4 s after launching pgbench (ssh + docker
# start take ~2 s), so the window sits inside pgbench's -T window.
sample() {
  local label="$1" secs="$2" wal="${3:-}"
  LC_ALL=C mpstat -P ALL 1 "$secs" >"$OUT/$label.db.mpstat" &
  local mp=$!
  local tx0 al0 wal0="" t0
  tx0=$(tx_bytes) al0=$(allowance) t0=$(date +%s.%N)
  ethtool -S "$IFACE" | grep allowance_exceeded >"$OUT/$label.allowance.before"
  [[ -n "$wal" ]] && wal0=$(wal_io)
  : >"$OUT/$label.locks"
  local i
  for i in $(seq 1 "$secs"); do
    psql -Atd bench -c "SELECT count(*) FILTER (WHERE wait_event_type = 'Lock') || ' ' || count(*)
      FROM pg_stat_activity WHERE backend_type = 'client backend' AND datname = 'bench' AND pid <> pg_backend_pid()" \
      >>"$OUT/$label.locks" || true
    sleep 0.9
  done
  wait "$mp"
  local tx1 al1 wal1="" t1
  tx1=$(tx_bytes) al1=$(allowance) t1=$(date +%s.%N)
  ethtool -S "$IFACE" | grep allowance_exceeded >"$OUT/$label.allowance.after"
  [[ -n "$wal" ]] && wal1=$(wal_io)
  printf '{"label":"%s","window_s":%s,"tx_bytes":%s,"allowance_exceeded_delta":%s,"wal_before":"%s","wal_after":"%s"}\n' \
    "$label" "$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b - a}')" "$((tx1 - tx0))" "$((al1 - al0))" "$wal0" "$wal1" \
    >"$OUT/$label.db.json"
}

# track_io_timing is off in postgresql.conf, so pg_stat_io WAL fsync_time would read 0. Turn WAL
# timing on for new sessions of the bench database only (run 10); postgresql.conf stays untouched.
wal-timing-on() { psql -qd postgres -c "ALTER DATABASE bench SET track_wal_io_timing = on"; }

"$@"

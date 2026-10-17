#!/usr/bin/env bash
# Starts Postgres (db/compose.yml) on the db host with the AWS memory settings, waits for the
# first-start seed (bench_template -> bench) to finish, and prints row counts.
# Idempotent: with an existing bench-pgdata volume it only (re)starts the container.
#
#   ./infra/host-setup/seed-db.sh
#   PG_SHARED_BUFFERS=2GB PG_EFFECTIVE_CACHE_SIZE=6GB ./infra/host-setup/seed-db.sh   # defaults shown (8 GB db host, = db/postgresql.conf)
#
# Wipe and reseed: ssh ubuntu@<db> 'cd ~/bench && docker compose -f db/compose.yml down -v', then re-run.
set -euo pipefail
# shellcheck source=infra/host-setup/lib.sh
source "$(dirname "$0")/lib.sh"

load_outputs
SHARED_BUFFERS="${PG_SHARED_BUFFERS:-2GB}"
CACHE_SIZE="${PG_EFFECTIVE_CACHE_SIZE:-6GB}"

log "db: starting Postgres (shared_buffers=$SHARED_BUFFERS, effective_cache_size=$CACHE_SIZE) and waiting for the seed"
rbash db "$REMOTE_REPO" "$SHARED_BUFFERS" "$CACHE_SIZE" <<'EOF'
set -euo pipefail
cd "$1"
export PG_SHARED_BUFFERS=$2 PG_EFFECTIVE_CACHE_SIZE=$3
docker compose -f db/compose.yml up -d

psql_db() { docker exec -e PGPASSWORD=bench bench-db psql -X -h 127.0.0.1 -U bench "$@"; }

# Over TCP on purpose: during the init scripts the entrypoint's temporary server listens on the
# Unix socket only, so "bench answers over TCP" means seeding finished and the real server is up.
start=$SECONDS deadline=$((SECONDS + 2700)) next_note=0
until psql_db -d bench -tAc 'select 1' >/dev/null 2>&1 &&
  [[ "$(docker inspect -f '{{.State.Health.Status}}' bench-db)" == healthy ]]; do
  state="$(docker inspect -f '{{.State.Status}}' bench-db)"
  if [[ "$state" != running ]]; then
    docker logs --tail 50 bench-db >&2
    echo "bench-db is $state" >&2
    exit 1
  fi
  ((SECONDS < deadline)) || { docker logs --tail 50 bench-db >&2; echo "seed timed out" >&2; exit 1; }
  if ((SECONDS >= next_note)); then
    echo "  seeding... $((SECONDS - start)) s"
    next_note=$((SECONDS + 30))
  fi
  sleep 5
done
echo "Postgres ready after $((SECONDS - start)) s"

psql_db -d postgres -tAc "select datname || ' template=' || datistemplate from pg_database where datname like 'bench%' order by 1"
psql_db -d bench -c "
  select 'categories' as table_name, count(*) as rows from categories
  union all select 'products', count(*) from products
  union all select 'customers', count(*) from customers
  union all select 'orders', count(*) from orders
  union all select 'order_items', count(*) from order_items"
psql_db -d bench -tAc "select 'shared_buffers=' || current_setting('shared_buffers') || ' effective_cache_size=' || current_setting('effective_cache_size') || ' size=' || pg_size_pretty(pg_database_size('bench'))"
EOF

log "loadgen -> db over the private network"
rssh loadgen "PGPASSWORD=bench psql -X -h $(priv_ip db) -U bench -d bench -tAc 'select count(*) from products' >/dev/null" ||
  die "loadgen cannot reach Postgres on $(priv_ip db):5432"
log "seed done"

# db

Postgres 18 schema, deterministic seed and server config ([docs/02-test-spec.md](../docs/02-test-spec.md#data-model-postgres-18)).

| File | Purpose |
|---|---|
| `init/01-schema.sql` | Tables, `pg_stat_statements`, `pg_prewarm` |
| `init/02-seed.sql` | Deterministic data from `hashtext()`: 50 categories, 100k products, 100k customers, 1M orders, ~4M items |
| `init/03-constraints.sql` | Foreign keys and secondary indexes, added after the load (faster seeding, same final schema) |
| `init/04-create-bench.sh` | Marks `bench_template` as a template and creates `bench` from it |
| `reset.sql` | Recreates `bench` from `bench_template`, then `VACUUM ANALYZE` and `pg_prewarm` |
| `postgresql.conf` | Server config for the db host (c7a.xlarge, 4 vCPU / 8 GiB) |
| `compose.yml` | `postgres:18.6`, host networking, user/password/db `bench` |

```bash
docker compose -f db/compose.yml up -d        # first start seeds (about 1 min locally), ~0.5 GB
PGPASSWORD=bench psql -h 127.0.0.1 -U bench -d postgres -f db/reset.sql   # fresh bench
docker compose -f db/compose.yml down -v      # wipe; the next start reseeds
```

Locally `shared_buffers` defaults to 1 GB. On the db host set `PG_SHARED_BUFFERS=8GB PG_EFFECTIVE_CACHE_SIZE=24GB` to match `postgresql.conf`.

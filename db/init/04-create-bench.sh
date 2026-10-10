#!/bin/bash
# The seeded database is bench_template; every test runs against a fresh copy named bench.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres <<SQL
UPDATE pg_database SET datistemplate = true WHERE datname = 'bench_template';
CREATE DATABASE bench TEMPLATE bench_template;
SQL

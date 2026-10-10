-- Recreate bench from bench_template (run against the "postgres" database).
-- The runner does this before tests 5 and 9; conformance does it before checking writes.
--   psql -h <db> -U bench -d postgres -f db/reset.sql
\set ON_ERROR_STOP on
DROP DATABASE IF EXISTS bench WITH (FORCE);
CREATE DATABASE bench TEMPLATE bench_template;
\c bench
VACUUM ANALYZE;
SELECT count(pg_prewarm(c.oid))
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'i');

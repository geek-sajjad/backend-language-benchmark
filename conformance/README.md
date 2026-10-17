# conformance

Every stack must pass this suite before it is benchmarked ([docs/02-test-spec.md](../docs/02-test-spec.md)).

| File | Purpose |
|---|---|
| `check.py` | Calls every endpoint (plain and `/secure`) and compares the JSON semantically with the expected values |
| `reference/reference.py` | splitmix64, the test 8 sales dataset and aggregation, the test 7 transform and payload generator, the test 6 mock formula |
| `reference/gen_transform_payloads.py` | Writes the 200 test 7 payloads to `k6/data/transform/` |

Expected values for tests 2–5 come from SQL run through `psql` against the same database, so the checks stay valid after writes. Tests 6–8 come from the reference module.

What is checked besides response bodies: 404s, every test 5 validation rule (422), that invalid orders write nothing, at most 3 queries per test 4 request (`pg_stat_statements`, not counting `BEGIN`/`COMMIT`/`ROLLBACK`), exact stock after 30 parallel orders on one product, and the auth rules (401 for missing, malformed, wrongly signed, wrong `aud`/`iss`, expired, no `exp` and HS256-confusion tokens; 403 for another customer's orders; `/secure/orders` uses `sub`).

Reported at the end, not checked: the database work per request for tests 2–5 (test 5 with 3 items per order). After a warm-up, 20 requests per test give the SQL statements per request (`pg_stat_statements`, this time including `BEGIN`/`COMMIT`/`ROLLBACK`) and the new DB connections per request (`pg_stat_database.sessions`). `pg_stat_statements` doesn't count a utility statement that is re-executed as a server-side prepared statement (pgjdbc prepares `COMMIT` that way), so transaction starts and ends that don't pair up are added back. They explain why stacks reach the db's limit at different rates ([docs/03](../docs/03-tech-stacks.md#database-work-per-request)).

```bash
pip install 'pyjwt[crypto]'          # plus psql on PATH
k6/data/jwt/gen-keys.sh
python3 conformance/check.py http://localhost:8080 [--skip-writes]
```

Database connection: `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` (defaults 127.0.0.1, 5432, bench, bench, bench). Test 5 writes orders, so reset the DB (`db/reset.sql`) before a benchmark run.

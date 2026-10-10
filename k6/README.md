# k6

Load scripts for the nine tests ([docs/02-test-spec.md](../docs/02-test-spec.md)). The runner (`runner/bench.py`) drives them; they also run on their own for development.

| Path | Purpose |
|---|---|
| `tests/01-json.js` … `tests/09-mixed.js` | One script per test |
| `tests/00-echo.js` | Calibration only: k6 and downstream-mock ceilings |
| `lib/options.js` | Shared load shape: scenario, VU sizing, thresholds, tags, phase tagging |
| `lib/env.js` | Every parameter, read from `-e` (the runner is the only reader of `config/bench.toml`) |
| `lib/http.js` | Requests with fixed `name` tags, status checks, body checks in smoke runs |
| `lib/rng.js` | Seeded per-VU PRNG |
| `lib/summary.js` | `handleSummary()`: full summary JSON to `SUMMARY_FILE` plus one line on stdout |
| `lib/payloads.js` | Test 7 bodies via `SharedArray` |
| `data/transform/` | 200 test 7 payloads (`conformance/reference/gen_transform_payloads.py`) |
| `data/jwt/gen-keys.sh` | Test-only RS256 key pair (gitignored) |
| `data/gen-tokens.py` | 10,000 test 9 tokens → `data/jwt/tokens.json` (gitignored; token *i* has `sub` = *i* + 1) |

## Load shape

- **`MODE=step`** (default): one `constant-arrival-rate` scenario for `SETTLE_S + MEASURE_S`. Each iteration tags itself `phase=settle`, `settle_tail` (the last 5 s of settle) or `measure` from the elapsed time. One scenario, not two, so no new VUs or connections open at the start of the measure window. Smoke, probe stages, recovery probes and the common-load run are all steps with different durations.
- **`MODE=warmup`**: `ramping-arrival-rate` from `FROM_RATE` to `TO_RATE` over `DURATION_S`.
- Rates are per second and may be fractional (test 9 sessions/s); internally they are expressed per 10 s.
- VUs: pre-allocated for 100 ms per request, at most 500 ms per request.
- Connections are reused across iterations (09 R10). Bodies are discarded except in smoke runs (`CHECK_BODY=1`) and where test 9 needs data from a response.
- Every request has a fixed `name` tag (`/products/{id}`); `systemTags` excludes `url` (09 R11).
- Pass-through thresholds make the `phase:measure`, `phase:settle_tail` and per-endpoint submetrics appear in the summary (09 R12). The runner judges the numbers; k6's exit code is ignored.
- Expected 422s (test 5's 2% invalid orders) are marked expected per request, so they don't count as failures.

## Run one by hand

```bash
k6/data/jwt/gen-keys.sh && python3 k6/data/gen-tokens.py     # once, for test 9
k6 run -e BASE_URL=http://localhost:8080 -e RATE=200 k6/tests/02-single-read.js
k6 run -e RATE=50 -e SETTLE_S=0 -e MEASURE_S=10 -e CHECK_BODY=1 k6/tests/09-mixed.js   # smoke with body checks
```

| Variable | Default | Meaning |
|---|---|---|
| `BASE_URL` | `http://localhost:8080` | Stack under test |
| `MODE` | `step` | `step` or `warmup` |
| `RATE` | 100 | Iterations/s (test 9: sessions/s) |
| `SETTLE_S` / `SETTLE_TAIL_S` / `MEASURE_S` | 15 / 5 / 30 | Step windows |
| `FROM_RATE` / `TO_RATE` / `DURATION_S` | 50 / 500 / 60 | Warm-up |
| `CHECK_BODY` | 0 | 1 = verify response bodies (smoke) |
| `SUMMARY_FILE` | none | Where `handleSummary` writes the JSON summary |
| `P95_MS` / `P99_MS` / `ERROR_RATE` / `TIMEOUT_S` | 100 / 250 / 0.01 / 5 | SLO, for k6's own pass/fail line only |
| `SEED` | 20261010 | PRNG seed |
| `JOURNEY_WEIGHTS` / `AVG_REQUESTS_PER_SESSION` | 40,25,20,10,5 / 4.65 | Test 9 |

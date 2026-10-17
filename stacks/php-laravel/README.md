# php-laravel

Status: **implemented**, passes `conformance/check.py` locally (118/118, against a freshly reset DB).

Stack choice, reasons and potential optimizations: [docs/03-tech-stacks.md](../../docs/03-tech-stacks.md).

| Item | Value |
|---|---|
| Pinned versions | PHP 8.5.11 (`php:8.5.11-fpm-trixie`, digest pinned in `Dockerfile`), Nginx 1.26.3 and libpq 17.11 from Debian trixie (exact package versions pinned in `Dockerfile`, see [Pinned packages](#pinned-packages)), Composer 2.10.3 (build only, digest pinned); Laravel 13.36.0, firebase/php-jwt 7.2.1, Guzzle 8.2.0 (through the `Http` facade). Full tree in `composer.lock`. |
| Worker / process count | PHP-FPM `pm = static`, `pm.max_children = 64` (fixed, not tuned; docs/03). Nginx `worker_processes auto` (one per core = 4). |
| DB pool size (total) | No pool: Laravel opens one PDO connection per request (the default, docs/03), so at most 64 at a time (one per FPM child). `DB_POOL_SIZE` is not used. |
| Runtime flags / env | `php.ini-production` + OPcache `validate_timestamps=0`, `jit=tracing` (JIT buffer at the 64M default), `memory_consumption` at the 128M default, `opcache.preload` of the test 8 dataset only. `APP_ENV=production`, `APP_DEBUG=false`, `LOG_CHANNEL=stderr`, `LOG_LEVEL=warning`, `CACHE_STORE=array`. |
| Deviations from the spec | Test 6: no shared keep-alive pool; every downstream call opens a new connection. Test 8: dataset pre-generated at build time and kept in OPcache (docs/02 Appendix A). |

## Layout

| Path | Contents |
|---|---|
| `routes/api.php` | The endpoints, registered twice: plain and under `/secure` with `JwtAuth`; `/health` |
| `app/Http/Controllers/` | `Catalog` (2, 3), `Order` (4, 5), `Downstream` (6), `Transform` (7), `Aggregate` (8), `Health` |
| `app/Http/Requests/CreateOrderRequest.php` | Test 5 validation rules |
| `app/Http/Middleware/JwtAuth.php` | Test 9 token check |
| `app/Models/` | Eloquent `Product`, `Order`, `OrderItem` |
| `bootstrap/app.php` | Routing (API group, no `/api` prefix) and the spec's JSON error bodies |
| `config/database.php`, `config/bench.php` | The spec's env variable names, downstream URL, JWT key, dataset path. Every other config comes from the framework's defaults |
| `tools/gen-sales.php` | Writes the test 8 dataset at build time (splitmix64 with GMP) |
| `docker/` | `nginx.conf`, FPM pool, `php.ini` overrides, `preload.php`, `entrypoint.sh` |

## Production config

| Setting | Why |
|---|---|
| Nginx + FPM in one container, started by `entrypoint.sh` (exits if either dies) | The memory metric reads one `app` container (docs/04) |
| Laravel's documented Nginx server block, unix socket to FPM | Laravel deployment docs |
| Nginx `access_log off`, FPM access log removed from the image's `docker.conf` | No per-request logs. The official image sends FPM's access log to stderr by default |
| Nginx `keepalive_requests 1000000000` | The default of 1000 would close k6's keep-alive connections during a run (same idea as Java's `max-keep-alive-requests=-1`) |
| Nginx `worker_connections 65535`, `worker_rlimit_nofile` | Debian's default of 768 connections per worker is far below what k6 opens |
| `config:cache` in the entrypoint; `route:cache` and `event:cache` in the image | `config:cache` freezes `env()`, so it must run where the runner's `DB_HOST` and the mounted key exist. The cached config holds the JWT PEM, so OPcache keeps it in shared memory and the key file is read once per container start |
| Routes in the API group with `apiPrefix: ''` | The web group adds sessions (database-backed by default) and CSRF; the spec's paths have no `/api` |
| `shouldRenderJsonWhen(fn () => true)` + render callbacks | Errors are always JSON with the spec's bodies, never a redirect (a failed FormRequest redirects non-JSON clients by default) |
| No `search_path` in the pgsql config, `sslmode=disable` | With a `search_path` set, Laravel runs `set search_path` on every new connection, which here means every request. `sslmode=disable` is what Go uses too; `prefer` would try a TLS handshake on each connection |

Laravel's default global middleware stays on, including `TrimStrings` and `ConvertEmptyStringsToNull`, which walk every string of the test 5 and 7 request bodies.

## Implementation notes

| Test | How |
|---|---|
| 2, 3 | `Product::findOrFail()` returned as the model's JSON; `where(category)->where(id > after)->orderBy(id)->limit(50)->get()` with full models, mapped to arrays. A non-numeric id or `after` → 400 |
| 4 | 3 queries: orders, then `with(['items' => orderBy id, 'items.product:id,name'])`, i.e. one `IN` query for the items and one for their products' id and name. Verified with `pg_stat_statements`: exactly these 3 statements, nothing else per request |
| 5 | `CreateOrderRequest`: `integer:strict` (rejects `"42"` and `1.5`), `list`, `min`/`max`/`between`, `distinct`; under `/secure` the `customer_id` rule is dropped and `sub` is used. `DB::transaction`: product id, name and price read with `whereIn`, `Order::create`, one multi-row `OrderItem::insert`, then `Product::whereKey()->decrement('stock', q)` per item in ascending `product_id` order. An unknown product, or an FK violation `23503` (unknown customer), → 422 |
| 6 | `Http::get()->throw()->json()`. A new connection per call (see below) |
| 7 | `$request->json()->all()` into arrays; `intdiv`, `usort`, `preg_replace('/\s+/', ' ', trim())` + `strtolower` |
| 8 | `require` of `storage/bench/sales_data.php`: 50,000 positional rows `[id, region, category, customer_tier, amount_cents, quantity, day]`, an immutable array in OPcache shared memory. A fresh grouping array per request |
| 9 | `JwtAuth` middleware: `JWT::decode($token, new Key($pem, 'RS256'))` (signature, matching `alg`, `exp`/`nbf`/`iat` when present), plus checks that `exp` is present, `iss`, `aud`, and a numeric `sub`, which goes into the request attributes. Another customer's orders → 403 |

**Test 8 dataset:** `tools/gen-sales.php` runs in a build stage with the GMP extension, since PHP ints are signed and splitmix64 needs unsigned 64-bit math. Record 1 is `[1,0,30,0,11648,7,341]` as in docs/02. Measured inside the image: `require` of the file costs 392 bytes of request memory with OPcache, against 11.8 MB without it, so the rows are shared and not copied.

**`created_at` from the app clock:** Eloquent fills `created_at` itself on insert (`UPDATED_AT = null`, since the table has no `updated_at`), as Node, Go and .NET do too (docs/02 allows either clock). Laravel's Postgres date format has whole seconds, so the stored value and the 201 response agree.

**Test 6, no keep-alive pool:** PHP-FPM frees the cURL handle when the request ends, so each downstream call does a TCP handshake to the mock. PHP 8.5's persistent cURL share handles could keep connections open, but Guzzle doesn't use them (potential optimization, docs/03). With 64 children and a 50 ms mock, the ceiling is about 1,280 req/s.

**DB connection per request:** measured locally, opening a connection costs about 3.6 ms, against 0.19 ms for a primary-key query on an open connection. During a short test 2 run Postgres used about 10 ms of CPU per request, almost all of it starting backends. The app connects to `127.0.0.1` locally, where `pg_hba.conf` says `trust`. On AWS it connects from another host, which goes through `scram-sha-256` and adds more per-connection work. PHP's DB tests are therefore limited by Postgres connection setup and will be flagged `db_bound`.

**Logging:** warnings and errors only, to stderr (FPM passes worker output to the container log). A 422 for an unknown customer is converted from the `QueryException` before Laravel reports it, so nothing is logged during conformance or a benchmark.

## Build and run

```bash
docker compose -f db/compose.yml up -d                     # seeds once, about a minute
docker compose -f downstream/compose.yml up -d --build
k6/data/jwt/gen-keys.sh                                   # test-only RS256 key pair
docker compose -f stacks/php-laravel/compose.yml up -d --build
python3 conformance/check.py http://localhost:8080
```

The build runs inside Docker (no local PHP or Composer needed). To change dependencies (and regenerate `composer.lock`) without a local PHP:

```bash
cd stacks/php-laravel
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e COMPOSER_HOME=/tmp/c -v "$PWD":/app -w /app composer:2.10.3 composer update --no-scripts --no-autoloader
rm -rf vendor
```

## Pinned packages

The `Dockerfile` installs exact Debian versions of the two runtime packages that come from apt: `nginx=1.26.3-3+deb13u9` and `libpq-dev=17.11-0+deb13u1` (which brings the `libpq5` that `pdo_pgsql` uses), so every rebuild gets the same binaries, as the other stacks do through image digests. Debian's archive keeps only the newest version of a package in trixie and trixie-security, so once Debian ships an update the build fails with `Version '…' for '…' was not found`. Then look up the current versions, update both pins, rebuild and rerun conformance:

```bash
docker run --rm php:8.5.11-fpm-trixie sh -c 'apt-get update -qq && apt-cache policy nginx libpq-dev | grep -E "^[a-z]|Candidate"'
```

Build the images once in the session before the campaign (calibration), so a stale pin shows up there and not mid-campaign.

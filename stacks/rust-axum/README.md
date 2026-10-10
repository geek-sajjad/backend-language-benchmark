# rust-axum

Status: **implemented**, passes `conformance/check.py` locally (120/120).

Stack choice, reasons and potential optimizations: [docs/03-tech-stacks.md](../../docs/03-tech-stacks.md).

| Item | Value |
|---|---|
| Pinned versions | Rust 1.99.0 (`rust:1.99.0-slim-trixie` builder), runtime `debian:trixie-slim` (digest pinned in `Dockerfile`); Axum 0.8.9, Tokio 1.53.2, SQLx 0.9.0, serde_json 1.0.151, validator 0.21.0, reqwest 0.13.5, jsonwebtoken 11.1.0 (`aws_lc_rs`), chrono 0.4.45. Full tree in `Cargo.lock`. |
| Worker / process count | One process; Tokio multi-threaded runtime with the default worker count (one per core = 8). `WORKERS` overrides it (only for the 1-core scaling run). |
| DB pool size (total) | 64 (`DB_POOL_SIZE`), SQLx `PgPool`, to be tuned in calibration |
| Runtime flags / env | None beyond the spec env vars. `TCP_NODELAY` is set on accepted sockets (Axum's documented `tap_io` pattern; Go, .NET and Node set it by default). |
| Deviations from the spec | none |

## Implementation notes

| Test | How |
|---|---|
| 2, 3 | `query_as!` into a full `Product` struct (all columns, including `description`); test 3 maps it to a summary without `description` |
| 4 | 2 queries: the 10 orders, then all their items joined with products via `order_id = ANY($1)`, grouped in Rust |
| 5 | `ValidJson<T>` extractor: serde + `validator` (rejections of either kind → 422). One transaction: price read, order insert, item insert via `UNNEST`, then one `stock = stock - $1` update per item in ascending `product_id` order. FK violation on the customer → 422. |
| 6 | One shared `reqwest::Client` (keep-alive pool, no per-host limit) |
| 7 | Typed serde structs; discounts as an internally tagged enum (`"kind"`) |
| 8 | `Vec<Sale>` built at startup from splitmix64; per request a fresh `HashMap<u8, Group>` |
| 9 | `/secure` router nested with a `route_layer` middleware: `jsonwebtoken::decode` (RS256, `iss`, `aud`, `exp`, `sub` required) → `AuthUser` request extension |

Startup order: JWT key → sales dataset → DB pool (retries up to 60 s) → bind `:8080`. `/health` answers only once all three are ready.

## Build and run

```bash
docker compose -f db/compose.yml up -d                     # seeds once, about a minute
docker compose -f downstream/compose.yml up -d --build
k6/data/jwt/gen-keys.sh                                   # test-only RS256 key pair
docker compose -f stacks/rust-axum/compose.yml up -d --build
python3 conformance/check.py http://localhost:8080
```

### Changing a query

SQLx checks every `query!` against the database at compile time. The Docker build is offline and uses the metadata in `.sqlx/`. After changing SQL, regenerate it against the local DB and commit `.sqlx/`:

```bash
cd stacks/rust-axum
docker run --rm --network host -v cargo-registry:/usr/local/cargo/registry \
  -v "$PWD":/app -w /app -e DATABASE_URL=postgres://bench:bench@127.0.0.1:5432/bench \
  -e SQLX_OFFLINE_DIR=/app/.sqlx rust:1.99.0-slim-trixie cargo build
```

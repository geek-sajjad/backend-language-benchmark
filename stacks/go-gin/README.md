# go-gin

Status: **implemented**, passes `conformance/check.py` locally (118/118, against a freshly reset DB).

Stack choice, reasons and potential optimizations: [docs/03-tech-stacks.md](../../docs/03-tech-stacks.md).

| Item | Value |
|---|---|
| Pinned versions | Go 1.27.2 (`golang:1.27.2-trixie` builder), runtime `debian:trixie-slim` (both digest pinned in `Dockerfile`); Gin 1.12.0, GORM 1.31.2 + `gorm.io/driver/postgres` 1.6.3 (pgx 5.11.0), go-playground/validator 10.30.5 (through Gin binding), golang-jwt 5.3.1. Full tree in `go.sum`. |
| Worker / process count | One process; `GOMAXPROCS` at its default (one per core = 4). |
| DB pool size (total) | 64 (`DB_POOL_SIZE`): `database/sql` `SetMaxOpenConns(64)` and `SetMaxIdleConns(64)`; fixed for every stack (docs/03), not tuned |
| Runtime flags / env | `GIN_MODE=release` (set in the `Dockerfile`). Default `GOGC`, no `GOMEMLIMIT`. `CGO_ENABLED=0`, `-trimpath`. |
| Deviations from the spec | none (test 5's `created_at` comes from the app clock, GORM's `autoCreateTime`, which docs/02 allows; see the note below) |

## Production config

| Setting | Why |
|---|---|
| `SetMaxIdleConns(64)` | `database/sql` keeps only 2 idle connections by default, so under load it would close and reopen Postgres connections constantly. Raised to the pool size (docs/03, decided 2026-10-14) |
| `MaxIdleConnsPerHost = 1024`, `MaxIdleConns = 1024` on the test 6 `Transport` | `net/http` keeps 2 idle connections per host and 100 in total by default; with one downstream host almost every connection would be closed after use (docs/03) |
| `gin.New()` + `gin.Recovery()` | `gin.Default()` also installs the per-request access logger |
| GORM logger: `Warn`, `SlowThreshold: 0`, `IgnoreRecordNotFoundError` | Errors only. The default logger also prints every query slower than 200 ms, which would log every request at overload, and logs a 404 as an error |
| GORM `TranslateError: true` | Driver errors become `gorm.ErrForeignKeyViolated` etc., used for the unknown-customer 422 |
| GORM `NowFunc`: UTC, truncated to microseconds | See `created_at` below |

## Implementation notes

| Test | How |
|---|---|
| 2, 3 | GORM `First(&p, id)` and `Where("category_id = ? AND id > ?").Order("id").Limit(50).Find`. Full `Product` models (including `description`); test 3 maps them to a summary struct |
| 4 | 2 queries: `Find` for the 10 orders with `Preload("Items", …)`, where the preload query uses `Joins("Product", db.Select("id", "name"))` (one `LEFT JOIN` for the belongs-to, only the product's id and name) and `Order("order_items.id")` |
| 5 | Request structs with Gin `binding` tags (go-playground/validator: `required`, `min`, `max`, `unique=ProductID`, `dive`); any bind error → 422. `encoding/json` already rejects `"42"` and `1.5` for integer fields. One `db.Transaction`: price read, `Create(&order)`, one multi-row `Create(&items)`, then one `Update("stock", gorm.Expr("stock - ?", q))` per item in ascending `product_id` order. FK violation on the customer → 422 |
| 6 | One shared `http.Client` with the raised idle limits above. The body is read to EOF so the connection goes back to the pool |
| 7 | Plain structs; a discount is `{kind, value}` switched on `kind` (Go has no tagged unions) |
| 8 | `[]Sale` built at startup from splitmix64; per request a fresh `map[uint8]*salesGroup` |
| 9 | Gin route group `/secure` with a middleware: golang-jwt v5 parser with `WithValidMethods(RS256)`, `WithIssuer`, `WithAudience`, `WithExpirationRequired`; `sub` must be numeric and is stored in the Gin context |

Startup order: JWT key → sales dataset → DB pool (GORM pings on open; retries up to 60 s) → listen on `:8080`. `/health` answers only once all three are ready.

**`created_at` from the app clock:** GORM fills a `CreatedAt` field itself on `Create` (`autoCreateTime`), which is how GORM models are written. Using the DB's `now()` would need a custom `GormValuer` type plus a forced `RETURNING`, which no GORM codebase does. Go's clock has nanoseconds and Postgres stores microseconds, so `NowFunc` truncates to microseconds and the 201 response equals the stored row. Node, .NET and PHP also set it in the app (docs/02 allows either clock).

**Logging:** GORM logs failed statements as errors. k6's invalid orders are all caught by validation before any SQL runs, so a benchmark never logs. Conformance's unknown-customer check makes GORM log one FK-violation line, which is expected.

## Build and run

```bash
docker compose -f db/compose.yml up -d                     # seeds once, about a minute
docker compose -f downstream/compose.yml up -d --build
k6/data/jwt/gen-keys.sh                                   # test-only RS256 key pair
docker compose -f stacks/go-gin/compose.yml up -d --build
python3 conformance/check.py http://localhost:8080
```

The build runs inside Docker (no local Go needed); modules and the build cache are kept in BuildKit cache mounts. To change dependencies without a local Go:

```bash
cd stacks/go-gin
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/src -w /src golang:1.27.2-trixie go mod tidy
```

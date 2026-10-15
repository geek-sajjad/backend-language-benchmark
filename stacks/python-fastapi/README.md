# python-fastapi

Status: **implemented**. Local conformance run: 117/118; the one failure was the test 4 query count, which counted SQLAlchemy's `BEGIN`/`ROLLBACK` (2 real queries per request). Conformance no longer counts transaction control; the full rerun is pending.

Stack choice, reasons and potential optimizations: [docs/03-tech-stacks.md](../../docs/03-tech-stacks.md).

| Item | Value |
|---|---|
| Pinned versions | CPython 3.15.0 (`python:3.15.0-slim-trixie`, digest pinned in `Dockerfile`); FastAPI 0.143.2 (Starlette 1.8.0), Pydantic 2.14.1, SQLAlchemy 2.1.4 + asyncpg 0.32.0 (greenlet 3.5.6), Uvicorn 0.54.0 + uvloop 0.23.0 + httptools 0.9.0, httpx 0.28.1, PyJWT 2.15.1 + cryptography 50.0.2. Full tree in `uv.lock` (uv 0.13.1, build only). |
| Worker / process count | Uvicorn's process manager with one worker per core: the entrypoint sets `WEB_CONCURRENCY=$(nproc)` (4 on the app host). |
| DB pool size (total) | 64 (`DB_POOL_SIZE`), split evenly across workers: `pool_size=16, max_overflow=0` per worker with 4 workers. Fixed for every stack (docs/03), not tuned. |
| Runtime flags / env | `--loop uvloop --http httptools --no-access-log --log-level warning`. Default GC, no Python flags. Bytecode precompiled at build. |
| Deviations from the spec | none (test 5's `created_at` comes from the DB's `now()`, see below) |

## Production config

| Setting | Why |
|---|---|
| `httpx.Limits(max_connections=1024, max_keepalive_connections=1024)` | httpx allows 100 connections per client and keeps only 20 alive by default; test 6 needs ~1,000 requests in flight across the workers (docs/03) |
| `expire_on_commit=False` on the `async_sessionmaker` | SQLAlchemy's asyncio docs recommend it: otherwise reading the order after commit would issue an implicit query, which asyncio doesn't allow |
| `--no-access-log --log-level warning` | Uvicorn logs every request at `info` by default |
| `openapi_url=None` (no `/docs`, `/redoc`) | A production API doesn't serve interactive docs; it has no effect on request handling |

## Implementation notes

| Test | How |
|---|---|
| 2, 3 | `session.get(Product, id)` and `select(Product).where(…).order_by(Product.id).limit(50)`. Full ORM entities (including `description`), converted to Pydantic models with `from_attributes` |
| 4 | 2 queries: the 10 orders, with `selectinload(Order.items).joinedload(OrderItem.product).load_only(Product.id, Product.name)` (one `IN` query with a `LEFT JOIN`, only the product's id and name). Item order comes from the relationship's `order_by=OrderItem.id` |
| 5 | Pydantic models in **strict mode** (lax mode would accept `"42"` and `1.0` as integers) with `Field(ge=…, le=…)`, `min_length/max_length` and a validator for duplicate products; FastAPI returns 422. One `session.begin()`: price read with `IN`, `add(order)` with its items (one INSERT per table, both with `RETURNING`), then one ORM `update(Product).values(stock=Product.stock - q)` per item in ascending `product_id` order. `IntegrityError` on the customer → 422 |
| 6 | One `httpx.AsyncClient` per worker, created in the lifespan and closed on shutdown |
| 7 | Nested Pydantic models; `discount.kind` is a `Literal["percent", "fixed"]` |
| 8 | `@dataclass(slots=True, frozen=True) Sale` list built at startup from splitmix64; per request a fresh `dict[int, Group]` |
| 9 | The shared routers are included a second time with `prefix="/secure"` and a router-level `Depends(current_customer)`. `HTTPBearer(auto_error=False)` + `jwt.decode(algorithms=["RS256"], audience, issuer, options={"require": ["exp", "iss", "aud", "sub"]})`; `sub` must be numeric. The PEM is parsed once at startup into a key object (every request is still verified) |

All handlers are `async def`, so nothing runs in Starlette's threadpool. Tests 7 and 8 are CPU work, which a thread can't run in parallel under the GIL anyway; the workers provide the parallelism.

Handlers declare their return type, so FastAPI validates the response and serializes it with Pydantic's Rust JSON serializer (FastAPI's default path when a response model exists). FastAPI's built-in OpenTelemetry hooks stay off: no OpenTelemetry SDK is installed, so each request costs only one check.

Startup per worker (FastAPI `lifespan`): JWT key → sales dataset → DB pool (retries for up to 60 s) → shared HTTP client. Uvicorn serves a worker's requests only after its startup has finished, so `/health` answers once all of these are ready.

**`created_at` from the DB:** the shared schema has no `DEFAULT`, so the model sets `insert_default=func.now()`. SQLAlchemy writes `now()` into the INSERT and reads the value back with `RETURNING` (its `eager_defaults="auto"` default on Postgres). The 201 response is therefore the stored value, with no extra query. This is the only stack so far that follows the spec here without a deviation.

**Errors:** custom handlers return the spec's bodies (`{"error": "not_found"}`, `{"error": "validation_failed", "details": …}`, …). A malformed path or query parameter (non-numeric, or outside int32) is a 400; FastAPI would otherwise return 422.

**Transactions on reads:** SQLAlchemy's session starts a transaction on its first query (autobegin) and rolls it back when the session closes. Test 4 therefore sends `BEGIN`, 2 queries and `ROLLBACK`. Conformance doesn't count transaction-control statements (docs/02).

## Build and run

```bash
docker compose -f db/compose.yml up -d                     # seeds once, about a minute
docker compose -f downstream/compose.yml up -d --build
k6/data/jwt/gen-keys.sh                                   # test-only RS256 key pair
docker compose -f stacks/python-fastapi/compose.yml up -d --build
python3 conformance/check.py http://localhost:8080
```

The build runs inside Docker (no local Python packages needed); uv's download cache is kept in a BuildKit cache mount. To change dependencies, edit `pyproject.toml` and relock inside the same Python image:

```bash
cd stacks/python-fastapi
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e UV_PYTHON_DOWNLOADS=never -v "$PWD":/src -w /src \
  --entrypoint sh python:3.15.0-slim-trixie -c 'pip install -q --user uv==0.13.1 && ~/.local/bin/uv lock'
```

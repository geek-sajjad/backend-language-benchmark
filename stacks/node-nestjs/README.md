# node-nestjs

Status: **implemented**, passes `conformance/check.py` locally (118/118, against a freshly reset DB).

Stack choice, reasons and potential optimizations: [docs/03-tech-stacks.md](../../docs/03-tech-stacks.md).

| Item | Value |
|---|---|
| Pinned versions | Node.js 24.21.0 (`node:24.21.0-trixie-slim`, digest pinned in `Dockerfile`); NestJS 12.1.2 (`@nestjs/platform-fastify`, Fastify 5), TypeORM 1.1.2 + pg 8.23.1, class-validator 0.15.1, class-transformer 0.5.1, `@nestjs/passport` 12.0.0 + passport-jwt 4.0.1, TypeScript 7.0.2 (build only). Full tree in `package-lock.json`. |
| Worker / process count | `node:cluster`: one primary + one worker per core (4 on the app host). |
| DB pool size (total) | 64 (`DB_POOL_SIZE`), split evenly across workers: 16 per worker with 4 workers. Fixed for every stack (docs/03), not tuned. |
| Runtime flags / env | `NODE_ENV=production`. No Node flags; default heap limits. |
| Deviations from the spec | Test 5: `created_at` is set from the app clock (`new Date()`), not the DB's `now()`. See the note below. |

## Implementation notes

| Test | How |
|---|---|
| 2, 3 | TypeORM `Repository<Product>`: `findOneBy({ id })` and `find({ where: { category_id, id: MoreThan(after) }, order, take: 50 })`. Full entities (including `description`); test 3 maps them to the summary shape |
| 4 | 2 queries: `find` for the 10 orders, then `find` on `OrderItem` with `relations: { product: true }` and `order_id: In(ids)` (one `LEFT JOIN`, only `products.id, name` selected), grouped in JS |
| 5 | DTO classes with class-validator (`@IsInt`, `@Min`, `@ArrayUnique`, `@ValidateNested`) through the global `ValidationPipe` (errors → 422). One `dataSource.transaction`: price read, `insert` order, one multi-row `insert` of items, then one `decrement(Product, { id }, 'stock', q)` per item in ascending `product_id` order. FK violation on the customer → 422 |
| 6 | Built-in `fetch`. Its default global dispatcher is one shared undici `Agent` per process (keep-alive, no per-origin connection limit) |
| 7 | Nested DTO classes (class-transformer `@Type`, class-validator decorators) through the global `ValidationPipe` |
| 8 | Array of plain `Sale` objects built at startup from splitmix64 (BigInt); per request a fresh `Map<number, Group>` |
| 9 | A second controller under `@Controller('secure')` with `@UseGuards(AuthGuard('jwt'))`. `JwtStrategy` (passport-jwt): RS256 only, `iss`, `aud`, `exp` checked by jsonwebtoken; `validate()` also requires `exp` and a numeric `sub` |

Startup per worker: JWT key → sales dataset → DB pool (TypeORM retries for up to a minute) → listen on `:8080`. `/health` answers only once all three are ready. If a worker dies, the primary exits so the failure is visible instead of silently losing a core.

**Malformed JSON → 422:** Nest turns Fastify's JSON parse error into a generic 400. `main.ts` registers Fastify's own JSON parser (same prototype-poisoning checks) through Nest's `useBodyParser`, raising a 422 instead, like the other validation failures.

**`created_at` precision:** a JS `Date` holds milliseconds, and TypeORM hydrates `timestamptz` into a `Date`. With the DB's `now()` (microseconds), the order in the 201 response would differ from the stored row by up to 1 ms. Setting `created_at` from the app clock stores a millisecond value that round-trips exactly. The same limit means rows written with sub-millisecond timestamps (for example by another stack's conformance run on the same DB) read back truncated to milliseconds, so run conformance against a reset DB (`db/reset.sql`). The seed uses whole seconds.

**Generated ids:** TypeORM merges a generated `bigint` id back into the entity as a string even with `parseInt8`, so `OrdersService` converts it with `Number()`. Reads return numbers (`parseInt8`).

## Build and run

```bash
docker compose -f db/compose.yml up -d                     # seeds once, about a minute
docker compose -f downstream/compose.yml up -d --build
k6/data/jwt/gen-keys.sh                                   # test-only RS256 key pair
docker compose -f stacks/node-nestjs/compose.yml up -d --build
python3 conformance/check.py http://localhost:8080
```

Outside Docker (Node 24): `npm ci && npm run build && JWT_PUBLIC_KEY_FILE=../../k6/data/jwt/public.pem npm start`.

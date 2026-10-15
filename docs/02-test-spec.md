# 02 · Test specification

Every stack implements **exactly** this contract. The conformance suite (`conformance/`) calls each endpoint with fixed inputs and compares the responses to a reference (JSON compared semantically). A stack that fails conformance is not benchmarked.

All apps listen on **port 8080**. They read config from environment variables: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_POOL_SIZE`, `DOWNSTREAM_URL` and `JWT_PUBLIC_KEY_FILE`. Every stack uses all cores of the app host; there is no worker-count variable.

---

## Data model (Postgres 18)

An e-commerce shape that every ORM can map naturally:

```sql
CREATE TABLE categories (
  id          integer PRIMARY KEY,
  name        text NOT NULL
);                                                  --      50 rows

CREATE TABLE products (
  id          integer PRIMARY KEY,
  sku         text NOT NULL UNIQUE,
  name        text NOT NULL,
  description text NOT NULL,
  price_cents integer NOT NULL,
  stock       integer NOT NULL,
  category_id integer NOT NULL REFERENCES categories(id),
  created_at  timestamptz NOT NULL
);                                                  -- 100,000 rows
CREATE INDEX products_category_id ON products (category_id, id);

CREATE TABLE customers (
  id          integer PRIMARY KEY,
  email       text NOT NULL UNIQUE,
  name        text NOT NULL,
  country     char(2) NOT NULL,
  created_at  timestamptz NOT NULL
);                                                  -- 100,000 rows

CREATE TABLE orders (
  id          bigserial PRIMARY KEY,
  customer_id integer NOT NULL REFERENCES customers(id),
  status      text NOT NULL,       -- pending | paid | shipped | cancelled
  total_cents integer NOT NULL,
  created_at  timestamptz NOT NULL
);                                                  -- 1,000,000 rows
CREATE INDEX orders_customer_created ON orders (customer_id, created_at DESC);

CREATE TABLE order_items (
  id               bigserial PRIMARY KEY,
  order_id         bigint  NOT NULL REFERENCES orders(id),
  product_id       integer NOT NULL REFERENCES products(id),
  quantity         integer NOT NULL,
  unit_price_cents integer NOT NULL
);                                                  -- ~4,000,000 rows
CREATE INDEX order_items_order ON order_items (order_id);
```

- **Seed:** `db/init/02-seed.sql` uses `generate_series` with values derived from `hashtext()` of the row id. The data is fully deterministic, so the same seed gives byte-identical tables every time. Stock starts at 1,000,000 per product so test 5 never runs out. `products.category_id = ((id − 1) % 50) + 1`, so each category has exactly 2,000 products. `description` is 60–120 characters. `orders.customer_id = ((id − 1) % 100,000) + 1`, so every customer has exactly 10 orders. Each order has 1–7 items (about 4 on average), `unit_price_cents` equals the product's `price_cents`, and `total_cents = Σ unit_price_cents × quantity`.
- **Reset:** the seeded database is kept as `bench_template`. Before every test, the runner recreates `bench` from it (`DROP DATABASE … ; CREATE DATABASE bench TEMPLATE bench_template`), then runs `VACUUM ANALYZE` and `pg_prewarm` on every table and index. Writes from test 5 or 9 never leak into the next test.
- Total size is about 0.5 GB (measured on Postgres 18.6), so everything is cached in RAM on the db host.

## Shared in-memory dataset (test 8)

Each app builds this **at startup** from a specified PRNG, so every language holds identical data without reading files:

- **PRNG:** `splitmix64`, seed `0x5EED_BE7C_0001`. The reference implementation is in `conformance/reference/`.
- **Sales:** 50,000 records `{id, region (0..7), category (0..49), customer_tier (0..3), amount_cents, quantity, day (0..364)}`.
- **Derivation:** one generator for the whole dataset. For `id` = 1..50,000, draw six values **in this order** and reduce each with plain modulo (`next() % n`, unsigned 64-bit):

  | Field | Value |
  |---|---|
  | `region` | `next() % 8` |
  | `category` | `next() % 50` |
  | `customer_tier` | `next() % 4` |
  | `amount_cents` | `100 + next() % 100000` (100..100,099) |
  | `quantity` | `1 + next() % 10` |
  | `day` | `next() % 365` |

  `conformance/reference/reference.py` is the reference (`build_sales`). Record 1 is `{region 0, category 30, customer_tier 0, amount_cents 11648, quantity 7, day 341}`.

Building it doesn't count toward any measurement. It happens before the readiness probe returns 200.

**PHP exception:** PHP-FPM keeps no state between requests, so "build at startup" doesn't apply. The dataset is shipped as a pre-generated PHP file kept in OPcache shared memory instead. See [Appendix A](#appendix-a-php-in-memory-dataset).

---

## Endpoints

**Error bodies** are JSON. Conformance checks only the status code: 404 `{"error":"not_found"}`, 422 `{"error":"validation_failed","details":…}`, 401 `{"error":"unauthorized"}`, 403 `{"error":"forbidden"}`. A malformed path or query parameter (for example a non-numeric id) is a 400 and is never sent by k6.

**Test numbering:** tests run from 2 to 9. Test 1 (JSON hello, `GET /json`) was **removed** on 2026-10-11: it needed by far the highest request rate (and so the biggest load generator) while saying little about real apps, since framework overhead also shows in test 2. The other tests keep their numbers so scripts, configs and results stay stable.

### 2 · Single read: `GET /products/{id}`

One `SELECT` by primary key through the ORM. Returns the product as JSON (all columns, `created_at` in ISO-8601 UTC). Unknown id → 404.

**k6:** `id` is uniform random in 1..100,000.

### 3 · Large result set: `GET /categories/{id}/products?after={after_id}`

Returns **one page of 50 products** in a category using keyset pagination: `category_id = id AND products.id > after_id ORDER BY id LIMIT 50`. This is **one query** that loads **full ORM entities** (all columns, no column projection or raw SQL). The ORM then builds 50 objects, which are mapped to the response shape and serialized:

```json
{"category_id": 7, "next_after": 23456, "items": [
  {"id": 23, "sku": "…", "name": "…", "price_cents": 1299, "stock": 1000000, "created_at": "…"}
]}
```

`description` is loaded with the entity but **not** returned, which keeps the response at about 6.7 KB. `next_after` is the id of the last item (`null` if the page is empty). A missing `after` means 0.

The test targets **ORM row hydration** (turning rows into objects) and serialization. Postgres's own cost is not small, though: the db-sizing test measured about 620 µs of Postgres CPU per 100-row page (mostly per-row output), which capped a 4-core db at ~7.1k pages/s. The page was therefore cut from 100 to 50 rows (2026-10-11). pgbench on the same SQL ran 50-row pages at 1.93× the rate of 100-row pages (local Postgres 18, 16 clients), so the 4-core db should reach about 13.7k pages/s (calibration confirms it), with half the db → app traffic. 50 full entities per request still make hydration the main app-side cost. ORM read-only hints that the framework docs recommend for read endpoints are allowed and listed in the stack README, for example EF Core `AsNoTracking()` and Hibernate's read-only query hint.

**k6:** category uniform random in 1..50, `after` uniform random in 0..90,000. That always leaves ≥ 50 matching rows, because each category has 2,000 products spread evenly over the id range. Db → app is about 10 KB per page (half of the 20.3 KB measured for 100 rows), so the db's ~13k pages/s ceiling is about 1.1 Gbps, inside the 1.56 Gbps baseline. The `ethtool` check guards it.

### 4 · List with join: `GET /customers/{id}/orders`

Returns the customer's **10 most recent orders**. Each order includes its items, and each item includes `{product_id, name, unit_price_cents, quantity}`.

```json
{"customer_id": 42, "orders": [
  {"id": 123, "status": "paid", "total_cents": 4599, "created_at": "…",
   "items": [{"product_id": 7, "name": "…", "unit_price_cents": 1299, "quantity": 2}]}
]}
```

Orders are sorted by `created_at` desc, then `id` desc. Items within an order are sorted by `order_items.id`. A customer with no orders (or an unknown id) returns `"orders": []`.

Use the ORM's idiomatic eager loading: a join, or a "load orders, then load items+products with one `IN` query" approach. **At most 3 queries per request. N+1 is forbidden** (conformance counts queries with `pg_stat_statements` in a dry run; transaction control such as `BEGIN`/`ROLLBACK` is not counted, since some ORMs wrap every read in a transaction).

**k6:** customer id uniform random in 1..100,000. Each customer has about 10 orders with about 4 items each, so a response is roughly 5–6 KB.

### 5 · Write transaction: `POST /orders`

```json
{"customer_id": 42, "items": [{"product_id": 7, "quantity": 2}, {"product_id": 99, "quantity": 1}]}
```

1. **Validate** with the framework's standard validation: `customer_id` is an integer ≥ 1, `items` has 1..10 entries, `product_id` is an integer ≥ 1, `quantity` is an integer in 1..10, and there are no duplicate product ids. Invalid input → 422 with an error body.
2. In **one transaction:** load the item products (prices), insert the `orders` row, insert the `order_items` rows, decrement `products.stock` for each item, and commit.
   - Prices come from a plain read in the same transaction. An unknown product id or customer id → 422, and nothing is written.
   - Stock is decremented with an **atomic update expression** through the ORM's update API (`UPDATE products SET stock = stock - :q WHERE id = :id`), not load-modify-save. No row locks beyond what that statement takes.
   - Items are processed in **ascending `product_id` order**, so concurrent orders lock rows in the same order and can't deadlock.
   - The new order has `status = "pending"`, `created_at = now()` and `total_cents = Σ unit_price_cents × quantity`.
3. Return **201** with the created order (same shape as an order in test 4), items in ascending `product_id` order. Conformance ignores the `id` and `created_at` values but checks their types, and checks that N parallel orders on one product decrease its stock by exactly the ordered total.

**k6:** 1–5 items with distinct random products (100k products keeps row-lock contention negligible). 2% of requests are deliberately invalid, and k6 expects 422 for those, so they don't count as errors.

### 6 · Slow downstream call: `GET /downstream/{id}`

The app calls `GET {DOWNSTREAM_URL}/inventory/{id}` using the framework's standard HTTP client with a **shared, keep-alive connection pool**. The mock responds after a **fixed 50 ms** with `{"id": id, "warehouse": "wh-<id % 8>", "available": (id × 7919) % 1000}`. The app returns `{"product_id": id, "available": …, "source": "downstream"}`.

The request is almost all waiting. Throughput is therefore set by **how many requests the concurrency model can keep in flight**, not by CPU. Little's law: 20,000 req/s × 50 ms = 1,000 concurrent requests.

**k6:** id uniform random in 1..100,000.

### 7 · Heavy JSON: `POST /transform`

- **Request:** a ~4 KB JSON cart export of 15 line items, each with nested `product {id, name, category, attributes{…}}`, `pricing {unit, currency, discounts[]}` and `quantity`. k6 picks from 200 pre-generated payloads in `k6/data/transform/` (loaded through `SharedArray`), written by `conformance/reference/gen_transform_payloads.py` from splitmix64 seed `0x5EED_BE7C_0007`.
  ```json
  {"cart_id": "cart-0001", "currency": "USD", "items": [
    {"line_id": 1, "quantity": 2,
     "product": {"id": 70693, "name": " mini   Backpack  No.70693  ", "category": "Beauty",
                 "attributes": {"size": "XL", "material": "recycled plastic", "origin": "MX", "warranty": "none"}},
     "pricing": {"unit": 12894, "currency": "USD", "discounts": [{"kind": "fixed", "value": 478}]}}
  ]}
  ```
- **Work:**
  1. Parse into typed structures. Language-idiomatic models are used: DTO classes in Java/.NET/TS, structs in Go/Rust, Pydantic models in Python and arrays in PHP.
  2. Apply discounts to the unit price **in list order**, in integer cents: `percent` → `unit − unit × value / 100` (integer division), `fixed` → `max(0, unit − value)`. Line total = discounted unit × `quantity`.
  3. Group by `product.category`, with count, subtotal and the top 3 items by line total (ties: lower `line_id` first).
  4. Normalize every item name: trim, lowercase, collapse whitespace runs to one space. Names are ASCII.
  5. Serialize the result. Categories are sorted by subtotal desc, then name asc.
- **Response:** about 1.9 KB.
  ```json
  {"cart_id": "cart-0001", "currency": "USD", "item_count": 15, "total_cents": 3052009, "categories": [
    {"category": "Electronics", "count": 3, "subtotal_cents": 1177156, "top_items": [
      {"line_id": 5, "product_id": 8964, "name": "pro blender no.8964", "total_cents": 572376}]}
  ]}
  ```

The payload is sized for the 4-core app host's network baseline (~1.56 Gbps): 4 KB at 40k req/s is about 1.3 Gbps inbound. It was 45 items (~12 KB) when the app host had 8 cores and a 3.1 Gbps baseline; at 12 KB the 4-core host would hit its network limit at ~16k req/s, below what the fast stacks reach. 15 items still exercise nested parsing, discounts, grouping, sorting and name normalization on every request.

### 8 · In-memory aggregation: `GET /aggregate?region={r}&min_amount={m}`

Done in pure language code: no numpy, no SIMD libraries and no parallel iterators.

1. Scan all 50,000 sales records and keep those where `region == r && amount_cents >= m`.
2. Group by `category` and compute `count`, `sum_amount`, `min_amount`, `max_amount`, `avg_amount` (integer division) and `total_quantity`.
3. Sort the groups by `sum_amount` desc, then `category` asc, and return the top 10.

A fresh map or dictionary is built per request, so allocation and GC are exercised.

Response:

```json
{"region": 3, "min_amount": 10000, "groups": [
  {"category": 0, "count": 136, "sum_amount": 7839133, "min_amount": 10888, "max_amount": 99881,
   "avg_amount": 57640, "total_quantity": 700}
]}
```

**k6:** `r` uniform in 0..7, and `m` from the fixed thresholds `0, 1000, 10000, 50000, 90000`.

### 9 · Mixed workload: user sessions (authenticated)
Real traffic arrives as **user sessions**, not as independent random requests. A visitor browses a category, opens a few products, checks stock and maybe checks out. Test 9 replays that.

Each k6 iteration is **one session**. It picks a journey by weight and a token (= one logged-in customer), then sends the journey's requests **in order, over the VU's keep-alive connection, with no think time**. As in tests 1–8, that connection is reused by the VU's later sessions, so per-session connection setup isn't measured ([09 R10](09-review-findings.md#r10-test-9-connection-model-is-contradictory)). Later requests use data from earlier responses, for example product ids taken from the category page.

| Journey | Weight | Requests, in order | Count |
|---|---|---|---|
| **Browser** | 40% | category page (3) → view 3 products from that page (2, 2, 2) → stock check on one of them (6) | 5 |
| **Buyer** | 25% | category page (3) → view 2 products (2, 2) → stock check (6) → **checkout** with 1–3 of those products (5) → my orders (4) | 6 |
| **Returning customer** | 20% | my orders (4) → view 2 products from a past order (2, 2) → category page of one of them (3) | 4 |
| **Cart sync** (mobile app uploads a cart) | 10% | heavy JSON transform (7) ×2 | 2 |
| **Back-office analyst** | 5% | sales report (8) ×3, different regions | 3 |

An average session is **4.65 requests**. The resulting request mix:

| Test | Share of requests |
|---|---|
| 2 Single read | 45% |
| 3 Large result set | 18% |
| 6 Downstream call | 14% |
| 4 List with join | 10% |
| 5 Write transaction | 5% |
| 7 Heavy JSON | 4% |
| 8 Aggregation | 3% |

(Test 1, JSON hello, was removed from the plan; it was never part of this mix because it isn't real traffic.) The SLO applies to all requests together. Per-endpoint and per-journey latency is also recorded for analysis.

**Rate and `max_rps`:** k6 offers a constant rate of **sessions** (`constant-arrival-rate`, one iteration = one session). The runner converts the search rate: `sessions/s = req/s ÷ 4.65`. The recorded `max_rps` is the **measured request rate** (`http_reqs` per second over the measure window) at the highest passing step, so test 9 stays in req/s like every other test. `L_common` is converted the same way.

**Why no think time:** real users pause for seconds between clicks. Here that would only multiply the number of k6 VUs (20k req/s × 10 s sessions ≈ 40k concurrent VUs) without changing the work the app does per request. The app sees the same request pattern either way, with fewer, busier connections.

**Errors inside a session:** the journey continues with the next request. A failed step whose data a later step needs (for example an empty category page) falls back to random ids, so a session always sends its full request count.

**Seed for reproducibility:** journey choice, token choice and ids come from a seeded PRNG per VU (`k6/lib/rng.js`), so every stack receives the same sequence of sessions.

#### Authentication

Real APIs check a token on every request, so test 9 does too. The same endpoints are also exposed under a **`/secure` prefix** (`/secure/products/{id}`, `/secure/orders`, …) through a route group guarded by authentication middleware. Tests 2–8 keep calling the unprotected routes, so their numbers stay comparable without auth.

| Item | Rule |
|---|---|
| Token | JWT, **RS256** (2048-bit RSA): the common setup when tokens come from an identity provider (Auth0, Cognito, Keycloak) |
| Header | `Authorization: Bearer <token>` |
| Checks | Signature, `exp`, `iss = "bench-idp"`, `aud = "bench-api"`; the `sub` claim (the customer id as a string, for example `"42"`) is exposed to the handler as the authenticated user. `exp`, `iss`, `aud` and `sub` are required. |
| Authorization | `GET /secure/customers/{id}/orders` returns **403** unless `id == sub`. `POST /secure/orders` ignores any `customer_id` in the body and uses `sub`. |
| Public key | PEM file read once at startup (`JWT_PUBLIC_KEY_FILE`). No JWKS fetching during the test. |
| Library | The framework's standard JWT or auth middleware (listed per stack in [03-tech-stacks.md](03-tech-stacks.md)) |
| Forbidden | Caching verification results; skipping signature checks; HS256 |
| Failure | Missing, expired or invalid token → **401**; another customer's orders → **403** |

**k6:** 10,000 tokens (`sub` = customer ids 1..10,000) are pre-generated by `k6/data/gen-tokens.py` with the test private key from `k6/data/jwt/gen-keys.sh` (both gitignored), valid for 30 days, and loaded through `SharedArray`. Each **session** picks one token and uses it for all of its requests. Conformance additionally checks that a missing token, a malformed token, a bad signature, a wrong `aud` or `iss`, an expired token, a token without `exp` and an HS256 token signed with the public key each return 401, and that reading another customer's orders returns 403.

---

## Readiness and health

`GET /health` returns 200 once the DB pool is connected, the sales dataset is built and the JWT public key is loaded. The runner waits for it before starting the warm-up.

---

## Appendix A: PHP in-memory dataset

**The problem:** test 8 assumes the app builds 50,000 sales records once at startup and keeps them in memory for every request. PHP-FPM is **shared-nothing**: each request starts with empty memory, and nothing a request creates survives it. Following the spec literally would make every `/aggregate` request rebuild the 50,000 records first. That would measure dataset generation, not aggregation, and would be unfair to PHP.

**The solution (idiomatic PHP):** let OPcache hold the data.

1. **At image build time** (`Dockerfile`), a script writes `storage/bench/sales_data.php`:
   ```php
   <?php
   return [
       [1, 3, 17, 2, 4599, 3, 120],   // id, region, category, customer_tier, amount_cents, quantity, day
       [2, 0, 41, 1, 12900, 1, 7],
       // … 50,000 rows
   ];
   ```
   The values must come from the same splitmix64 sequence as every other stack. The generator script may be the PHP port of splitmix64 or the reference implementation in `conformance/reference/`, since generation is never measured. Conformance verifies the result either way.
2. **OPcache compiles the file once.** An array made only of literal values is an *immutable array*: OPcache stores it in **shared memory** that all FPM workers read. A request runs `$sales = require base_path('storage/bench/sales_data.php');` and gets the array **without copying it**, in about the cost of a pointer lookup.
3. **The file is preloaded** through `opcache.preload` so the first request after a restart doesn't pay the compile cost. This fits the existing production settings (`validate_timestamps=0`).

**What stays the same as other stacks:**
- Each request still filters, groups and sorts in plain PHP and builds **fresh arrays per request**, so allocation and memory work are measured as in every other language.
- Rows are positional arrays (`[id, region, …]`) instead of associative arrays or objects, because that is how a PHP developer would store 50k read-only rows for speed. Other stacks use their natural record type (struct, record class, tuple/dataclass).

**Memory accounting:** the shared OPcache segment counts once in the container's memory, not once per worker. That is real PHP-FPM behaviour, and it shows up correctly in the memory score.

**Config:** `opcache.memory_consumption` must have room for the file (about 15–25 MB compiled). The default 128 MB is enough. The stack README records the value used.

**Why not other options:**

| Option | Why not |
|---|---|
| Rebuild per request | Measures generation, not aggregation; unrealistic |
| APCu cache | `apcu_fetch` **copies** the whole array into each request (~50k rows), so every request pays a large copy |
| Redis or the database | Turns a pure-CPU test into an I/O test |
| Laravel Octane / Swoole | Persistent workers would remove the problem entirely, but that's a different stack (see [D1](08-roadmap.md#open-decisions)) |


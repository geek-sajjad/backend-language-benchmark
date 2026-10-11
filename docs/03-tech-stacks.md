# 03 · Tech stacks

## How a stack was chosen

For every language we asked: **"What would a typical team pick today for a new production JSON API backed by Postgres?"** The rules:

1. **The most-used framework** for building APIs in that language, by surveys, GitHub, and job posts.
2. **That ecosystem's mainstream data layer**, which in practice means the ORM most teams use. The ORM is part of the real cost of the language, so it stays in.
3. **The production deployment the framework's own docs recommend**, including the multi-process model where the runtime needs it (Node cluster, Uvicorn workers, PHP-FPM).
4. **Defaults for everything else**, except the "allowed production config" below.

Optimizations that change libraries or bypass the framework are listed for each stack under **potential optimizations**. They are excluded from the main round and are candidates for a follow-up "tuned" episode.

> Versions are the current LTS or stable releases as of the planning date (Oct 2026). Exact versions are pinned in each stack's Dockerfile and lockfile during milestone M2 and frozen before the first scored run.

## Allowed production config (applies to every stack)

| Area | Rule |
|---|---|
| Build | Release / production build (`--release`, `NODE_ENV=production`, `APP_ENV=production`, Release config, …) |
| Workers | Use all 4 cores: worker processes = 4 for multi-process runtimes; single-process runtimes use their default (all cores) |
| DB pool | **≤ 64 total connections to Postgres per stack** (for example 4 workers × 16). Pooled stacks pick their size during calibration and document it. |
| HTTP keep-alive | On, for both inbound and outbound (test 6) connections |
| Logging | No per-request access logs and no SQL logging; warnings and errors only |
| Validation | The framework's standard validation mechanism (test 5) |
| Memory | Defaults, except setting the runtime's heap ceiling to fit 30 GB where the runtime requires a value |
| Not allowed | Swapping the JSON/HTTP/ORM library, raw SQL in place of the ORM, response caching, precomputed responses, disabling safety (HTML escaping, validation), unusual runtime flags |

---

## Node.js: NestJS + Fastify adapter + TypeORM, cluster mode

| Layer | Choice |
|---|---|
| Runtime | Node.js 24 LTS |
| Framework | NestJS 11 with `@nestjs/platform-fastify` |
| ORM / driver | TypeORM 0.3 + `pg` |
| Validation | `class-validator` + `class-transformer` via the global `ValidationPipe` |
| HTTP client | Built-in `fetch` (undici) with a shared `Agent` (keep-alive) |
| Auth (test 9) | `@nestjs/passport` + `passport-jwt` guard (NestJS's documented JWT setup) |
| Process model | `node:cluster` with 4 workers, TypeORM pool of 16 per worker (64 total) |

**Why:** NestJS is the most adopted structured Node backend framework. Its architecture (DI, modules, decorators) is what enterprises use, which makes it closer to "the Spring of Node" than bare Express. The Fastify adapter is officially supported and is NestJS's documented performance path. TypeORM is Nest's long-standing first-class ORM integration (`@nestjs/typeorm`). Node is single-threaded per process, so cluster mode with one worker per core is the standard production deployment. Without it, Node would be using only 1 of 4 cores.

**Potential optimizations:**
- Fastify response schemas → `fast-json-stringify` (Nest doesn't wire these up by default)
- Prisma 6+ (Rust-free client) or Drizzle instead of TypeORM; raw `pg` with prepared statements
- `undici.Pool` used directly for test 6
- `fast-jwt` instead of `jsonwebtoken` under Passport
- Bun or Deno runtimes; replacing class-validator with a compiled validator (Typia, Zod 4)
- Reducing worker count to leave cores for libuv threads and GC

---

## Rust: Axum + Tokio + SQLx

| Layer | Choice |
|---|---|
| Toolchain | Rust 1.99.0 (pinned), `--release`, `lto = "thin"`, `codegen-units = 1` |
| Framework | Axum 0.8 on Tokio 1.53 (multi-threaded runtime, default worker threads = cores) |
| Data layer | SQLx 0.9 (async Postgres, compile-time checked queries via `query!` / `query_as!`) |
| JSON | `serde` + `serde_json` |
| Validation | `validator` 0.21 crate + serde |
| HTTP client | `reqwest` 0.13 (shared `Client`, connection pooling built in) |
| Auth (test 9) | `jsonwebtoken` 11 with the `aws_lc_rs` backend (the backend its README shows first; since v10 a backend must be chosen) in an Axum middleware |
| Process model | One process, Tokio scheduler on 4 cores, pool of up to 64 |

**Why:** Axum (from the Tokio team) is the most popular Rust web framework today. SQLx is the de facto Postgres layer for async Rust. It isn't a full ORM like the others, but it is what most Rust teams use. The heavier ORMs (Diesel, SeaORM) see much less use in async web services, and the stack docs say so explicitly.

**Potential optimizations:**
- `mimalloc` or `jemalloc` global allocator
- `sonic-rs` / `simd-json`
- `tokio-postgres` directly, skipping SQLx's row decoding layer (test 3)
- Tuning the pool size down (Rust often peaks at 16–32 connections)
- `target-cpu=native`

---

## Go: Gin + GORM (pgx driver)

| Layer | Choice |
|---|---|
| Toolchain | Go 1.27 (pinned), `CGO_ENABLED=0` |
| Framework | Gin |
| ORM / driver | GORM v2 with the `pgx` driver |
| JSON | Gin default (`encoding/json`) |
| Validation | Gin binding (`go-playground/validator`) |
| HTTP client | `net/http` with a shared `Transport`, `MaxIdleConnsPerHost` raised to 1024 (required production config: the default of 2 per host is a well-known production bug) |
| Auth (test 9) | `golang-jwt/jwt/v5` in a Gin middleware |
| Process model | One process, `GOMAXPROCS` = 4 (default), pool of up to 64 |

**Why:** Gin is the most starred and most used Go web framework, and GORM is Go's most used ORM. Many Go teams prefer stdlib `net/http` + `pgx` + `sqlc` instead, which is a real cultural split in the Go community (see [open decisions](08-roadmap.md#open-decisions)). Gin + GORM was picked to keep the "framework + ORM" rule consistent across all seven languages.

**Potential optimizations:**
- stdlib `net/http` (Go 1.22+ routing) or Fiber (fasthttp)
- `pgx` directly or `sqlc` (no reflection)
- `bytedance/sonic` via Gin's build tag, or `encoding/json/v2`
- Tuning `GOGC` / `GOMEMLIMIT`
- PGO (profile-guided optimization) builds

---

## Python: FastAPI + SQLAlchemy 2 (async) + Uvicorn workers

| Layer | Choice |
|---|---|
| Runtime | CPython 3.14 (standard GIL build) |
| Framework | FastAPI with Pydantic v2 |
| ORM / driver | SQLAlchemy 2.0 async ORM + `asyncpg` |
| Validation / JSON | Pydantic v2 models (request and response) |
| HTTP client | `httpx.AsyncClient` (shared, pool limits raised) |
| Auth (test 9) | PyJWT (`pyjwt[crypto]`) in a FastAPI `Depends` on the `/secure` router |
| Server | Uvicorn with `uvloop` + `httptools`, `--workers 4` |
| Process model | 4 worker processes × pool of 16 (64 total) |

**Why:** FastAPI is now the most popular choice for new Python APIs and is async-first, which matters for tests 2–6. SQLAlchemy 2 is Python's dominant ORM, and asyncpg is its fastest async Postgres driver. The GIL limits each process to one core, so one Uvicorn worker per core (4) is the documented production setup. Django was considered; FastAPI won on "new API project in 2026" share and its native async.

**Potential optimizations:**
- `ORJSONResponse` or msgspec
- Granian server or Gunicorn + uvicorn workers
- Raw asyncpg queries
- Free-threaded CPython 3.14t
- numpy or polars for test 8 (breaks the "pure language" rule)
- PyPy

---

## Java: Spring Boot (MVC + virtual threads) + Spring Data JPA

| Layer | Choice |
|---|---|
| Runtime | Java 25 LTS (Eclipse Temurin), default G1 GC |
| Framework | Spring Boot 4 with Spring MVC (embedded Tomcat), `spring.threads.virtual.enabled=true` |
| ORM / driver | Spring Data JPA (Hibernate 7) + PostgreSQL JDBC + HikariCP |
| JSON | Jackson (Spring default) |
| Validation | Jakarta Bean Validation (`@Valid`) |
| HTTP client | Spring `RestClient` over JDK `HttpClient` |
| Auth (test 9) | Spring Security OAuth2 Resource Server (`NimbusJwtDecoder.withPublicKey`) |
| Heap | `-XX:MaxRAMPercentage=75` (explicit, so the JVM uses the container's memory) |

**Why:** Spring Boot dominates Java backends. Since Java 21, virtual threads let blocking JDBC/MVC code handle high concurrency (test 6) without reactive rewrites. That is now the recommended path for new Spring MVC apps, and JDK 24+ removed the `synchronized` pinning problem. JPA/Hibernate is what nearly every Spring team uses.

**Potential optimizations:**
- WebFlux + R2DBC (reactive)
- GraalVM native image (better startup and RSS, often lower peak throughput)
- ZGC or Generational Shenandoah
- AOT cache / Leyden (startup only)
- Trimming unused Spring Security filters from the chain
- Quarkus or Micronaut as alternative frameworks
- Hibernate batch and stateless sessions

---

## .NET: ASP.NET Core Minimal APIs + EF Core

| Layer | Choice |
|---|---|
| Runtime | .NET 10 LTS, Server GC (default for ASP.NET Core), Dynamic PGO (default) |
| Framework | ASP.NET Core Minimal APIs on Kestrel |
| ORM / driver | Entity Framework Core 10 + Npgsql |
| JSON | `System.Text.Json` (default) |
| Validation | Built-in Minimal API validation (`AddValidation()`, .NET 10) with DataAnnotations |
| HTTP client | `IHttpClientFactory` typed client (`SocketsHttpHandler` pooling) |
| Auth (test 9) | `Microsoft.AspNetCore.Authentication.JwtBearer` + `RequireAuthorization()` on the route group |
| Process model | One process, thread pool on 4 cores, pool of up to 64 |

**Why:** ASP.NET Core with Minimal APIs is Microsoft's recommended template for new HTTP APIs, and EF Core is the default .NET ORM. Server GC and PGO are on by default, so no tuning is involved.

**Potential optimizations:**
- `System.Text.Json` source generators
- `AddDbContextPool` and EF compiled queries
- Dapper or raw Npgsql
- Native AOT (limited with EF Core)
- `DOTNET_GCDynamicAdaptationMode` (DATAS) tuning to reduce RSS

---

## PHP: Laravel on Nginx + PHP-FPM (OPcache + JIT)

| Layer | Choice |
|---|---|
| Runtime | PHP 8.5, OPcache on (`validate_timestamps=0`), JIT `tracing` |
| Framework | Laravel 13 |
| ORM | Eloquent (PDO pgsql) |
| Validation | Laravel FormRequest |
| HTTP client | Laravel `Http` facade (Guzzle) |
| Auth (test 9) | `firebase/php-jwt` in a route middleware. Laravel has no built-in JWT; its first-party Sanctum uses database-stored tokens, which would make this a different test. `firebase/php-jwt` is the most downloaded PHP JWT library. |
| Server | Nginx → PHP-FPM, `pm = static`, `pm.max_children` tuned in calibration (starting at 64) |
| Laravel prod steps | `config:cache`, `route:cache`, `event:cache`, `composer install --no-dev -o` |
| Test 8 dataset | Pre-generated `sales_data.php` held in OPcache shared memory and preloaded ([02 Appendix A](02-test-spec.md#appendix-a-php-in-memory-dataset)) |

**Why:** Laravel is by far the most popular PHP framework, and Nginx + PHP-FPM is still the most deployed PHP production setup. It is also the one shown in Laravel's deployment docs. FPM is shared-nothing: every request bootstraps the framework, and each worker handles one request at a time. That is exactly what test 6 reveals, and it is what most PHP apps in production actually run.

**DB connections:** Laravel's default is a new connection per request (no persistent PDO). That stays, because it's the realistic default. The 64-connection cap doesn't apply in the usual way: concurrent connections are bounded by `pm.max_children`, and Postgres `max_connections` is raised to 200 to fit (the db host has 8 GB, so the backend count is kept bounded).

**Potential optimizations:**
- **Laravel Octane on FrankenPHP or Swoole** (persistent workers; likely the single biggest PHP gain)
- Persistent PDO connections or PgBouncer
- Tuning `pm.max_children` / `pm` mode
- Preloading
- The `simdjson` extension

---

## Shared services (not scored)

| Component | Choice | Why |
|---|---|---|
| Database | PostgreSQL 18 (official image) | The most common open-source DB for these stacks; same for all |
| Downstream mock | Go `net/http` service, fixed 50 ms `time.Sleep`, on the db host (never on the app host) | Go handles tens of thousands of concurrent sleeping requests on a few cores; its ceiling is measured in calibration |
| Load generator | Grafana k6 (pinned), `constant-arrival-rate` executor | Open-model load (fixed offered rate), so latency isn't hidden by coordinated omission |

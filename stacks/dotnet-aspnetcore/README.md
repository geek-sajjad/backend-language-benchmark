# dotnet-aspnetcore

Status: **implemented**, passes `conformance/check.py` locally (118/118, against a freshly reset DB).

Stack choice, reasons and potential optimizations: [docs/03-tech-stacks.md](../../docs/03-tech-stacks.md).

| Item | Value |
|---|---|
| Pinned versions | .NET 10 LTS: runtime 10.0.13 (`mcr.microsoft.com/dotnet/aspnet:10.0.13-noble`), SDK 10.0.402 (`sdk:10.0.402-noble` builder), both digest pinned in `Dockerfile`; EF Core 10.0.13 through Npgsql.EntityFrameworkCore.PostgreSQL 10.0.3 (Npgsql 10.0.3), EFCore.NamingConventions 10.0.1, Microsoft.AspNetCore.Authentication.JwtBearer 10.0.13. Full tree in `packages.lock.json` (restored with `--locked-mode`). |
| Worker / process count | One process. Kestrel and the .NET thread pool use every core of the host (4). |
| DB pool size (total) | 64 (`DB_POOL_SIZE`), Npgsql `Maximum Pool Size`; fixed for every stack (docs/03), not tuned |
| Runtime flags / env | None. Release build; defaults for everything else: Server GC (with DATAS, the .NET 9+ default), tiered compilation with Dynamic PGO, GC heap limit at the default 75% of the 7 GB container limit. |
| Deviations from the spec | Test 5: `created_at` is set from the app clock, not the DB's `now()`. See the note below. |

## Layout

| Path | Namespace | Contents |
|---|---|---|
| `Program.cs` | | Composition root: services, JSON/validation/auth setup, route groups, startup |
| `Data/` | `Bench.Data` | EF Core `BenchDb` and entities (`Product`, `Order`, `OrderItem`); the test 8 `Sales` dataset |
| `Endpoints/` | `Bench.Endpoints` | One static class per area with its request/response records: `Catalog` (2, 3), `Orders` (4, 5), `Downstream` (6), `Transform` (7), `Aggregate` (8) |
| `Infrastructure/` | `Bench.Infrastructure` | `Auth` (JwtBearer setup, test 9) and `ApiErrors` (error bodies, 422 mapping) |

## Production config

| Setting | Where | Why |
|---|---|---|
| Logging `Default: Warning` | `appsettings.json` | No request or EF command logging (EF logs every command at Information) |
| `PropertyNamingPolicy = SnakeCaseLower` | `ConfigureHttpJsonOptions` | JSON field names from PascalCase C# records |
| `NumberHandling = Strict` | `ConfigureHttpJsonOptions` | ASP.NET's web JSON defaults read `"42"` as an integer; test 5 requires 422 for it (same idea as Java's strict coercion). `1.5` is rejected either way |
| `RouteHandlerOptions.ThrowOnBadRequest = true` + `IExceptionHandler` (`ApiErrors`) | `Program.cs` | Minimal APIs answer an unreadable JSON body with a bare 400; the spec wants 422. With the option on, the binding failure is thrown and `ApiErrors` answers 422 (JSON errors) or 400 (bad path/query parameter). Costs only on invalid requests |
| `CustomizeProblemDetails` → 422 | `Program.cs`, `ApiErrors.ValidationAs422` | The .NET 10 `AddValidation()` filter answers 400 ValidationProblem; changed to 422 |
| `UseSnakeCaseNamingConvention()` | `Program.cs` | Table and column names (`OrderItems` → `order_items`) without per-property mapping; the usual choice with Npgsql |
| `AddDbContext` (not `AddDbContextPool`) | `Program.cs` | Context pooling is a listed potential optimization |

## Implementation notes

| Test | How |
|---|---|
| 2, 3 | `AsNoTracking()` reads of full `Product` entities (the spec allows the read-only hint by name): `FirstOrDefaultAsync(p => p.Id == id)` and `Where(category, id > after).OrderBy(id).Take(50)`. Test 3 maps them to a summary record |
| 4 | **One query**: an EF projection into DTO records. The 10 newest orders become a `LIMIT` subquery, `LEFT JOIN`ed to `order_items ⋈ products`, ordered by `created_at DESC, id DESC, order_items.id`. Only the product's name is read |
| 5 | Request classes with DataAnnotations (`[Range]`, `[Required]`, `[Length(1, 10)]`) validated by the built-in .NET 10 Minimal API validation (`AddValidation()`), plus `IValidatableObject` for duplicate product ids. One explicit transaction: prices read with a projection, `Add(order)` with its items + `SaveChangesAsync` (the order `INSERT … RETURNING id`, then all item `INSERT`s in one batched round trip), then one `ExecuteUpdateAsync(stock = stock - q)` per item in ascending `product_id` order. A customer FK violation (`23503`) → 422 |
| 6 | Typed client from `IHttpClientFactory` (`AddHttpClient<InventoryClient>`): pooled `SocketsHttpHandler`, keep-alive, no per-host connection cap. Defaults otherwise, including the factory's 2-minute handler rotation |
| 7 | Records. A discount is a plain `Discount(Kind, Value)` record switched on `Kind`. `System.Text.Json` polymorphism would require `kind` to be the first property by default |
| 8 | `Sale[]` of `readonly record struct` built at startup from splitmix64 and registered as a singleton; per request a fresh `Dictionary<int, SalesGroup>` |
| 9 | `MapGroup("/secure").RequireAuthorization()` with JwtBearer: `RsaSecurityKey` from the PEM, `ValidAlgorithms = [RS256]`, issuer, audience, `RequireExpirationTime`, `MapInboundClaims = false` (keeps `sub`). A non-numeric `sub` fails in `OnTokenValidated` (→ 401). `OnChallenge` writes the spec's JSON 401 body. JwtBearer's default 5-minute clock skew is kept |

Startup order: JWT key → sales dataset → DB connection (opened once, retried for up to 60 s), all before `app.Run()` starts Kestrel, so `/health` answers only once all three are ready.

**`created_at` from the app clock:** EF Core can't write a SQL expression such as `now()` into an `INSERT`. The idiomatic EF way is `HasDefaultValueSql("now()")`, which assumes the column has a DB `DEFAULT` (an EF migration would create one). The shared schema has none, so EF would insert NULL. The handler therefore sets `CreatedAt = DateTime.UtcNow`, truncated to microseconds so the 201 response equals the stored row. The Node and Go stacks have the same deviation (decided 2026-10-15).

**Authentication on unprotected routes:** with JwtBearer as the only scheme, ASP.NET Core makes it the default and the authentication middleware runs on every request, including tests 2–8. Without an `Authorization` header the handler returns at once, so this is the normal .NET setup and costs very little. (Java limits Spring Security to `/secure/**` because its filter chain is much heavier.)

**Logging:** EF logs a failed command as an error. k6's invalid orders are all caught by validation before any SQL runs, so a benchmark never logs. Conformance's unknown-customer check makes EF log one FK-violation error, which is expected. At startup the image prints `Cannot load library libgssapi_krb5.so.2` (Npgsql probing for Kerberos, unused) and two DataProtection warnings (pulled in by authentication, unused); both are harmless.

## Build and run

```bash
docker compose -f db/compose.yml up -d                     # seeds once, about a minute
docker compose -f downstream/compose.yml up -d --build
k6/data/jwt/gen-keys.sh                                   # test-only RS256 key pair
docker compose -f stacks/dotnet-aspnetcore/compose.yml up -d --build
python3 conformance/check.py http://localhost:8080
```

The build runs inside Docker (no local .NET SDK needed); NuGet packages are kept in a BuildKit cache mount. To change dependencies (and regenerate `packages.lock.json`) without a local SDK:

```bash
cd stacks/dotnet-aspnetcore
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/src -w /src mcr.microsoft.com/dotnet/sdk:10.0.402-noble dotnet restore --force-evaluate
rm -rf bin obj
```

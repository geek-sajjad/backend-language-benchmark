# java-spring

Status: **implemented**, passes `conformance/check.py` locally (118/118, against a freshly reset DB).

Stack choice, reasons and potential optimizations: [docs/03-tech-stacks.md](../../docs/03-tech-stacks.md).

| Item | Value |
|---|---|
| Pinned versions | Java 25 LTS: Eclipse Temurin 25.0.4.1+1 (`eclipse-temurin:25.0.4.1_1-jre-noble` runtime, `maven:3.10.0-eclipse-temurin-25-noble` builder, both digest pinned in `Dockerfile`); Spring Boot 4.1.1, which pins the rest through its BOM: Spring Framework 7.0.9, Spring Data JPA with Hibernate 7.4.5, HikariCP 7.0.2, PostgreSQL JDBC 42.7.13, Jackson 3.1.5, Spring Security 7.1.1, Tomcat 11.0.24, Hibernate Validator. |
| Worker / process count | One JVM. Spring MVC on Tomcat with virtual threads (`spring.threads.virtual.enabled=true`); the JVM uses every core of the host (4), with one virtual-thread carrier thread per core. |
| DB pool size (total) | 64 (`DB_POOL_SIZE`), HikariCP `maximum-pool-size`; fixed for every stack (docs/03), not tuned |
| Runtime flags / env | `-XX:MaxRAMPercentage=75` (heap from the 7 GB container limit); default G1 GC. No other JVM flags. |
| Deviations from the spec | none |

## Production config (`application.properties`)

| Setting | Why |
|---|---|
| `spring.threads.virtual.enabled=true` | Spring's recommended model for blocking MVC + JDBC code since Java 21 (docs/03) |
| `server.tomcat.max-keep-alive-requests=-1` | Tomcat closes every keep-alive connection after 100 requests by default; the other stacks' servers don't. Keep-alive stays on, as the shared config rules require |
| `spring.jpa.open-in-view=false` | Spring Boot warns at startup that open-session-in-view is on by default and should be turned off; every production guide does |
| `spring.jackson.mapper.allow-coercion-of-scalars=false`, `spring.jackson.deserialization.accept-float-as-int=false` | Jackson otherwise accepts `"42"` and `1.5` as integers; test 5 requires 422 for both |
| `spring.jackson.property-naming-strategy=SNAKE_CASE` | JSON field names from camelCase Java records |
| `spring.datasource.hikari.initialization-fail-timeout=60000` | Retry the first DB connection for up to a minute at startup |
| `logging.level.root=warn` | No request or SQL logging |

## Implementation notes

| Test | How |
|---|---|
| 2, 3 | Spring Data `ProductRepository`: `findById` and the derived query `findByCategoryIdAndIdGreaterThanOrderById(…, Limit.of(50))`. Full entities (including `description`); test 3 maps them to a summary record. Both reads run in a read-only transaction (`BEGIN READ ONLY` … `COMMIT`), which also makes the Hibernate session read-only: Spring Data's default for `findById`, and `@Transactional(readOnly = true)` on `ProductRepository` for the derived query, because Spring Data gives declared query methods no transaction (the interface-level annotation is the pattern its reference recommends) |
| 4 | 2 queries in one `@Transactional(readOnly = true)` service method: the derived query `findTop10ByCustomerIdOrderByCreatedAtDescIdDesc`, then one JPQL `join` from `OrderItem` to `Product` with `in :orderIds`, returning a constructor projection (`ItemRow`: only the product's id and name); grouped in Java |
| 5 | Request records with Bean Validation (`@Valid`, `@Min`, `@Max`, `@Size`, plus a custom `@UniqueProductIds` constraint). One `@Transactional` method: products read (`findByIdInOrderById`), `save` the order, `saveAll` the items, then a `@Modifying` JPQL `update Product p set p.stock = p.stock - :quantity` per item in ascending `product_id` order. `created_at` is `@CurrentTimestamp(source = DB)`: Hibernate writes the DB clock in the `INSERT` and reads it back with `RETURNING id, created_at` (no extra query). An FK violation on the customer → 422 |
| 6 | Spring `RestClient` over the JDK `HttpClient` (one shared client, keep-alive pool, no per-host limit). HTTP/1.1 is set because the JDK client otherwise sends an `h2c` upgrade on plain-HTTP connections. Virtual threads keep the blocking call cheap |
| 7 | Records bound by Jackson; the discounts are a sealed `Discount` interface with Jackson's `@JsonTypeInfo(property = "kind")`. No Bean Validation (`@Valid`): test 7 validates nothing beyond parsing (docs/02) |
| 8 | `Sale[]` of records built at startup from splitmix64; per request a fresh `HashMap<Integer, Group>` |
| 9 | Spring Security OAuth2 resource server, with its `SecurityFilterChain` limited to `/secure/**` (`securityMatcher`), so tests 2–8 never pass through Spring Security. `NimbusJwtDecoder.withPublicKey(…)`: RS256 only, validators for `exp` (present and not expired), `iss`, `aud` and a numeric `sub`. The `/secure` routes are the same handler methods mapped on both paths; the customer routes read the `Jwt` principal |

Startup: the JWT key, the sales dataset and the Hikari pool (opened while Hibernate starts) are all singletons created before Tomcat opens `:8080`, so `/health` answers only once all three are ready.

**Hibernate and IDENTITY ids:** the `orders` and `order_items` ids are `bigserial`, mapped as `GenerationType.IDENTITY`. Hibernate can't batch IDENTITY inserts, so test 5 sends one `INSERT` per item (1–5) instead of the single multi-row insert of the Node and Rust stacks. That is the cost of Spring Data JPA's default mapping for this schema, so it stays.

**`created_at` and the session time zone:** for an `Instant`, Hibernate writes `localtimestamp`, which Postgres interprets in the session's time zone. pgjdbc sets that from the JVM's zone, which is UTC in the container, so the stored instant is the DB's `now()`.

## Build and run

```bash
docker compose -f db/compose.yml up -d                     # seeds once, about a minute
docker compose -f downstream/compose.yml up -d --build
k6/data/jwt/gen-keys.sh                                   # test-only RS256 key pair
docker compose -f stacks/java-spring/compose.yml up -d --build
python3 conformance/check.py http://localhost:8080
```

The build runs Maven inside Docker (no local JDK needed); dependencies are cached in a BuildKit cache mount.

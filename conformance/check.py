#!/usr/bin/env python3
"""Conformance suite: checks one running stack against docs/02-test-spec.md.

    python3 conformance/check.py http://localhost:8080

Expected values for the DB endpoints come from SQL run through `psql` (PG* env vars,
defaults: 127.0.0.1:5432, user/password/db bench). Tests 6-8 come from reference/reference.py.
Needs: psql, PyJWT with cryptography, and k6/data/jwt/private.pem (k6/data/jwt/gen-keys.sh).

Test 5 writes orders (customer 99999 and products 12345, 20001..20030 by default; the database
work report also orders products 60000..60179 for customer 77777), so run it against a database
you can reset (db/reset.sql). --skip-writes skips every write check.
"""
import argparse
import base64
import concurrent.futures
import datetime as dt
import glob
import hashlib
import hmac
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "conformance" / "reference"))
import reference  # noqa: E402

try:
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    sys.exit("needs PyJWT and cryptography: pip install 'pyjwt[crypto]'")

BASE = "http://localhost:8080"
FAILURES = []
PASSES = 0


# ---------------------------------------------------------------- helpers

def http(method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, payload, ctype = r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        status, payload, ctype = e.code, e.read(), e.headers.get("Content-Type", "")
    try:
        parsed = json.loads(payload) if payload else None
    except ValueError:
        parsed = payload.decode(errors="replace")
    return status, parsed, ctype


def sql(query):
    env = {**os.environ, "PGTZ": "UTC"}
    env.setdefault("PGHOST", "127.0.0.1")
    env.setdefault("PGPORT", "5432")
    env.setdefault("PGUSER", "bench")
    env.setdefault("PGPASSWORD", "bench")
    env.setdefault("PGDATABASE", "bench")
    out = subprocess.run(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-c", query],
                         env=env, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"psql failed: {out.stderr.strip()}\nquery: {query}")
    return out.stdout.strip()


def sql_json(query):
    return json.loads(sql(query))


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def diff(actual, expected, path="$"):
    """First semantic difference, or None. `created_at` values compare as instants."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path}: expected object, got {type(actual).__name__}"
        if set(actual) != set(expected):
            return f"{path}: keys {sorted(actual)} != {sorted(expected)}"
        for k in expected:
            if k == "created_at" and isinstance(expected[k], str):
                if not isinstance(actual[k], str):
                    return f"{path}.{k}: expected a timestamp string"
                try:
                    if parse_time(actual[k]) != parse_time(expected[k]):
                        return f"{path}.{k}: {actual[k]} != {expected[k]}"
                except ValueError:
                    return f"{path}.{k}: not ISO-8601: {actual[k]!r}"
                continue
            d = diff(actual[k], expected[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return f"{path}: expected array, got {type(actual).__name__}"
        if len(actual) != len(expected):
            return f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            d = diff(a, e, f"{path}[{i}]")
            if d:
                return d
        return None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return None if actual is expected else f"{path}: {actual!r} != {expected!r}"
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return None if actual == expected else f"{path}: {actual!r} != {expected!r}"
    return None if actual == expected else f"{path}: {actual!r} != {expected!r}"


def check(name, ok, detail=""):
    global PASSES
    if ok:
        PASSES += 1
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL {name}: {detail}")


def expect(name, method, path, status, expected=None, **kw):
    got_status, body, _ = http(method, path, **kw)
    if got_status != status:
        check(name, False, f"{method} {path} → {got_status} (want {status}) {str(body)[:200]}")
        return body
    if expected is not None:
        d = diff(body, expected)
        check(name, d is None, f"{method} {path}: {d}")
    else:
        check(name, True)
    return body


# ---------------------------------------------------------------- expected values from SQL

PRODUCT_SQL = """json_build_object('id', id, 'sku', sku, 'name', name, 'description', description,
  'price_cents', price_cents, 'stock', stock, 'category_id', category_id, 'created_at', created_at)"""


def expected_product(pid):
    return sql_json(f"SELECT {PRODUCT_SQL} FROM products WHERE id = {int(pid)}")


def expected_category_page(cid, after):
    return sql_json(f"""
      SELECT json_build_object('category_id', {int(cid)}, 'next_after', max(id),
        'items', coalesce(json_agg(json_build_object('id', id, 'sku', sku, 'name', name,
          'price_cents', price_cents, 'stock', stock, 'created_at', created_at) ORDER BY id), '[]'))
      FROM (SELECT * FROM products WHERE category_id = {int(cid)} AND id > {int(after)}
            ORDER BY id LIMIT 50) p""")


def order_json_sql(alias="o"):
    return f"""json_build_object('id', {alias}.id, 'status', {alias}.status, 'total_cents', {alias}.total_cents,
      'created_at', {alias}.created_at, 'items', (
        SELECT coalesce(json_agg(json_build_object('product_id', oi.product_id, 'name', p.name,
          'unit_price_cents', oi.unit_price_cents, 'quantity', oi.quantity) ORDER BY oi.id), '[]')
        FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE oi.order_id = {alias}.id))"""


def expected_customer_orders(cid):
    return sql_json(f"""
      SELECT json_build_object('customer_id', {int(cid)}, 'orders', coalesce(json_agg({order_json_sql()}
        ORDER BY o.created_at DESC, o.id DESC), '[]'))
      FROM (SELECT * FROM orders WHERE customer_id = {int(cid)}
            ORDER BY created_at DESC, id DESC LIMIT 10) o""")


def stock(pid):
    return int(sql(f"SELECT stock FROM products WHERE id = {int(pid)}"))


# ---------------------------------------------------------------- tokens

PRIVATE_KEY_FILE = ROOT / "k6" / "data" / "jwt" / "private.pem"


def load_private_key():
    if not PRIVATE_KEY_FILE.exists():
        sys.exit(f"missing {PRIVATE_KEY_FILE}: run k6/data/jwt/gen-keys.sh")
    return PRIVATE_KEY_FILE.read_bytes()


def make_token(key, sub, **overrides):
    now = int(time.time())
    claims = {"sub": str(sub), "iss": "bench-idp", "aud": "bench-api", "iat": now, "exp": now + 3600}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- checks

def check_basics():
    print("health")
    expect("health", "GET", "/health", 200)


def check_reads():
    print("test 2: single read")
    for pid in [1, 2, 777, 50_000, 99_999, 100_000]:
        expect(f"product {pid}", "GET", f"/products/{pid}", 200, expected_product(pid))
    for pid in [0, 100_001]:
        expect(f"product {pid} missing", "GET", f"/products/{pid}", 404)

    print("test 3: large result set")
    for cid, after in [(1, 0), (7, 23_456), (50, 90_000), (25, 89_999)]:
        exp = expected_category_page(cid, after)
        check(f"category {cid} page size", len(exp["items"]) == 50, "seed must leave >= 50 rows")
        expect(f"category {cid} after {after}", "GET", f"/categories/{cid}/products?after={after}", 200, exp)
    expect("category page, no after", "GET", "/categories/3/products", 200, expected_category_page(3, 0))

    print("test 4: list with join")
    for cid in [1, 42, 4_242, 100_000]:
        expect(f"customer {cid} orders", "GET", f"/customers/{cid}/orders", 200, expected_customer_orders(cid))
    check_query_count()


def check_query_count():
    """At most 3 queries per test 4 request (N+1 is forbidden).

    Transaction control (BEGIN/COMMIT/ROLLBACK) isn't counted: some ORMs wrap every read in a
    transaction (SQLAlchemy's autobegin), which says nothing about N+1."""
    n = 20
    sql("SELECT pg_stat_statements_reset()")
    for cid in range(500, 500 + n):
        http("GET", f"/customers/{cid}/orders")
    calls = int(sql("""SELECT coalesce(sum(calls), 0) FROM pg_stat_statements
                       WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                         AND query NOT ILIKE '%pg_stat_statements%'
                         AND query !~* '^\\s*(begin|start transaction|commit|end|rollback)\\M'"""))
    check("test 4 queries per request <= 3", 0 < calls <= 3 * n, f"{calls} statements for {n} requests")


def check_writes(key):
    print("test 5: write transaction")
    customer, a, b = 99_999, 7, 99
    before = {a: stock(a), b: stock(b)}
    prices = {pid: int(sql(f"SELECT price_cents FROM products WHERE id = {pid}")) for pid in (a, b)}
    names = {pid: sql(f"SELECT name FROM products WHERE id = {pid}") for pid in (a, b)}
    # Items deliberately out of order: the response lists them by ascending product_id.
    status, body, _ = http("POST", "/orders", {"customer_id": customer,
                                                "items": [{"product_id": b, "quantity": 1},
                                                          {"product_id": a, "quantity": 2}]})
    if status != 201:
        check("create order", False, f"→ {status} {body}")
    else:
        expected = {"id": body.get("id"), "status": "pending", "total_cents": prices[a] * 2 + prices[b],
                    "created_at": body.get("created_at"),
                    "items": [{"product_id": a, "name": names[a], "unit_price_cents": prices[a], "quantity": 2},
                              {"product_id": b, "name": names[b], "unit_price_cents": prices[b], "quantity": 1}]}
        d = diff(body, expected)
        check("create order body", d is None, d)
        check("create order id is an integer", isinstance(body.get("id"), int), repr(body.get("id")))
        try:
            age = abs((dt.datetime.now(dt.timezone.utc) - parse_time(body["created_at"])).total_seconds())
            check("create order created_at is now", age < 120, f"{body['created_at']}")
        except (KeyError, ValueError, TypeError) as e:
            check("create order created_at is ISO-8601", False, str(e))
        if isinstance(body.get("id"), int):
            stored = sql_json(f"SELECT {order_json_sql()} FROM orders o WHERE o.id = {body['id']}")
            d = diff(body, stored)
            check("create order stored", d is None, d)
            owner = int(sql(f"SELECT customer_id FROM orders WHERE id = {body['id']}"))
            check("create order customer", owner == customer, f"customer_id {owner}")
        check("stock decremented", stock(a) == before[a] - 2 and stock(b) == before[b] - 1,
              f"{a}: {before[a]}→{stock(a)}, {b}: {before[b]}→{stock(b)}")

    invalid = {
        "customer_id 0": {"customer_id": 0, "items": [{"product_id": 1, "quantity": 1}]},
        "customer_id missing": {"items": [{"product_id": 1, "quantity": 1}]},
        "customer_id string": {"customer_id": "42", "items": [{"product_id": 1, "quantity": 1}]},
        "no items": {"customer_id": 1, "items": []},
        "11 items": {"customer_id": 1, "items": [{"product_id": i, "quantity": 1} for i in range(1, 12)]},
        "product_id 0": {"customer_id": 1, "items": [{"product_id": 0, "quantity": 1}]},
        "quantity 0": {"customer_id": 1, "items": [{"product_id": 1, "quantity": 0}]},
        "quantity 11": {"customer_id": 1, "items": [{"product_id": 1, "quantity": 11}]},
        "quantity float": {"customer_id": 1, "items": [{"product_id": 1, "quantity": 1.5}]},
        "duplicate product": {"customer_id": 1, "items": [{"product_id": 5, "quantity": 1},
                                                          {"product_id": 5, "quantity": 2}]},
        "unknown product": {"customer_id": 1, "items": [{"product_id": 100_001, "quantity": 1}]},
        "unknown customer": {"customer_id": 100_001, "items": [{"product_id": 1, "quantity": 1}]},
    }
    orders_before = int(sql("SELECT count(*) FROM orders"))
    for name, body in invalid.items():
        expect(f"422 {name}", "POST", "/orders", 422, body=body)
    expect("422 malformed json", "POST", "/orders", 422, raw=b'{"customer_id": 1, "items": [')
    check("invalid orders write nothing", int(sql("SELECT count(*) FROM orders")) == orders_before,
          "an invalid request inserted an order")

    # Concurrency: parallel orders on one product must lose no stock updates and must not deadlock.
    hot, n = 12_345, 30
    hot_before = stock(hot)

    def place(i):
        other = 20_000 + i
        items = [{"product_id": hot, "quantity": 1}, {"product_id": other, "quantity": 1}]
        if i % 2:
            items.reverse()
        return http("POST", "/orders", {"customer_id": customer, "items": items})[0]

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        statuses = list(pool.map(place, range(1, n + 1)))
    check("concurrent orders all 201", statuses.count(201) == n, f"statuses {sorted(set(statuses))}")
    check("concurrent stock exact", stock(hot) == hot_before - statuses.count(201),
          f"{hot_before} → {stock(hot)} after {statuses.count(201)} orders")


def check_downstream():
    print("test 6: downstream")
    for pid in [1, 42, 99_999]:
        expect(f"downstream {pid}", "GET", f"/downstream/{pid}", 200, reference.downstream_expected(pid))


def check_transform():
    print("test 7: transform")
    files = sorted(glob.glob(str(ROOT / "k6" / "data" / "transform" / "*.json")))
    if not files:
        sys.exit("no k6/data/transform/*.json: run conformance/reference/gen_transform_payloads.py")
    for f in files[:: max(1, len(files) // 10)]:
        payload = json.loads(pathlib.Path(f).read_text())
        expect(f"transform {pathlib.Path(f).name}", "POST", "/transform", 200,
               reference.transform(payload), body=payload)


def check_aggregate():
    print("test 8: aggregate")
    sales = reference.build_sales()
    for region in range(8):
        for m in reference.AGGREGATE_MIN_AMOUNTS:
            expect(f"aggregate r={region} m={m}", "GET", f"/aggregate?region={region}&min_amount={m}", 200,
                   reference.aggregate(sales, region, m))


def check_auth(key, writes):
    print("test 9: authentication")
    good = bearer(make_token(key, 42))
    expect("secure product", "GET", "/secure/products/1", 200, expected_product(1), headers=good)
    expect("secure category", "GET", "/secure/categories/7/products?after=0", 200,
           expected_category_page(7, 0), headers=good)
    expect("secure own orders", "GET", "/secure/customers/42/orders", 200, expected_customer_orders(42), headers=good)
    expect("secure other orders → 403", "GET", "/secure/customers/43/orders", 403, headers=good)
    expect("secure downstream", "GET", "/secure/downstream/5", 200, reference.downstream_expected(5), headers=good)
    payload = json.loads(sorted((ROOT / "k6" / "data" / "transform").glob("*.json"))[0].read_text())
    expect("secure transform", "POST", "/secure/transform", 200, reference.transform(payload),
           body=payload, headers=good)
    expect("secure aggregate", "GET", "/secure/aggregate?region=2&min_amount=1000", 200,
           reference.aggregate(reference.build_sales(), 2, 1000), headers=good)

    if writes:
        status, body, _ = http("POST", "/secure/orders",
                               {"customer_id": 7, "items": [{"product_id": 11, "quantity": 1}]}, headers=good)
        if status != 201 or not isinstance(body, dict) or not isinstance(body.get("id"), int):
            check("secure create order", False, f"→ {status} {body}")
        else:
            owner = int(sql(f"SELECT customer_id FROM orders WHERE id = {body['id']}"))
            check("secure order uses sub, not body customer_id", owner == 42, f"customer_id {owner}")
        expect("secure order without customer_id", "POST", "/secure/orders", 201,
               body={"items": [{"product_id": 12, "quantity": 1}]}, headers=good)
        expect("secure order validation → 422", "POST", "/secure/orders", 422,
               body={"items": [{"product_id": 12, "quantity": 99}]}, headers=good)

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public_pem = (ROOT / "k6" / "data" / "jwt" / "public.pem").read_bytes()
    now = int(time.time())
    bad = {
        "missing token": {},
        "not bearer": {"Authorization": "Basic Zm9vOmJhcg=="},
        "garbage token": bearer("not.a.jwt"),
        "bad signature": bearer(make_token(other_key, 42)),
        "wrong aud": bearer(make_token(key, 42, aud="other-api")),
        "wrong iss": bearer(make_token(key, 42, iss="other-idp")),
        "expired": bearer(make_token(key, 42, iat=now - 7200, exp=now - 3600)),
        "missing exp": bearer(make_token(key, 42, exp=None)),
        "HS256 with public key": bearer(hs256_token(public_pem, {"sub": "42", "iss": "bench-idp",
                                                                 "aud": "bench-api", "exp": now + 3600})),
    }
    for name, headers in bad.items():
        expect(f"401 {name}", "GET", "/secure/products/1", 401, headers=headers)
    expect("401 on secure orders", "GET", "/secure/customers/42/orders", 401)


def hs256_token(secret, claims):
    """Algorithm-confusion attack: HMAC-signed with the RSA public key as the secret.
    Built by hand because PyJWT refuses to use a PEM key as an HMAC secret."""
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
    signing_input = b64(b'{"alg":"HS256","typ":"JWT"}') + b"." + b64(json.dumps(claims).encode())
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + b64(signature)).decode()


# ---------------------------------------------------------------- database work per request (reported only)

# All statements, the transaction starts and the transaction ends. pg_stat_statements doesn't see a
# utility statement that is re-executed as a server-side prepared statement (pgjdbc's COMMIT), so
# the starts and ends that don't pair up are added back.
DB_STATEMENTS = """SELECT coalesce(sum(calls), 0),
                          coalesce(sum(calls) FILTER (WHERE query ~* '^\\s*(begin|start transaction)\\M'), 0),
                          coalesce(sum(calls) FILTER (WHERE query ~* '^\\s*(commit|end|rollback)\\M'
                                                        AND query !~* '^\\s*rollback\\s+to\\M'), 0)
                   FROM pg_stat_statements
                   WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                     AND query NOT ILIKE '%pg_stat_statements%'"""
DB_SESSIONS = "(SELECT sessions FROM pg_stat_database WHERE datname = current_database())"


def db_work_per_request(method, path_for, body_for=None, n=20):
    """Average SQL statements (BEGIN/COMMIT/ROLLBACK included) and new DB connections per request.

    Not a pass/fail check. An ORM can add statements to the test's own SQL (for example a
    transaction around every read), and PHP opens a connection per request, so stacks reach the
    db's limit at different rates (docs/04, "When Postgres is the bottleneck")."""
    def send(i):
        http(method, path_for(i), body_for(i) if body_for else None)

    for i in range(2 * n):  # warm-up: pools open their connections, drivers cache statements
        send(i)
    # Both reads mention pg_stat_statements, so neither is counted as a statement.
    sessions_before = int(sql(f"SELECT pg_stat_statements_reset(), {DB_SESSIONS}").split("|")[1])
    for i in range(2 * n, 3 * n):
        send(i)
    statements, starts, ends, sessions_after = map(
        int, sql(f"SELECT s.*, {DB_SESSIONS} FROM ({DB_STATEMENTS}) s").split("|"))
    statements += abs(starts - ends)
    # The psql session doing the second read is itself one new session.
    return statements / n, (sessions_after - sessions_before - 1) / n


def report_db_work(writes):
    print("\ndatabase work per request (reported, not checked)")
    probes = [
        ("test 2", "GET", lambda i: f"/products/{1 + i * 997 % 100_000}", None),
        ("test 3", "GET", lambda i: f"/categories/{1 + i % 50}/products?after={i * 1_000}", None),
        ("test 4", "GET", lambda i: f"/customers/{1_000 + i}/orders", None),
    ]
    if writes:
        # Customer 77777 and products 60000..60179 are used by no other check.
        probes.append(("test 5, 3 items", "POST", lambda i: "/orders",
                       lambda i: {"customer_id": 77_777,
                                  "items": [{"product_id": 60_000 + 3 * i + k, "quantity": 1} for k in range(3)]}))
    for name, method, path_for, body_for in probes:
        statements, connections = db_work_per_request(method, path_for, body_for)
        print(f"  {name:<16} {statements:4.1f} SQL statements (incl. BEGIN/COMMIT)  {connections:4.2f} new connections")


def main():
    global BASE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url", nargs="?", default=BASE)
    ap.add_argument("--skip-writes", action="store_true", help="skip test 5 and the secure write checks")
    args = ap.parse_args()
    BASE = args.base_url.rstrip("/")
    key = load_private_key()

    check_basics()
    check_reads()
    check_downstream()
    check_transform()
    check_aggregate()
    if not args.skip_writes:
        check_writes(key)
    check_auth(key, writes=not args.skip_writes)
    report_db_work(writes=not args.skip_writes)

    print(f"\n{PASSES} passed, {len(FAILURES)} failed")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()

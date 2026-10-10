-- Deterministic seed: every value is derived from hashtext() of a tagged row id,
-- so the same Postgres major version produces byte-identical tables every time.
--
-- Shapes (relied on by k6 and conformance, see docs/02-test-spec.md):
--   products.category_id = ((id - 1) % 50) + 1     → exactly 2,000 products per category
--   orders.customer_id   = ((id - 1) % 100000) + 1 → exactly 10 orders per customer
--   items per order      = 1..7 (avg 4), unit_price_cents = the product's price_cents
--   orders.total_cents   = Σ unit_price_cents × quantity of its items
--   stock                = 1,000,000 for every product

\set ON_ERROR_STOP on

CREATE FUNCTION seed_hash(text) RETURNS bigint
  LANGUAGE sql IMMUTABLE PARALLEL SAFE
  AS $$ SELECT abs(hashtext($1)::bigint) $$;

INSERT INTO categories (id, name)
SELECT i,
       (ARRAY['Home','Garden','Kitchen','Office','Outdoor','Sports','Toys','Audio','Video','Travel'])[1 + (i - 1) / 5]
       || ' ' ||
       (ARRAY['Essentials','Accessories','Supplies','Gear','Classics'])[1 + (i - 1) % 5]
FROM generate_series(1, 50) AS i;

INSERT INTO products (id, sku, name, description, price_cents, stock, category_id, created_at)
SELECT i,
       'SKU-' || lpad(i::text, 6, '0'),
       (ARRAY['Classic','Smart','Ultra','Compact','Eco','Pro','Deluxe','Mini','Rugged','Vintage'])[1 + seed_hash('pa' || i) % 10]
       || ' ' ||
       (ARRAY['Widget','Lamp','Chair','Kettle','Speaker','Backpack','Monitor','Jacket','Blender','Router'])[1 + seed_hash('pn' || i) % 10]
       || ' ' || i,
       left(repeat(md5('d' || i) || ' ', 4), 60 + (seed_hash('dl' || i) % 61)::int),  -- 60..120 chars
       99 + (seed_hash('pp' || i) % 99901)::int,                                        -- 0.99 .. 999.99
       1000000,
       ((i - 1) % 50) + 1,
       timestamptz '2024-01-01 00:00:00+00' + make_interval(secs => seed_hash('pc' || i) % (365 * 86400))
FROM generate_series(1, 100000) AS i;

INSERT INTO customers (id, email, name, country, created_at)
SELECT i,
       'customer' || i || '@example.com',
       (ARRAY['Ava','Ben','Chloe','Daniel','Emma','Farid','Grace','Hugo','Iris','Jonas','Kira','Liam','Maya','Noah','Olga','Pablo'])[1 + seed_hash('cf' || i) % 16]
       || ' ' ||
       (ARRAY['Smith','Garcia','Müller','Rossi','Kim','Novak','Silva','Tanaka','Jensen','Dubois','Ahmadi','Cohen'])[1 + seed_hash('cl' || i) % 12],
       (ARRAY['US','GB','DE','FR','CA','NL','SE','JP','AU','BR'])[1 + seed_hash('cc' || i) % 10],
       timestamptz '2022-01-01 00:00:00+00' + make_interval(secs => seed_hash('ct' || i) % (2 * 365 * 86400))
FROM generate_series(1, 100000) AS i;

-- Order lines first, so order totals can be computed before the orders are inserted.
CREATE TEMP TABLE seed_items AS
SELECT row_number() OVER (ORDER BY o, n) AS id,
       o AS order_id,
       (1 + seed_hash('ip' || o || ':' || n) % 100000)::int AS product_id,
       (1 + seed_hash('iq' || o || ':' || n) % 5)::int      AS quantity
FROM generate_series(1, 1000000) AS o,
     LATERAL generate_series(1, (1 + seed_hash('ic' || o) % 7)::int) AS n;

INSERT INTO orders (id, customer_id, status, total_cents, created_at)
SELECT t.order_id,
       ((t.order_id - 1) % 100000) + 1,
       (ARRAY['pending','paid','shipped','cancelled'])[1 + seed_hash('os' || t.order_id) % 4],
       t.total_cents,
       timestamptz '2025-01-01 00:00:00+00' + make_interval(secs => seed_hash('oc' || t.order_id) % (365 * 86400))
FROM (
  SELECT si.order_id, sum(p.price_cents * si.quantity)::int AS total_cents
  FROM seed_items si JOIN products p ON p.id = si.product_id
  GROUP BY si.order_id
) AS t
ORDER BY t.order_id;

INSERT INTO order_items (id, order_id, product_id, quantity, unit_price_cents)
SELECT si.id, si.order_id, si.product_id, si.quantity, p.price_cents
FROM seed_items si JOIN products p ON p.id = si.product_id
ORDER BY si.id;

SELECT setval('orders_id_seq', (SELECT max(id) FROM orders));
SELECT setval('order_items_id_seq', (SELECT max(id) FROM order_items));

DROP TABLE seed_items;
DROP FUNCTION seed_hash(text);

-- Test 3: one keyset page of 100 full rows
\set c random(1, 50)
\set a random(0, 90000)
SELECT id, sku, name, description, price_cents, stock, category_id, created_at
FROM products WHERE category_id = :c AND id > :a ORDER BY id LIMIT 100;

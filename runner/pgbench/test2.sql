-- Test 2: the SQL one GET /products/{id} produces
\set id random(1, 100000)
SELECT id, sku, name, description, price_cents, stock, category_id, created_at FROM products WHERE id = :id;

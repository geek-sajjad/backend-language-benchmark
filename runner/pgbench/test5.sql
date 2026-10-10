-- Test 5: the write transaction with 3 items (k6 sends 1-5, average 3)
\set c random(1, 100000)
\set p1 random(1, 33333)
\set p2 random(33334, 66666)
\set p3 random(66667, 100000)
BEGIN;
SELECT id, name, price_cents FROM products WHERE id IN (:p1, :p2, :p3) ORDER BY id;
INSERT INTO orders (customer_id, status, total_cents, created_at) VALUES (:c, 'pending', 1000, now()) RETURNING id \gset
INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents)
VALUES (:id, :p1, 1, 100), (:id, :p2, 1, 100), (:id, :p3, 1, 100);
UPDATE products SET stock = stock - 1 WHERE id = :p1;
UPDATE products SET stock = stock - 1 WHERE id = :p2;
UPDATE products SET stock = stock - 1 WHERE id = :p3;
COMMIT;

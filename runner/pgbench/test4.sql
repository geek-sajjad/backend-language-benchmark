-- Test 4: orders, then their items with product names (2 queries, like the eager-loading stacks)
\set c random(1, 100000)
SELECT id, status, total_cents, created_at FROM orders WHERE customer_id = :c ORDER BY created_at DESC, id DESC LIMIT 10;
SELECT oi.order_id, oi.product_id, p.name, oi.unit_price_cents, oi.quantity
FROM order_items oi JOIN products p ON p.id = oi.product_id
WHERE oi.order_id IN (SELECT id FROM orders WHERE customer_id = :c ORDER BY created_at DESC, id DESC LIMIT 10)
ORDER BY oi.id;

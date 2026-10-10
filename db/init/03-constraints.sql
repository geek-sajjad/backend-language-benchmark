-- Foreign keys and secondary indexes, added after the bulk load.

\set ON_ERROR_STOP on

ALTER TABLE products    ADD FOREIGN KEY (category_id) REFERENCES categories(id);
ALTER TABLE orders      ADD FOREIGN KEY (customer_id) REFERENCES customers(id);
ALTER TABLE order_items ADD FOREIGN KEY (order_id)    REFERENCES orders(id);
ALTER TABLE order_items ADD FOREIGN KEY (product_id)  REFERENCES products(id);

CREATE INDEX products_category_id      ON products (category_id, id);
CREATE INDEX orders_customer_created   ON orders (customer_id, created_at DESC);
CREATE INDEX order_items_order         ON order_items (order_id);

VACUUM (FREEZE, ANALYZE);

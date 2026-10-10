-- Tables only. Foreign keys and secondary indexes are added by 03-constraints.sql
-- after the bulk load, which is much faster; the final schema matches docs/02-test-spec.md.

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;  -- conformance counts queries per request (test 4)
CREATE EXTENSION IF NOT EXISTS pg_prewarm;          -- the runner prewarms every table after a reset

CREATE TABLE categories (
  id          integer PRIMARY KEY,
  name        text NOT NULL
);

CREATE TABLE products (
  id          integer PRIMARY KEY,
  sku         text NOT NULL UNIQUE,
  name        text NOT NULL,
  description text NOT NULL,
  price_cents integer NOT NULL,
  stock       integer NOT NULL,
  category_id integer NOT NULL,
  created_at  timestamptz NOT NULL
);

CREATE TABLE customers (
  id          integer PRIMARY KEY,
  email       text NOT NULL UNIQUE,
  name        text NOT NULL,
  country     char(2) NOT NULL,
  created_at  timestamptz NOT NULL
);

CREATE TABLE orders (
  id          bigserial PRIMARY KEY,
  customer_id integer NOT NULL,
  status      text NOT NULL,       -- pending | paid | shipped | cancelled
  total_cents integer NOT NULL,
  created_at  timestamptz NOT NULL
);

CREATE TABLE order_items (
  id               bigserial PRIMARY KEY,
  order_id         bigint  NOT NULL,
  product_id       integer NOT NULL,
  quantity         integer NOT NULL,
  unit_price_cents integer NOT NULL
);

-- Sample e-commerce dataset + a read-only role for the app.
-- Runs automatically the first time the Postgres container starts.

CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT NOT NULL,
    province    TEXT NOT NULL,
    signup_date DATE NOT NULL
);
COMMENT ON TABLE customers IS 'Registered customers';
COMMENT ON COLUMN customers.province IS 'Two-letter Canadian province code, e.g. AB';

CREATE TABLE products (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL
);
COMMENT ON TABLE products IS 'Product catalogue with current list price (CAD)';

CREATE TABLE orders (
    id          SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(id),
    order_date  DATE NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('completed', 'refunded', 'cancelled'))
);
COMMENT ON TABLE orders IS 'One row per order. Revenue should normally count only status = completed';
COMMENT ON COLUMN orders.customer_id IS 'Foreign key to customers.id. Join with orders.customer_id = customers.id';

CREATE TABLE order_items (
    id         SERIAL PRIMARY KEY,
    order_id   INT NOT NULL REFERENCES orders(id),
    product_id INT NOT NULL REFERENCES products(id),
    quantity   INT NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL
);
COMMENT ON TABLE order_items IS 'Line items. Line revenue = quantity * unit_price (price at time of sale)';
COMMENT ON COLUMN order_items.order_id IS 'Foreign key to orders.id';
COMMENT ON COLUMN order_items.product_id IS 'Foreign key to products.id';

SELECT setseed(0.42);

INSERT INTO products (name, category, unit_price) VALUES
    ('Merino Wool Toque', 'Accessories', 29.99),
    ('Leather Gloves', 'Accessories', 49.99),
    ('Canvas Tote Bag', 'Accessories', 24.50),
    ('Wool Scarf', 'Accessories', 34.00),
    ('Down Parka', 'Outerwear', 399.00),
    ('Rain Shell Jacket', 'Outerwear', 189.00),
    ('Fleece Pullover', 'Outerwear', 89.00),
    ('Insulated Vest', 'Outerwear', 129.00),
    ('Hiking Boots', 'Footwear', 219.00),
    ('Winter Boots', 'Footwear', 249.00),
    ('Running Shoes', 'Footwear', 159.00),
    ('Slip-on Sneakers', 'Footwear', 79.00),
    ('Flannel Shirt', 'Tops', 59.00),
    ('Organic Cotton Tee', 'Tops', 25.00),
    ('Hoodie', 'Tops', 69.00),
    ('Thermal Base Layer', 'Tops', 55.00),
    ('Slim Jeans', 'Bottoms', 89.00),
    ('Cargo Pants', 'Bottoms', 79.00),
    ('Joggers', 'Bottoms', 59.00),
    ('Hiking Shorts', 'Bottoms', 49.00);

WITH cities(idx, city, province) AS (VALUES
    (0, 'Edmonton', 'AB'), (1, 'Calgary', 'AB'), (2, 'Red Deer', 'AB'), (3, 'Lethbridge', 'AB'),
    (4, 'Vancouver', 'BC'), (5, 'Toronto', 'ON'), (6, 'Winnipeg', 'MB'), (7, 'Saskatoon', 'SK'))
INSERT INTO customers (name, city, province, signup_date)
SELECT 'Customer ' || lpad(g::text, 4, '0'), c.city, c.province,
       DATE '2023-01-01' + floor(random() * 540)::int
FROM generate_series(1, 500) AS g
JOIN cities c ON c.idx = g % 8;

INSERT INTO orders (customer_id, order_date, status)
SELECT c.id,
       c.signup_date + floor(s.d * 365)::int,
       CASE WHEN s.r < 0.90 THEN 'completed' WHEN s.r < 0.96 THEN 'refunded' ELSE 'cancelled' END
FROM (SELECT 1 + floor(random() * 500)::int AS cid, random() AS d, random() AS r
      FROM generate_series(1, 3000)) AS s
JOIN customers c ON c.id = s.cid;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT x.order_id, p.id, x.qty, p.unit_price
FROM (SELECT o.id AS order_id,
             1 + floor(random() * 20)::int AS pid,
             1 + floor(random() * 3)::int AS qty
      FROM orders o, LATERAL generate_series(1, 1 + o.id % 4) AS n) AS x
JOIN products p ON p.id = x.pid;

-- Read-only role used by the API (first line of defence against bad SQL).
CREATE ROLE insight_reader LOGIN PASSWORD 'reader_pw';
GRANT CONNECT ON DATABASE insightbot TO insight_reader;
GRANT USAGE ON SCHEMA public TO insight_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO insight_reader;
ALTER ROLE insight_reader SET default_transaction_read_only = on;
ALTER ROLE insight_reader SET statement_timeout = '5s';

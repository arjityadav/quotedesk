-- 0003: order history ("wie letztes Mal").
-- Re-runnable: every statement uses IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS orders (
    id           text PRIMARY KEY CHECK (id ~ '^OR-[0-9]{6}$'),
    customer_id  text NOT NULL REFERENCES customers (id),
    order_date   date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_lines (
    order_id    text NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    line_no     integer NOT NULL CHECK (line_no >= 1),
    sku         text NOT NULL REFERENCES products (sku),
    quantity    integer NOT NULL CHECK (quantity > 0),
    unit_price  numeric(10, 2) NOT NULL CHECK (unit_price > 0),
    PRIMARY KEY (order_id, line_no),
    CONSTRAINT order_lines_one_row_per_sku UNIQUE (order_id, sku)
);

-- "The customer's last order" and "orders in March".
CREATE INDEX IF NOT EXISTS orders_customer_date_idx ON orders (customer_id, order_date DESC);
CREATE INDEX IF NOT EXISTS order_lines_sku_idx ON order_lines (sku);

-- 0002: customers, discount tiers and contract price lists.
-- Re-runnable: every statement uses IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS price_lists (
    id          text PRIMARY KEY CHECK (id ~ '^PL-[0-9]{4}$'),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customers (
    id             text PRIMARY KEY CHECK (id ~ '^CU-[0-9]{4}$'),
    name           text NOT NULL CHECK (name <> ''),
    kind           text NOT NULL CHECK (kind IN ('electrician', 'hvac_installer', 'facility_manager')),
    city           text NOT NULL CHECK (city <> ''),
    postcode       text NOT NULL CHECK (postcode ~ '^[0-9]{5}$'),
    discount_tier  text NOT NULL CHECK (discount_tier IN ('A', 'B', 'C')),
    delivery       text NOT NULL CHECK (delivery IN ('standard', 'express', 'pickup')),
    price_list_id  text REFERENCES price_lists (id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- One row per (list, SKU, quantity break). A price applies from min_qty upwards.
CREATE TABLE IF NOT EXISTS prices (
    price_list_id  text NOT NULL REFERENCES price_lists (id) ON DELETE CASCADE,
    sku            text NOT NULL REFERENCES products (sku),
    min_qty        integer NOT NULL CHECK (min_qty >= 1),
    unit_price     numeric(10, 2) NOT NULL CHECK (unit_price > 0),
    PRIMARY KEY (price_list_id, sku, min_qty)
);

-- "Which contracts include this SKU?" (the primary key already covers lookups by list).
CREATE INDEX IF NOT EXISTS prices_sku_idx ON prices (sku);
CREATE INDEX IF NOT EXISTS customers_price_list_idx ON customers (price_list_id);

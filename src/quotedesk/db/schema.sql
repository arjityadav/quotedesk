-- QuoteDesk schema: the product catalog.
-- Re-runnable: every statement uses IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS products (
    sku           text PRIMARY KEY CHECK (sku ~ '^MH-[0-9]{6}$'),
    name_de       text NOT NULL CHECK (name_de <> ''),
    name_en       text NOT NULL CHECK (name_en <> ''),
    category      text NOT NULL,
    manufacturer  text NOT NULL,
    mpn           text NOT NULL,
    unit          text NOT NULL CHECK (unit IN ('C62', 'MTR', 'PK')),
    pack_size     integer NOT NULL CHECK (pack_size >= 1),
    -- Money is exact numeric, never float.
    list_price    numeric(10, 2) NOT NULL CHECK (list_price > 0),
    attributes    jsonb NOT NULL DEFAULT '{}',
    discontinued  boolean NOT NULL DEFAULT false,
    -- Deferred so a product and its successor can be loaded in one transaction, in any order.
    successor_sku text REFERENCES products (sku) DEFERRABLE INITIALLY DEFERRED,
    -- German name weighs more than the English one in ranking.
    -- Generated columns need immutable expressions, hence the explicit ::regconfig.
    search_tsv    tsvector GENERATED ALWAYS AS (
                      setweight(to_tsvector('german'::regconfig, name_de), 'A')
                      || setweight(to_tsvector('english'::regconfig, name_en), 'B')
                  ) STORED,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT products_successor_only_when_discontinued
        CHECK (successor_sku IS NULL OR discontinued),
    CONSTRAINT products_successor_not_itself
        CHECK (successor_sku IS DISTINCT FROM sku),
    CONSTRAINT products_manufacturer_mpn_key
        UNIQUE (manufacturer, mpn)
);

-- Full-text search ("Steckdosen Klappdeckel").
CREATE INDEX IF NOT EXISTS products_search_tsv_idx ON products USING gin (search_tsv);

-- Attribute containment filters (attributes @> '{"cross_section_mm2": 2.5}').
CREATE INDEX IF NOT EXISTS products_attributes_idx ON products USING gin (attributes jsonb_path_ops);

CREATE INDEX IF NOT EXISTS products_category_idx ON products (category);

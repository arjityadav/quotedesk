"""Load catalog products, customers, price lists and order history into PostgreSQL."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import psycopg

from quotedesk.catalog.models import AttrValue, Product
from quotedesk.customers.models import Customer, PriceListEntry
from quotedesk.customers.orders import Order
from quotedesk.db.migrate import sql_files


def apply_schema(conn: psycopg.Connection[Any]) -> None:
    """Run every schema file in order (idempotent). For tests and throwaway schemas.

    Real databases use Alembic (`make migrate`), which runs the same files and records
    the version.
    """
    for path in sql_files():
        conn.execute(path.read_text(encoding="utf-8"))


def attributes_json(attributes: dict[str, AttrValue]) -> str:
    """Serialise attributes to JSON with Decimals as exact JSON numbers."""
    # json.dumps can't encode Decimal, and going through float can change the value
    # (float(Decimal("0.1")) * 3 != 0.3). jsonb stores numbers as exact numeric, so
    # write each Decimal's own text as the number.
    parts = []
    for key in sorted(attributes):
        value = attributes[key]
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError(f"attribute {key}: {value} is not a JSON number")
            encoded = str(value)
        else:
            encoded = json.dumps(value, ensure_ascii=False)
        parts.append(f"{json.dumps(key, ensure_ascii=False)}: {encoded}")
    return "{" + ", ".join(parts) + "}"


_COLUMNS = (
    "sku",
    "name_de",
    "name_en",
    "category",
    "manufacturer",
    "mpn",
    "unit",
    "pack_size",
    "list_price",
    "attributes",
    "discontinued",
    "successor_sku",
)
_UPDATED = [c for c in _COLUMNS if c != "sku"]
_PLACEHOLDERS = ["%(attributes)s::jsonb" if c == "attributes" else f"%({c})s" for c in _COLUMNS]

# Insert or update one product by sku. The WHERE clause skips rows that didn't change,
# so reloading the same catalog writes nothing and updated_at stays meaningful.
UPSERT_SQL = f"""
INSERT INTO products ({", ".join(_COLUMNS)})
VALUES ({", ".join(_PLACEHOLDERS)})
ON CONFLICT (sku) DO UPDATE SET
    {", ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATED)},
    updated_at = now()
WHERE ({", ".join(f"products.{c}" for c in _UPDATED)})
    IS DISTINCT FROM ({", ".join(f"EXCLUDED.{c}" for c in _UPDATED)})
"""


def _row(p: Product) -> dict[str, Any]:
    return {
        "sku": p.sku,
        "name_de": p.name_de,
        "name_en": p.name_en,
        # .value explicitly: psycopg would otherwise dump an Enum by its name ("PIECE").
        "category": p.category.value,
        "manufacturer": p.manufacturer,
        "mpn": p.mpn,
        "unit": p.unit.value,
        "pack_size": p.pack_size,
        "list_price": p.list_price,
        "attributes": attributes_json(p.attributes),
        "discontinued": p.discontinued,
        "successor_sku": p.successor_sku,
    }


def load_products(conn: psycopg.Connection[Any], products: list[Product]) -> int:
    """Upsert all products in ONE transaction; return how many rows were sent."""
    rows = [_row(p) for p in products]
    # All or nothing: any failure (including the deferred FK check at commit) rolls back.
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
    return len(rows)


_CUSTOMER_COLUMNS = (
    "id",
    "name",
    "kind",
    "city",
    "postcode",
    "discount_tier",
    "delivery",
    "price_list_id",
)
_CUSTOMER_UPDATED = [c for c in _CUSTOMER_COLUMNS if c != "id"]

UPSERT_CUSTOMER_SQL = f"""
INSERT INTO customers ({", ".join(_CUSTOMER_COLUMNS)})
VALUES ({", ".join(f"%({c})s" for c in _CUSTOMER_COLUMNS)})
ON CONFLICT (id) DO UPDATE SET
    {", ".join(f"{c} = EXCLUDED.{c}" for c in _CUSTOMER_UPDATED)},
    updated_at = now()
WHERE ({", ".join(f"customers.{c}" for c in _CUSTOMER_UPDATED)})
    IS DISTINCT FROM ({", ".join(f"EXCLUDED.{c}" for c in _CUSTOMER_UPDATED)})
"""


def load_customers(
    conn: psycopg.Connection[Any], customers: list[Customer], entries: list[PriceListEntry]
) -> int:
    """Upsert customers and replace their price lists in ONE transaction.

    Price lists are replaced, not merged: a SKU dropped from a contract must lose its
    contract price. Returns how many customers were sent.
    """
    list_ids = sorted(
        {c.price_list_id for c in customers if c.price_list_id} | {e.price_list_id for e in entries}
    )
    customer_rows = [
        {**c.model_dump(include=set(_CUSTOMER_COLUMNS)), "kind": c.kind.value,
         "discount_tier": c.discount_tier.value, "delivery": c.delivery.value}
        for c in customers
    ]  # fmt: skip
    price_rows = [e.model_dump() for e in entries]

    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO price_lists (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
            [(i,) for i in list_ids],
        )
        cur.executemany(UPSERT_CUSTOMER_SQL, customer_rows)
        cur.execute("DELETE FROM prices WHERE price_list_id = ANY(%s)", (list_ids,))
        cur.executemany(
            "INSERT INTO prices (price_list_id, sku, min_qty, unit_price)"
            " VALUES (%(price_list_id)s, %(sku)s, %(min_qty)s, %(unit_price)s)",
            price_rows,
        )
    return len(customer_rows)


def load_orders(conn: psycopg.Connection[Any], orders: list[Order]) -> int:
    """Upsert orders and replace their lines in ONE transaction; return orders sent."""
    order_rows = [
        {"id": o.id, "customer_id": o.customer_id, "order_date": o.order_date} for o in orders
    ]
    line_rows = [{"order_id": o.id, **line.model_dump()} for o in orders for line in o.lines]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO orders (id, customer_id, order_date)"
            " VALUES (%(id)s, %(customer_id)s, %(order_date)s)"
            " ON CONFLICT (id) DO UPDATE SET"
            " customer_id = EXCLUDED.customer_id, order_date = EXCLUDED.order_date"
            " WHERE (orders.customer_id, orders.order_date)"
            " IS DISTINCT FROM (EXCLUDED.customer_id, EXCLUDED.order_date)",
            order_rows,
        )
        cur.execute("DELETE FROM order_lines WHERE order_id = ANY(%s)", ([o.id for o in orders],))
        cur.executemany(
            "INSERT INTO order_lines (order_id, line_no, sku, quantity, unit_price)"
            " VALUES (%(order_id)s, %(line_no)s, %(sku)s, %(quantity)s, %(unit_price)s)",
            line_rows,
        )
    return len(order_rows)

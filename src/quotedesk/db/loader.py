"""Load catalog products into PostgreSQL."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from quotedesk.catalog.models import AttrValue, Product

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def apply_schema(conn: psycopg.Connection[Any]) -> None:
    """Create tables and indexes (idempotent)."""
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


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

"""Load catalog products into PostgreSQL."""

from __future__ import annotations

import json  # noqa: F401
from decimal import Decimal  # noqa: F401
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
    raise NotImplementedError


# Insert or update one product by sku.
UPSERT_SQL = ""


def load_products(conn: psycopg.Connection[Any], products: list[Product]) -> int:
    """Upsert all products in ONE transaction; return how many rows were sent."""
    raise NotImplementedError

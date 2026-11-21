"""Database schema and loader. Needs PostgreSQL: `make db-up` then `make test-db`."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from quotedesk.catalog.models import Product
from quotedesk.db.loader import apply_schema, attributes_json, load_products

pytestmark = pytest.mark.db


# attributes_json needs no database, so it runs everywhere.
@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        ({}, "{}"),
        (
            {"type": "NYM-J", "cross_section_mm2": Decimal("1.5")},
            '{"cross_section_mm2": 1.5, "type": "NYM-J"}',
        ),
        ({"colour": "weiß", "dn": Decimal("20")}, '{"colour": "weiß", "dn": 20}'),
        ({"note": 'say "hi"'}, '{"note": "say \\"hi\\""}'),
    ],
)
def test_attributes_json(attrs: dict[str, Any], expected: str) -> None:
    assert attributes_json(attrs) == expected


# The `conn` (fresh schema per test) and `catalog` fixtures live in conftest.py.


def count(conn: Any) -> int:
    return int(conn.execute("SELECT count(*) FROM products").fetchone()[0])


def test_schema_is_rerunnable(conn: Any) -> None:
    apply_schema(conn)
    apply_schema(conn)


def test_load_and_idempotent(conn: Any, catalog: list[Product]) -> None:
    assert load_products(conn, catalog) == len(catalog)
    assert count(conn) == len(catalog)
    load_products(conn, catalog)
    assert count(conn) == len(catalog)


def test_upsert_updates_changed_rows(conn: Any, catalog: list[Product]) -> None:
    load_products(conn, catalog)
    changed = catalog[0].model_copy(update={"list_price": Decimal("123.45")})
    load_products(conn, [changed])
    price = conn.execute("SELECT list_price FROM products WHERE sku = %s", (changed.sku,))
    assert price.fetchone()[0] == Decimal("123.45")


def test_successor_loaded_in_any_order(conn: Any, catalog: list[Product]) -> None:
    old = next(p for p in catalog if p.successor_sku)
    succ = next(p for p in catalog if p.sku == old.successor_sku)
    load_products(conn, [old, succ])  # FK must be deferred to the end of the transaction
    assert count(conn) == 2


def test_all_or_nothing(conn: Any, catalog: list[Product]) -> None:
    broken = catalog[0].model_copy(update={"discontinued": True, "successor_sku": "MH-999999"})
    with pytest.raises(Exception):  # noqa: B017 - FK violation at commit
        load_products(conn, [*catalog[1:50], broken])
    assert count(conn) == 0


def test_constraints(conn: Any, catalog: list[Product]) -> None:
    import psycopg

    load_products(conn, catalog[:5])
    bad_statements = [
        "UPDATE products SET list_price = 0 WHERE sku = 'MH-100001'",
        "UPDATE products SET unit = 'KG' WHERE sku = 'MH-100001'",
        "UPDATE products SET sku = 'XX-1' WHERE sku = 'MH-100001'",
        "UPDATE products SET successor_sku = 'MH-100002' WHERE sku = 'MH-100001'",
        "UPDATE products SET mpn = (SELECT mpn FROM products WHERE sku = 'MH-100002'),"
        " manufacturer = (SELECT manufacturer FROM products WHERE sku = 'MH-100002')"
        " WHERE sku = 'MH-100001'",
    ]
    for sql in bad_statements:
        with pytest.raises(psycopg.errors.IntegrityError):
            conn.execute(sql)


def test_attributes_are_exact_json_numbers(conn: Any, catalog: list[Product]) -> None:
    load_products(conn, catalog)
    row = conn.execute(
        "SELECT attributes->'cross_section_mm2', jsonb_typeof(attributes->'cross_section_mm2')"
        " FROM products WHERE name_de = 'Mantelleitung NYM-J 3x1,5 mm² Ring 100 m' LIMIT 1"
    ).fetchone()
    assert row[1] == "number"
    assert Decimal(str(row[0])) == Decimal("1.5")
    # Containment query on the GIN index: every 2,5 mm² cable.
    n = conn.execute(
        """SELECT count(*) FROM products WHERE attributes @> '{"cross_section_mm2": 2.5}'"""
    ).fetchone()[0]
    assert n > 0


def test_german_full_text_search(conn: Any, catalog: list[Product]) -> None:
    load_products(conn, catalog)
    # German stemming: "Steckdosen" (plural) finds "Steckdose".
    rows = conn.execute(
        "SELECT name_de FROM products"
        " WHERE search_tsv @@ websearch_to_tsquery('german', 'Steckdosen Klappdeckel')"
    ).fetchall()
    assert rows and all("Klappdeckel" in r[0] for r in rows)


def test_indexes_exist(conn: Any) -> None:
    rows = conn.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'products'").fetchall()
    defs = " ".join(r[0] for r in rows)
    assert "gin (search_tsv)" in defs
    assert "gin (attributes jsonb_path_ops)" in defs
    assert "(category)" in defs

"""Customers and price lists in Postgres. Needs PostgreSQL: `make db-up`, `make test-db`."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from quotedesk.catalog.models import Product
from quotedesk.customers.generator import generate_customers
from quotedesk.customers.models import Customer, PriceListEntry
from quotedesk.db.loader import load_customers, load_products

pytestmark = pytest.mark.db


@pytest.fixture
def seeded(conn: Any, catalog: list[Product]) -> tuple[list[Customer], list[PriceListEntry]]:
    load_products(conn, catalog)
    customers, entries = generate_customers(catalog, seed=42)
    load_customers(conn, customers, entries)
    return customers, entries


def scalar(conn: Any, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def test_load_and_idempotent(
    conn: Any, seeded: tuple[list[Customer], list[PriceListEntry]]
) -> None:
    customers, entries = seeded
    assert scalar(conn, "SELECT count(*) FROM customers") == len(customers)
    assert scalar(conn, "SELECT count(*) FROM prices") == len(entries)
    before = scalar(conn, "SELECT max(updated_at) FROM customers")
    load_customers(conn, customers, entries)
    assert scalar(conn, "SELECT count(*) FROM prices") == len(entries)
    assert scalar(conn, "SELECT max(updated_at) FROM customers") == before  # nothing rewritten


def test_enums_stored_as_values(
    conn: Any, seeded: tuple[list[Customer], list[PriceListEntry]]
) -> None:
    kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM customers")}
    assert kinds == {"electrician", "hvac_installer", "facility_manager"}


def test_price_list_is_replaced_not_merged(
    conn: Any, seeded: tuple[list[Customer], list[PriceListEntry]]
) -> None:
    customers, entries = seeded
    pl = next(c.price_list_id for c in customers if c.price_list_id)
    kept = [e for e in entries if e.price_list_id != pl]
    dropped_one = [e for e in entries if e.price_list_id == pl][1:]
    load_customers(conn, customers, kept + dropped_one)
    n = scalar(conn, f"SELECT count(*) FROM prices WHERE price_list_id = '{pl}'")
    assert n == len(dropped_one)


def test_all_or_nothing(conn: Any, catalog: list[Product]) -> None:
    load_products(conn, catalog)
    customers, entries = generate_customers(catalog, seed=42)
    broken = [*entries, entries[0].model_copy(update={"sku": "MH-999999"})]  # FK violation
    with pytest.raises(Exception):  # noqa: B017
        load_customers(conn, customers, broken)
    assert scalar(conn, "SELECT count(*) FROM customers") == 0
    assert scalar(conn, "SELECT count(*) FROM prices") == 0


def test_constraints(conn: Any, seeded: tuple[list[Customer], list[PriceListEntry]]) -> None:
    import psycopg

    for sql in [
        "UPDATE customers SET discount_tier = 'Z' WHERE id = 'CU-0001'",
        "UPDATE customers SET postcode = '123' WHERE id = 'CU-0001'",
        "UPDATE customers SET price_list_id = 'PL-9999' WHERE id = 'CU-0001'",
        "UPDATE prices SET unit_price = 0",
        "INSERT INTO prices VALUES ('PL-0001', 'MH-999999', 1, 1.00)",
    ]:
        with pytest.raises(psycopg.errors.IntegrityError):
            conn.execute(sql)


def test_contract_price_lookup(
    conn: Any, seeded: tuple[list[Customer], list[PriceListEntry]]
) -> None:
    # The query the pricing engine will need: best contract price for a quantity.
    _, entries = seeded
    e = next(x for x in entries if any(
        y.sku == x.sku and y.price_list_id == x.price_list_id and y.min_qty > x.min_qty
        for y in entries
    ))  # fmt: skip
    big = e.min_qty * 100
    price = scalar(
        conn,
        "SELECT unit_price FROM prices"
        f" WHERE price_list_id = '{e.price_list_id}' AND sku = '{e.sku}' AND min_qty <= {big}"
        " ORDER BY min_qty DESC LIMIT 1",
    )
    assert price < e.unit_price
    assert isinstance(price, Decimal)


def test_orders_load_and_query(conn: Any, catalog: list[Product]) -> None:
    from quotedesk.customers.orders import generate_orders, last_order
    from quotedesk.db.loader import load_orders

    load_products(conn, catalog)
    customers, entries = generate_customers(catalog, seed=42)
    load_customers(conn, customers, entries)
    orders = generate_orders(catalog, customers, entries, seed=42)
    assert load_orders(conn, orders) == len(orders)
    load_orders(conn, orders)  # idempotent
    assert scalar(conn, "SELECT count(*) FROM orders") == len(orders)
    n_lines = sum(len(o.lines) for o in orders)
    assert scalar(conn, "SELECT count(*) FROM order_lines") == n_lines

    # "wie letztes Mal" in SQL matches the Python helper.
    cid = customers[0].id
    expected = last_order(orders, cid)
    assert expected is not None
    got = scalar(
        conn,
        f"SELECT id FROM orders WHERE customer_id = '{cid}'"
        " ORDER BY order_date DESC, id DESC LIMIT 1",
    )
    assert got == expected.id

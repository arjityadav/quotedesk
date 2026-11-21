"""Order history."""

from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from quotedesk.catalog.generator import generate_catalog, round_price
from quotedesk.catalog.models import Product
from quotedesk.customers.generator import generate_customers
from quotedesk.customers.models import KIND_CATEGORIES, TIER_DISCOUNT, Customer, PriceListEntry
from quotedesk.customers.orders import (
    HISTORY_DAYS,
    HISTORY_END,
    Order,
    OrderLine,
    generate_orders,
    last_order,
    paid_price,
    validate_orders,
)

Data = tuple[list[Product], list[Customer], list[PriceListEntry], list[Order]]


@pytest.fixture(scope="module")
def data() -> Data:
    catalog = generate_catalog(seed=42)
    customers, entries = generate_customers(catalog, seed=42)
    return catalog, customers, entries, generate_orders(catalog, customers, entries, seed=42)


def test_valid(data: Data) -> None:
    catalog, customers, _, orders = data
    assert validate_orders(orders, customers, catalog) == []


@pytest.mark.parametrize("seed", range(5))
def test_valid_for_any_seed(data: Data, seed: int) -> None:
    catalog, customers, entries, _ = data
    orders = generate_orders(catalog, customers, entries, seed=seed)
    assert validate_orders(orders, customers, catalog) == []


def test_shape(data: Data) -> None:
    _, customers, _, orders = data
    per_customer = Counter(o.customer_id for o in orders)
    assert set(per_customer) == {c.id for c in customers}
    assert all(3 <= n <= 8 for n in per_customer.values())
    assert [o.id for o in orders] == [f"OR-{n:06d}" for n in range(1, len(orders) + 1)]
    assert [o.order_date for o in orders] == sorted(o.order_date for o in orders)
    first = HISTORY_END.toordinal() - HISTORY_DAYS + 1
    assert all(date.fromordinal(first) <= o.order_date <= HISTORY_END for o in orders)


def test_deterministic(data: Data) -> None:
    catalog, customers, entries, orders = data
    assert generate_orders(catalog, customers, entries, seed=42) == orders


def test_customers_reorder_the_same_items(data: Data) -> None:
    # The point of a basket: "wie letztes Mal" has something to refer to.
    _, customers, _, orders = data
    for c in customers:
        skus = Counter(line.sku for o in orders if o.customer_id == c.id for line in o.lines)
        assert max(skus.values()) >= 2, c.id


def test_items_fit_the_trade(data: Data) -> None:
    catalog, customers, _, orders = data
    by_sku = {p.sku: p for p in catalog}
    kind = {c.id: c.kind for c in customers}
    for o in orders:
        for line in o.lines:
            assert by_sku[line.sku].category in KIND_CATEGORIES[kind[o.customer_id]]


def test_some_past_items_are_now_discontinued(data: Data) -> None:
    catalog, _, _, orders = data
    discontinued = {p.sku for p in catalog if p.discontinued}
    assert any(line.sku in discontinued for o in orders for line in o.lines)


def test_paid_price_prefers_the_best_contract_break(data: Data) -> None:
    catalog, customers, entries, _ = data
    by_sku = {p.sku: p for p in catalog}
    customer = next(c for c in customers if c.price_list_id)
    contract = [e for e in entries if e.price_list_id == customer.price_list_id]
    rows = sorted((e for e in contract if e.sku == contract[0].sku), key=lambda e: e.min_qty)
    product = by_sku[rows[0].sku]
    assert paid_price(product, customer, rows[0].min_qty, contract) == rows[0].unit_price
    assert paid_price(product, customer, 10**6, contract) == rows[-1].unit_price


def test_paid_price_without_contract_uses_tier(data: Data) -> None:
    catalog, customers, _, _ = data
    customer = customers[0]
    product = catalog[0]
    discount = TIER_DISCOUNT[customer.discount_tier]
    assert paid_price(product, customer, 1, []) == round_price(product.list_price * (1 - discount))


def test_last_order(data: Data) -> None:
    _, customers, _, orders = data
    cid = customers[0].id
    last = last_order(orders, cid)
    assert last is not None
    assert all(o.order_date <= last.order_date for o in orders if o.customer_id == cid)
    assert last_order(orders, "CU-9999") is None


def test_validate_reports_problems(data: Data) -> None:
    catalog, customers, _, _ = data
    ring = next(p for p in catalog if p.pack_size == 100)
    line = OrderLine(line_no=1, sku=ring.sku, quantity=100, unit_price=Decimal("1.00"))
    bad = [
        Order(id="OR-000001", customer_id="CU-9999", order_date=date(2027, 1, 1), lines=(line,)),
        Order(id="OR-000001", customer_id=customers[0].id, order_date=date(2026, 1, 1),
              lines=(line, line.model_copy(update={"line_no": 3, "quantity": 30}),
                     line.model_copy(update={"line_no": 4, "sku": "MH-999999"}))),
    ]  # fmt: skip
    text = "\n".join(validate_orders(bad, customers, catalog))
    assert "duplicate order id OR-000001" in text
    assert "unknown customer CU-9999" in text
    assert "after 2026-10-31" in text
    assert "line numbers [1, 3, 4]" in text
    assert f"{ring.sku} appears on 2 lines" in text
    assert "MH-999999 is not in the catalog" in text
    assert "quantity 30 is not a multiple of pack size 100" in text

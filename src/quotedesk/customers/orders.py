"""Order history: what each customer bought before.

Requests like "wie letztes Mal" or "die gleichen Dosen wie im März" only make sense
against a customer's past orders. Customers re-order from a small regular basket, and
some basket items have been discontinued since (a re-order should get the successor).
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from quotedesk.catalog.generator import round_price
from quotedesk.catalog.models import Money, Product
from quotedesk.customers.models import (
    KIND_CATEGORIES,
    SKU,
    TIER_DISCOUNT,
    Customer,
    PriceListEntry,
)

# Fixed window, never relative to today: same seed -> same orders, whenever you run it.
HISTORY_END = date(2026, 10, 31)
HISTORY_DAYS = 365
BASKET_SIZE = (6, 14)
ORDERS_PER_CUSTOMER = (3, 8)
LINES_PER_ORDER = (1, 8)
FROM_BASKET = 0.8  # share of order lines taken from the regular basket


class OrderLine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    line_no: int = Field(ge=1)
    sku: str = Field(pattern=SKU)
    quantity: int = Field(gt=0, description="in the product's order unit (m, pieces, packs)")
    unit_price: Money


class Order(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^OR-\d{6}$")
    customer_id: str = Field(pattern=r"^CU-\d{4}$")
    order_date: date
    lines: tuple[OrderLine, ...] = Field(min_length=1)


def paid_price(
    product: Product, customer: Customer, quantity: int, contract: list[PriceListEntry]
) -> Decimal:
    """Contract price for this quantity if there is one, else list price minus the tier."""
    applicable = [e for e in contract if e.sku == product.sku and e.min_qty <= quantity]
    if applicable:
        return max(applicable, key=lambda e: e.min_qty).unit_price
    return round_price(product.list_price * (1 - TIER_DISCOUNT[customer.discount_tier]))


def generate_orders(
    catalog: list[Product],
    customers: list[Customer],
    entries: list[PriceListEntry],
    seed: int = 42,
) -> list[Order]:
    """Past orders for every customer, oldest first. Deterministic."""
    rng = random.Random(seed)
    by_list: dict[str, list[PriceListEntry]] = {}
    for e in entries:
        by_list.setdefault(e.price_list_id, []).append(e)

    drafts: list[tuple[date, str, list[tuple[Product, int, Decimal]]]] = []
    for customer in customers:
        fits = sorted(
            (p for p in catalog if p.category in KIND_CATEGORIES[customer.kind]),
            key=lambda p: p.sku,
        )
        # `fits` includes discontinued products on purpose: they were on sale back then.
        # A re-order of one ("wie letztes Mal") must resolve to its successor.
        basket = rng.sample(fits, k=min(len(fits), rng.randint(*BASKET_SIZE)))
        contract = by_list.get(customer.price_list_id or "", [])

        for _ in range(rng.randint(*ORDERS_PER_CUSTOMER)):
            day = HISTORY_END - timedelta(days=rng.randrange(HISTORY_DAYS))
            n_lines = rng.randint(*LINES_PER_ORDER)
            picked: list[Product] = []
            while len(picked) < n_lines:
                pool = basket if rng.random() < FROM_BASKET else fits
                product = rng.choice(pool)
                if product not in picked:
                    picked.append(product)
            lines = []
            for product in sorted(picked, key=lambda p: p.sku):
                quantity = product.pack_size * rng.randint(1, 10)
                lines.append((product, quantity, paid_price(product, customer, quantity, contract)))
            drafts.append((day, customer.id, lines))

    # Number orders chronologically across all customers, like a real ERP.
    drafts.sort(key=lambda d: (d[0], d[1]))
    return [
        Order(
            id=f"OR-{number:06d}",
            customer_id=customer_id,
            order_date=day,
            lines=tuple(
                OrderLine(line_no=i, sku=p.sku, quantity=q, unit_price=price)
                for i, (p, q, price) in enumerate(lines, start=1)
            ),
        )
        for number, (day, customer_id, lines) in enumerate(drafts, start=1)
    ]


def validate_orders(
    orders: list[Order], customers: list[Customer], catalog: list[Product]
) -> list[str]:
    """Check orders against customers and the catalog. One message per problem."""
    problems: list[str] = []
    ids = Counter(o.id for o in orders)
    for oid in sorted(i for i, n in ids.items() if n > 1):
        problems.append(f"duplicate order id {oid} ({ids[oid]} times)")

    customer_ids = {c.id for c in customers}
    by_sku = {p.sku: p for p in catalog}
    for o in orders:
        if o.customer_id not in customer_ids:
            problems.append(f"{o.id}: unknown customer {o.customer_id}")
        if o.order_date > HISTORY_END:
            problems.append(f"{o.id}: dated {o.order_date}, after {HISTORY_END}")
        numbers = [line.line_no for line in o.lines]
        if numbers != list(range(1, len(o.lines) + 1)):
            problems.append(f"{o.id}: line numbers {numbers} are not 1..{len(o.lines)}")
        skus = Counter(line.sku for line in o.lines)
        for sku in sorted(s for s, n in skus.items() if n > 1):
            problems.append(f"{o.id}: {sku} appears on {skus[sku]} lines")
        for line in o.lines:
            product = by_sku.get(line.sku)
            if product is None:
                problems.append(f"{o.id}: {line.sku} is not in the catalog")
            elif line.quantity % product.pack_size:
                problems.append(
                    f"{o.id}: {line.sku} quantity {line.quantity}"
                    f" is not a multiple of pack size {product.pack_size}"
                )
    return problems


def last_order(orders: list[Order], customer_id: str) -> Order | None:
    """The customer's most recent order ("wie letztes Mal")."""
    mine = [o for o in orders if o.customer_id == customer_id]
    return max(mine, key=lambda o: (o.order_date, o.id)) if mine else None

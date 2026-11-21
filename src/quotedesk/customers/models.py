"""Customer domain model for Musterhandel GmbH (fictional).

Two ways a customer gets a better price than list:
  * a discount tier (A/B/C), a flat percentage off the list price for everything, and
  * an optional contract price list: fixed net prices for selected SKUs, optionally with
    quantity breaks (a lower price from min_qty upwards).
The pricing engine decides which applies; this module only holds and checks the data.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from quotedesk.catalog.models import Category, Money, Product

CUSTOMER_ID = r"^CU-\d{4}$"
PRICE_LIST_ID = r"^PL-\d{4}$"
SKU = r"^MH-\d{6}$"


class CustomerKind(StrEnum):
    ELECTRICIAN = "electrician"
    HVAC_INSTALLER = "hvac_installer"
    FACILITY_MANAGER = "facility_manager"


class DiscountTier(StrEnum):
    A = "A"
    B = "B"
    C = "C"


TIER_DISCOUNT: dict[DiscountTier, Decimal] = {
    DiscountTier.A: Decimal("0.18"),
    DiscountTier.B: Decimal("0.12"),
    DiscountTier.C: Decimal("0.06"),
}


class Delivery(StrEnum):
    STANDARD = "standard"
    EXPRESS = "express"
    PICKUP = "pickup"


# What each kind of customer typically buys. Contract prices are drawn from these.
KIND_CATEGORIES: dict[CustomerKind, frozenset[Category]] = {
    CustomerKind.ELECTRICIAN: frozenset(
        {
            Category.CABLE,
            Category.WIRE,
            Category.CABLE_GLAND,
            Category.JUNCTION_BOX,
            Category.CIRCUIT_BREAKER,
            Category.RCD,
            Category.SOCKET,
            Category.SWITCH,
            Category.LIGHTING,
            Category.CONDUIT,
        }
    ),
    CustomerKind.HVAC_INSTALLER: frozenset({Category.PRESS_FITTING, Category.BALL_VALVE}),
    CustomerKind.FACILITY_MANAGER: frozenset(
        {
            Category.LIGHTING,
            Category.SOCKET,
            Category.SWITCH,
            Category.CIRCUIT_BREAKER,
            Category.BALL_VALVE,
        }
    ),
}


class Customer(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    id: str = Field(pattern=CUSTOMER_ID)
    name: str = Field(min_length=1)
    kind: CustomerKind
    city: str = Field(min_length=1)
    postcode: str = Field(pattern=r"^\d{5}$")
    discount_tier: DiscountTier
    delivery: Delivery
    price_list_id: str | None = Field(default=None, pattern=PRICE_LIST_ID)


class PriceListEntry(BaseModel):
    """A contract net price for one SKU, valid from min_qty (in order units) upwards."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    price_list_id: str = Field(pattern=PRICE_LIST_ID)
    sku: str = Field(pattern=SKU)
    min_qty: int = Field(ge=1)
    unit_price: Money


def validate_customers(
    customers: list[Customer], entries: list[PriceListEntry], catalog: list[Product]
) -> list[str]:
    """Check customers and price lists against each other and the catalog.

    Returns one message per problem (empty = valid); never raises.
    """
    problems: list[str] = []

    ids = Counter(c.id for c in customers)
    for cid in sorted(i for i, n in ids.items() if n > 1):
        problems.append(f"duplicate customer id {cid} ({ids[cid]} times)")

    keys = Counter((e.price_list_id, e.sku, e.min_qty) for e in entries)
    for (pl, sku, qty), n in sorted(keys.items()):
        if n > 1:
            problems.append(f"{pl}: duplicate price for {sku} from {qty} ({n} times)")

    used = {c.price_list_id for c in customers if c.price_list_id}
    defined = {e.price_list_id for e in entries}
    for pl in sorted(used - defined):
        problems.append(f"{pl}: assigned to a customer but has no prices")
    for pl in sorted(defined - used):
        problems.append(f"{pl}: has prices but no customer uses it")

    by_sku = {p.sku: p for p in catalog}
    breaks: dict[tuple[str, str], list[PriceListEntry]] = defaultdict(list)
    for e in entries:
        product = by_sku.get(e.sku)
        if product is None:
            problems.append(f"{e.price_list_id}: {e.sku} is not in the catalog")
            continue
        if product.discontinued:
            problems.append(f"{e.price_list_id}: {e.sku} is discontinued")
        if e.unit_price >= product.list_price:
            problems.append(
                f"{e.price_list_id}: {e.sku} contract price {e.unit_price}"
                f" is not below list price {product.list_price}"
            )
        if e.min_qty % product.pack_size:
            problems.append(
                f"{e.price_list_id}: {e.sku} min_qty {e.min_qty}"
                f" is not a multiple of pack size {product.pack_size}"
            )
        breaks[(e.price_list_id, e.sku)].append(e)

    # Quantity breaks: each higher break must be cheaper per unit, or it is pointless.
    for (pl, sku), rows in sorted(breaks.items()):
        rows.sort(key=lambda e: e.min_qty)
        for lower, higher in zip(rows, rows[1:], strict=False):
            if higher.unit_price > lower.unit_price:
                problems.append(
                    f"{pl}: {sku} costs more from {higher.min_qty}"
                    f" ({higher.unit_price}) than from {lower.min_qty} ({lower.unit_price})"
                )
            elif higher.unit_price == lower.unit_price:
                problems.append(
                    f"{pl}: {sku} break at {higher.min_qty} is not cheaper"
                    f" than from {lower.min_qty} ({lower.unit_price})"
                )

    return problems

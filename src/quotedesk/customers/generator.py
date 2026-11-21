"""Deterministic generator for synthetic customers and contract price lists."""

from __future__ import annotations

import random
from decimal import Decimal

from quotedesk.catalog.generator import round_price
from quotedesk.catalog.models import Product
from quotedesk.customers.models import (
    KIND_CATEGORIES,
    TIER_DISCOUNT,
    Customer,
    CustomerKind,
    Delivery,
    DiscountTier,
    PriceListEntry,
)

# All fictional. Names, cities and postcodes are plausible, not real businesses.
DIRECTORY: tuple[tuple[str, CustomerKind, str, str], ...] = (
    ("Elektro Brandt GmbH", CustomerKind.ELECTRICIAN, "Hamburg", "22041"),
    ("Elektrotechnik Wiesner & Sohn", CustomerKind.ELECTRICIAN, "Bremen", "28195"),
    ("Haustechnik Kälberer KG", CustomerKind.HVAC_INSTALLER, "Stuttgart", "70173"),
    ("Lichtwerk Installationen GmbH", CustomerKind.ELECTRICIAN, "Köln", "50667"),
    ("Sanitär Dörflinger GmbH", CustomerKind.HVAC_INSTALLER, "Freiburg im Breisgau", "79098"),
    ("Gebäudeservice Nordstern GmbH", CustomerKind.FACILITY_MANAGER, "Hannover", "30159"),
    ("Elektro Yılmaz GmbH", CustomerKind.ELECTRICIAN, "Berlin", "10997"),
    ("Wärme & Wasser Petersen", CustomerKind.HVAC_INSTALLER, "Kiel", "24103"),
    ("Objektservice Rhein-Main GmbH", CustomerKind.FACILITY_MANAGER, "Frankfurt am Main", "60311"),
    ("Elektro Hufnagel", CustomerKind.ELECTRICIAN, "München", "80331"),
    ("Heizungsbau Ostermann GmbH", CustomerKind.HVAC_INSTALLER, "Leipzig", "04109"),
    ("Elektroinstallation Kowalski", CustomerKind.ELECTRICIAN, "Dortmund", "44135"),
    ("Klinikservice Süd GmbH", CustomerKind.FACILITY_MANAGER, "Augsburg", "86150"),
    ("Elektro Lindqvist GmbH", CustomerKind.ELECTRICIAN, "Rostock", "18055"),
    ("Gebäudetechnik Amrhein GmbH & Co. KG", CustomerKind.HVAC_INSTALLER, "Würzburg", "97070"),
)  # fmt: skip

PRICE_LIST_SHARE = 0.6  # share of customers with a contract price list
BREAK_SHARE = 0.3  # share of contract SKUs that get a quantity-break price
BREAK_MULTIPLE = 10  # break starts at 10 packs
BREAK_DISCOUNT = Decimal("0.97")


def generate_customers(
    catalog: list[Product], seed: int = 42
) -> tuple[list[Customer], list[PriceListEntry]]:
    """Customers and their contract prices. Same catalog + seed -> identical output."""
    rng = random.Random(seed)
    active = sorted((p for p in catalog if not p.discontinued), key=lambda p: p.sku)
    customers: list[Customer] = []
    entries: list[PriceListEntry] = []
    price_lists = 0

    for number, (name, kind, city, postcode) in enumerate(DIRECTORY, start=1):
        tier = rng.choice(list(DiscountTier))
        delivery = rng.choice(list(Delivery))
        price_list_id = None

        if rng.random() < PRICE_LIST_SHARE:
            price_lists += 1
            price_list_id = f"PL-{price_lists:04d}"
            pool = [p for p in active if p.category in KIND_CATEGORIES[kind]]
            chosen = rng.sample(pool, k=min(len(pool), rng.randint(10, 30)))
            for product in sorted(chosen, key=lambda p: p.sku):
                # Contract price: tier discount plus a negotiated 2-8 % on top.
                discount = TIER_DISCOUNT[tier] + Decimal(rng.randint(2, 8)) / 100
                price = round_price(product.list_price * (1 - discount))
                entries.append(
                    PriceListEntry(
                        price_list_id=price_list_id,
                        sku=product.sku,
                        min_qty=product.pack_size,
                        unit_price=price,
                    )
                )
                break_price = round_price(price * BREAK_DISCOUNT)
                # Draw first, then check: keeps the random sequence independent of prices.
                # On very cheap items 3 % off rounds back to the same cent; skip those.
                if rng.random() < BREAK_SHARE and break_price < price:
                    entries.append(
                        PriceListEntry(
                            price_list_id=price_list_id,
                            sku=product.sku,
                            min_qty=product.pack_size * BREAK_MULTIPLE,
                            unit_price=break_price,
                        )
                    )

        customers.append(
            Customer(
                id=f"CU-{number:04d}",
                name=name,
                kind=kind,
                city=city,
                postcode=postcode,
                discount_tier=tier,
                delivery=delivery,
                price_list_id=price_list_id,
            )
        )

    return customers, entries

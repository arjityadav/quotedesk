"""Deterministic generator for the synthetic catalog."""

from __future__ import annotations

import itertools
import random
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from quotedesk.catalog.families import FAMILIES, MANUFACTURER_PREFIX, Family
from quotedesk.catalog.models import Product

FIRST_SKU = 100001
CENT = Decimal("0.01")
SUCCESSOR_MARKUP = Decimal("1.05")


def round_price(value: Decimal) -> Decimal:
    """Round to cents, half up (commercial rounding), never below 0.01."""
    return max(value.quantize(CENT, rounding=ROUND_HALF_UP), CENT)


def expand_family(family: Family) -> list[dict[str, object]]:
    """All option combinations of a family, in axis order, as dicts."""
    names = list(family.axes)
    combos = itertools.product(*family.axes.values())
    return [dict(zip(names, combo, strict=True)) for combo in combos]


def generate_catalog(seed: int = 42, discontinue_rate: float = 0.03) -> list[Product]:
    """Generate the Musterhandel catalog. Same seed -> identical catalog."""
    rng = random.Random(seed)  # own generator: the global random state never leaks in
    products: list[Product] = []
    next_sku = FIRST_SKU
    mpn_counter: Counter[str] = Counter()

    for family in FAMILIES:
        for options in expand_family(family):
            manufacturer = str(options["manufacturer"])
            mpn_counter[manufacturer] += 1
            pack_size = options.get("pack_size", family.default_pack_size)
            assert isinstance(pack_size, int)
            jitter = Decimal(str(round(rng.uniform(0.9, 1.1), 4)))
            products.append(
                Product(
                    sku=f"MH-{next_sku}",
                    name_de=family.name_de(options),
                    name_en=family.name_en(options),
                    category=family.category,
                    manufacturer=manufacturer,
                    mpn=f"{MANUFACTURER_PREFIX[manufacturer]}-{mpn_counter[manufacturer]:05d}",
                    unit=family.unit,
                    pack_size=pack_size,
                    list_price=round_price(family.base_price(options) * jitter),
                    attributes=family.attributes(options),
                )
            )
            next_sku += 1

    # Discontinue a random sample; each gets a slightly dearer successor with a new SKU.
    k = round(discontinue_rate * len(products))
    for i in sorted(rng.sample(range(len(products)), k)):
        old = products[i]
        successor = old.model_copy(
            update={
                "sku": f"MH-{next_sku}",
                "mpn": old.mpn + "N",
                "list_price": round_price(old.list_price * SUCCESSOR_MARKUP),
            }
        )
        next_sku += 1
        products.append(successor)
        products[i] = old.model_copy(update={"discontinued": True, "successor_sku": successor.sku})

    return sorted(products, key=lambda p: p.sku)

"""Deterministic generator for the synthetic catalog."""

from __future__ import annotations

import itertools  # noqa: F401
import random  # noqa: F401
from decimal import ROUND_HALF_UP, Decimal  # noqa: F401

from quotedesk.catalog.families import FAMILIES, MANUFACTURER_PREFIX, Family  # noqa: F401
from quotedesk.catalog.models import Product

FIRST_SKU = 100001
CENT = Decimal("0.01")


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
    raise NotImplementedError

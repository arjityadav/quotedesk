"""Catalog domain model for Musterhandel GmbH (fictional).

The Product model is the contract the rest of QuoteDesk builds on: the generator creates
Products, the BMEcat writer serialises them, the parser turns supplier files back into
Products, and later modules match customer requests against them.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    CABLE = "cable"
    WIRE = "wire"
    CABLE_GLAND = "cable_gland"
    JUNCTION_BOX = "junction_box"
    CIRCUIT_BREAKER = "circuit_breaker"
    RCD = "rcd"
    SOCKET = "socket"
    SWITCH = "switch"
    LIGHTING = "lighting"
    CONDUIT = "conduit"
    PRESS_FITTING = "press_fitting"
    BALL_VALVE = "ball_valve"


class Unit(StrEnum):
    """Order units as UN/ECE Recommendation 20 codes (what BMEcat files use)."""

    PIECE = "C62"
    METRE = "MTR"
    PACK = "PK"


# Attribute values are either text ("IP65", "weiß") or exact decimals (Decimal("1.5")).
# Never floats: 1.5 mm² must compare equal after a round trip through XML and the database.
AttrValue = str | Decimal

# Attributes every product of a category must have. Later modules rely on these keys
# as hard filters ("1.5 mm² must equal 1.5 mm²").
REQUIRED_ATTRIBUTES: dict[Category, frozenset[str]] = {
    Category.CABLE: frozenset({"type", "cores", "cross_section_mm2"}),
    Category.WIRE: frozenset({"type", "cross_section_mm2", "colour"}),
    Category.CABLE_GLAND: frozenset({"thread", "material"}),
    Category.JUNCTION_BOX: frozenset({"ip_rating", "size_mm"}),
    Category.CIRCUIT_BREAKER: frozenset({"characteristic", "rated_current_a", "poles"}),
    Category.RCD: frozenset({"rated_current_a", "residual_current_ma", "poles", "rcd_type"}),
    Category.SOCKET: frozenset({"variant", "colour", "series"}),
    Category.SWITCH: frozenset({"variant", "colour", "series"}),
    Category.LIGHTING: frozenset({"variant", "colour_temperature_k", "power_w"}),
    Category.CONDUIT: frozenset({"variant", "diameter_mm"}),
    Category.PRESS_FITTING: frozenset({"shape", "diameter_mm", "material"}),
    Category.BALL_VALVE: frozenset({"dn", "connection", "material"}),
}

SKU_PATTERN = re.compile(r"^MH-\d{6}$")
IP_PATTERN = re.compile(r"^IP[0-6][0-9]$")

Money = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]


class Product(BaseModel):
    """One sellable article. Prices are net list prices in EUR per order unit."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    sku: str
    name_de: str = Field(min_length=1)
    name_en: str = Field(min_length=1)
    category: Category
    manufacturer: str = Field(min_length=1)
    mpn: str = Field(min_length=1, description="manufacturer part number")
    unit: Unit
    pack_size: int = Field(ge=1, description="order multiple, e.g. a 100 m ring")
    list_price: Money
    attributes: dict[str, AttrValue] = Field(default_factory=dict)
    discontinued: bool = False
    successor_sku: str | None = None

    @model_validator(mode="after")
    def check_rules(self) -> Product:
        """Per-product rules: SKU format, required attributes, IP rating, successor."""
        if not SKU_PATTERN.fullmatch(self.sku):
            raise ValueError(f"sku {self.sku!r} must look like MH-123456")

        required = REQUIRED_ATTRIBUTES[self.category]
        missing = required - self.attributes.keys()
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"{self.category} {self.sku} is missing required attributes: {names}")

        if "ip_rating" in self.attributes:
            ip = self.attributes["ip_rating"]
            if not isinstance(ip, str) or not IP_PATTERN.fullmatch(ip):
                raise ValueError(f"{self.sku}: invalid ip_rating {ip!r}, expected e.g. 'IP65'")

        if self.successor_sku is not None:
            if not self.discontinued:
                raise ValueError(
                    f"{self.sku}: successor_sku is only allowed on a discontinued product"
                )
            if self.successor_sku == self.sku:
                raise ValueError(f"{self.sku}: a product cannot be its own successor (itself)")

        return self


def validate_catalog(products: list[Product]) -> list[str]:
    """Check rules that need the whole catalog. Return a list of problems (empty = valid)."""
    problems: list[str] = []

    counts = Counter(p.sku for p in products)
    for sku in sorted(s for s, n in counts.items() if n > 1):
        problems.append(f"duplicate sku {sku} ({counts[sku]} times)")

    by_sku = {p.sku: p for p in products}
    for p in products:
        if p.successor_sku is None:
            continue
        successor = by_sku.get(p.successor_sku)
        if successor is None:
            problems.append(f"{p.sku}: successor {p.successor_sku} is not in the catalog")
        elif successor.discontinued:
            problems.append(f"{p.sku}: successor {successor.sku} is itself discontinued")
        elif successor.category != p.category:
            problems.append(
                f"{p.sku}: successor {successor.sku} is in a different category "
                f"({successor.category}, expected {p.category})"
            )

    return problems

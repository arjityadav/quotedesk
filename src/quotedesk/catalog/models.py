"""Catalog domain model for Musterhandel GmbH (fictional).

The Product model is the contract the rest of QuoteDesk builds on: the generator creates
Products, the BMEcat writer serialises them, the parser turns supplier files back into
Products, and later modules match customer requests against them.
"""

from __future__ import annotations

import re
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
        return self


def validate_catalog(products: list[Product]) -> list[str]:
    """Check rules that need the whole catalog. Return a list of problems (empty = valid)."""
    raise NotImplementedError

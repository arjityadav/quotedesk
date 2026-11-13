"""Read and write catalogs in a simplified subset of BMEcat 2005.

BMEcat is the XML format German suppliers and wholesalers use to exchange product catalogs.
Real files are messy: missing namespaces, German decimal commas, unit aliases, rows that
break the rules. The parser must survive all of that and report what it skipped.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation  # noqa: F401

from lxml import etree
from pydantic import BaseModel, ValidationError

from quotedesk.catalog.models import (  # noqa: F401
    AttrValue,
    Category,
    Product,
    Unit,
    validate_catalog,
)

NS = "http://www.bmecat.org/bmecat/2005"
FEATURE_SYSTEM = "MH-2026"

# How suppliers actually write units. Keys are upper-case.
UNIT_ALIASES: dict[str, Unit] = {
    "C62": Unit.PIECE, "PCE": Unit.PIECE, "STK": Unit.PIECE, "ST": Unit.PIECE,
    "STÜCK": Unit.PIECE, "PIECE": Unit.PIECE,
    "MTR": Unit.METRE, "M": Unit.METRE, "METER": Unit.METRE, "METRE": Unit.METRE,
    "PK": Unit.PACK, "VE": Unit.PACK, "PACK": Unit.PACK,
}  # fmt: skip

# FUNIT written for attribute keys with these suffixes (informational for readers).
_FEATURE_UNITS = {"_mm2": "MMK", "_mm": "MMT", "_a": "AMP", "_ma": "4K", "_k": "KEL", "_w": "WTT"}

LANG_DE = {"deu", "ger", "de"}
LANG_EN = {"eng", "en"}


class BMEcatError(Exception):
    """The whole file is unusable (not XML, not BMEcat, or unsafe)."""


class ParseIssue(BaseModel):
    position: int | None  # 1-based PRODUCT position in the file; None for catalog-level issues
    sku: str | None
    message: str


class ParseResult(BaseModel):
    products: list[Product]
    issues: list[ParseIssue]


# --------------------------------------------------------------------------- writer


def _el(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    child = etree.SubElement(parent, f"{{{NS}}}{tag}", attrs)
    if text is not None:
        child.text = text
    return child


def _funit(key: str) -> str | None:
    for suffix, code in _FEATURE_UNITS.items():
        if key.endswith(suffix):
            return code
    return None


def to_bmecat(
    products: list[Product],
    *,
    catalog_id: str = "MH-2026",
    catalog_version: str = "1.0",
    supplier_name: str = "Musterhandel GmbH",
    generation_date: date = date(2026, 1, 1),
) -> bytes:
    """Serialise products to BMEcat 2005 XML (UTF-8, namespaced, pretty-printed)."""
    root = etree.Element(f"{{{NS}}}BMECAT", {"version": "2005"}, nsmap={None: NS})
    header = _el(root, "HEADER")
    catalog = _el(header, "CATALOG")
    _el(catalog, "LANGUAGE", "deu", default="true")
    _el(catalog, "LANGUAGE", "eng")
    _el(catalog, "CATALOG_ID", catalog_id)
    _el(catalog, "CATALOG_VERSION", catalog_version)
    _el(catalog, "GENERATION_DATE", generation_date.isoformat())
    _el(catalog, "CURRENCY", "EUR")
    supplier = _el(header, "SUPPLIER")
    _el(supplier, "SUPPLIER_NAME", supplier_name)

    body = _el(root, "T_NEW_CATALOG")
    for p in products:
        prod = _el(body, "PRODUCT", mode="new")
        _el(prod, "SUPPLIER_PID", p.sku)
        details = _el(prod, "PRODUCT_DETAILS")
        _el(details, "DESCRIPTION_SHORT", p.name_de, lang="deu")
        _el(details, "DESCRIPTION_SHORT", p.name_en, lang="eng")
        _el(details, "MANUFACTURER_PID", p.mpn)
        _el(details, "MANUFACTURER_NAME", p.manufacturer)
        if p.discontinued:
            _el(details, "PRODUCT_STATUS", "discontinued", type="old_product")

        features = _el(prod, "PRODUCT_FEATURES")
        _el(features, "REFERENCE_FEATURE_SYSTEM_NAME", FEATURE_SYSTEM)
        _el(features, "REFERENCE_FEATURE_GROUP_ID", p.category.value)
        for key, value in p.attributes.items():
            feature = _el(features, "FEATURE")
            _el(feature, "FNAME", key)
            _el(feature, "FVALUE", str(value))
            unit = _funit(key)
            if unit:
                _el(feature, "FUNIT", unit)

        order = _el(prod, "PRODUCT_ORDER_DETAILS")
        _el(order, "ORDER_UNIT", p.unit.value)
        _el(order, "CONTENT_UNIT", p.unit.value)
        _el(order, "NO_CU_PER_OU", "1")
        _el(order, "QUANTITY_MIN", str(p.pack_size))
        _el(order, "QUANTITY_INTERVAL", str(p.pack_size))

        prices = _el(prod, "PRODUCT_PRICE_DETAILS")
        price = _el(prices, "PRODUCT_PRICE", price_type="net_list")
        _el(price, "PRICE_AMOUNT", str(p.list_price))
        _el(price, "PRICE_CURRENCY", "EUR")

        if p.successor_sku:
            ref = _el(prod, "PRODUCT_REFERENCE", type="followup")
            _el(ref, "PROD_ID_TO", p.successor_sku)

    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True))


# --------------------------------------------------------------------------- parser


def parse_decimal(text: str | None) -> Decimal:
    """Parse a supplier number (German decimal comma allowed)."""
    raise NotImplementedError


def parse_feature_value(text: str | None) -> AttrValue:
    """FVALUE -> Decimal if it is a number, else the stripped text."""
    raise NotImplementedError


def parse_unit(text: str | None) -> Unit:
    """Map a unit code or alias to Unit, case-insensitive."""
    raise NotImplementedError


def parse_bmecat(data: bytes) -> ParseResult:
    """Parse BMEcat XML into Products, collecting per-product issues instead of failing."""
    raise NotImplementedError


def _text(el: etree._Element, path: str) -> str | None:
    """Helper: stripped text of the first match, or None."""
    found = el.find(path)
    if found is None or found.text is None:
        return None
    return str(found.text).strip()


def _short(e: ValidationError) -> str:
    """Helper: a one-line message from a pydantic ValidationError."""
    first = e.errors()[0]
    loc = ".".join(str(x) for x in first["loc"])
    return f"{loc}: {first['msg']}" if loc else str(first["msg"])

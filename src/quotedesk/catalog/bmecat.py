"""Read and write catalogs in a simplified subset of BMEcat 2005.

BMEcat is the XML format German suppliers and wholesalers use to exchange product catalogs.
Real files are messy: missing namespaces, German decimal commas, unit aliases, rows that
break the rules. The parser must survive all of that and report what it skipped.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from lxml import etree
from pydantic import BaseModel, ValidationError

from quotedesk.catalog.models import (
    AttrValue,
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


# Plain decimal with one optional separator. A whitelist instead of trusting Decimal(),
# which would also accept "NaN", "Infinity", "1e3" and "1_000".
_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")


def parse_decimal(text: str | None) -> Decimal:
    """Parse a supplier number (German decimal comma allowed)."""
    if text is None:
        raise ValueError("number is missing")
    cleaned = text.strip()
    if not _NUMBER.fullmatch(cleaned):
        # Also rejects "1.234,50": guessing thousands separators silently changes prices.
        raise ValueError(f"not a plain decimal number: {text!r}")
    return Decimal(cleaned.replace(",", "."))


def parse_feature_value(text: str | None) -> AttrValue:
    """FVALUE -> Decimal if it is a number, else the stripped text."""
    if text is None or not text.strip():
        raise ValueError("feature value is empty")
    try:
        return parse_decimal(text)
    except ValueError:
        return text.strip()


def parse_unit(text: str | None) -> Unit:
    """Map a unit code or alias to Unit, case-insensitive."""
    if text is None or not text.strip():
        raise ValueError("order unit is missing")
    unit = UNIT_ALIASES.get(text.strip().upper())
    if unit is None:
        raise ValueError(f"unknown order unit {text.strip()!r}")
    return unit


def parse_bmecat(data: bytes) -> ParseResult:
    """Parse BMEcat XML into Products, collecting per-product issues instead of failing."""
    root = _parse_xml(data)

    products: list[Product] = []
    issues: list[ParseIssue] = []
    for position, prod in enumerate(root.iterfind("{*}T_NEW_CATALOG/{*}PRODUCT"), start=1):
        try:
            products.append(_parse_product(prod))
        except ValueError as e:  # pydantic's ValidationError is a ValueError too
            message = _short(e) if isinstance(e, ValidationError) else str(e)
            sku = _text(prod, "{*}SUPPLIER_PID")
            issues.append(ParseIssue(position=position, sku=sku, message=message))

    for problem in validate_catalog(products):
        issues.append(ParseIssue(position=None, sku=None, message=problem))

    return ParseResult(products=products, issues=issues)


def _parse_xml(data: bytes) -> etree._Element:
    """Parse untrusted XML safely and check it is a BMEcat document."""
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        raise BMEcatError(f"not well-formed XML: {e}") from e
    if root is None:
        raise BMEcatError("empty document")
    # A DOCTYPE is how XXE and entity-expansion ("billion laughs") attacks get in.
    # BMEcat never needs one, so refuse the whole file.
    if root.getroottree().docinfo.doctype:
        raise BMEcatError("DOCTYPE declarations are not allowed")
    if etree.QName(root).localname != "BMECAT":
        raise BMEcatError(f"root element is {etree.QName(root).localname}, expected BMECAT")
    return root


def _parse_product(prod: etree._Element) -> Product:
    """One PRODUCT element -> Product. Raises ValueError on any problem."""
    name_de: str | None = None
    name_en: str | None = None
    for desc in prod.iterfind("{*}PRODUCT_DETAILS/{*}DESCRIPTION_SHORT"):
        text = (desc.text or "").strip()
        lang = (desc.get("lang") or "").strip().lower()
        if not lang or lang in LANG_DE:  # no lang attribute means German
            name_de = name_de or text
        elif lang in LANG_EN:
            name_en = name_en or text

    attributes: dict[str, AttrValue] = {}
    for feature in prod.iterfind("{*}PRODUCT_FEATURES/{*}FEATURE"):
        fname = _text(feature, "{*}FNAME")
        if not fname:
            raise ValueError("FEATURE without FNAME")
        try:
            attributes[fname] = parse_feature_value(_text(feature, "{*}FVALUE"))
        except ValueError as e:
            raise ValueError(f"feature {fname}: {e}") from e

    raw_pack = _text(prod, "{*}PRODUCT_ORDER_DETAILS/{*}QUANTITY_INTERVAL")
    pack = parse_decimal(raw_pack) if raw_pack is not None else Decimal(1)
    if pack != pack.to_integral_value():
        raise ValueError(f"pack size must be a whole number, got {raw_pack!r}")

    list_price = None
    for price in prod.iterfind("{*}PRODUCT_PRICE_DETAILS/{*}PRODUCT_PRICE"):
        if price.get("price_type") == "net_list":
            list_price = parse_decimal(_text(price, "{*}PRICE_AMOUNT"))
            break
    if list_price is None:
        raise ValueError("no net_list price")

    discontinued = any(
        status.get("type") == "old_product" for status in prod.iterfind(".//{*}PRODUCT_STATUS")
    )
    successor_sku = None
    for ref in prod.iterfind("{*}PRODUCT_REFERENCE"):
        if ref.get("type") == "followup":
            successor_sku = _text(ref, "{*}PROD_ID_TO")
            break

    # model_validate runs every field and model rule, exactly like Product(...).
    return Product.model_validate(
        {
            "sku": _text(prod, "{*}SUPPLIER_PID"),
            "name_de": name_de,
            "name_en": name_en or name_de,
            "category": _text(prod, "{*}PRODUCT_FEATURES/{*}REFERENCE_FEATURE_GROUP_ID"),
            "manufacturer": _text(prod, "{*}PRODUCT_DETAILS/{*}MANUFACTURER_NAME"),
            "mpn": _text(prod, "{*}PRODUCT_DETAILS/{*}MANUFACTURER_PID"),
            "unit": parse_unit(_text(prod, "{*}PRODUCT_ORDER_DETAILS/{*}ORDER_UNIT")),
            "pack_size": int(pack),
            "list_price": list_price,
            "attributes": attributes,
            "discontinued": discontinued,
            "successor_sku": successor_sku,
        }
    )


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

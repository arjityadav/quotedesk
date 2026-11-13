"""Reading supplier catalogs in BMEcat."""

from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from quotedesk.catalog.bmecat import (
    NS,
    BMEcatError,
    parse_bmecat,
    parse_decimal,
    parse_feature_value,
    parse_unit,
    to_bmecat,
)
from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Category, Unit

FIXTURES = Path(__file__).parent / "fixtures"


# ----------------------------------------------------------------------- small helpers


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1.5", "1.5"), (" 1,5 ", "1.5"), ("16", "16"), ("0,89", "0.89"), ("4.995", "4.995")],
)
def test_parse_decimal(text: str, expected: str) -> None:
    assert parse_decimal(text) == Decimal(expected)


@pytest.mark.parametrize("text", [None, "", "  ", "abc", "NaN", "Infinity", "1.234,50", "1,2,3"])
def test_parse_decimal_rejects(text: str | None) -> None:
    with pytest.raises(ValueError):
        parse_decimal(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,5", Decimal("1.5")),
        ("16", Decimal("16")),
        ("IP65", "IP65"),
        (" weiß ", "weiß"),
        ("NaN", "NaN"),
        ("100x100", "100x100"),
    ],
)
def test_parse_feature_value(text: str, expected: object) -> None:
    value = parse_feature_value(text)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize("text", [None, "", "   "])
def test_parse_feature_value_rejects_empty(text: str | None) -> None:
    with pytest.raises(ValueError):
        parse_feature_value(text)


@pytest.mark.parametrize(
    ("text", "unit"),
    [("C62", Unit.PIECE), ("stk", Unit.PIECE), (" Stk ", Unit.PIECE), ("PCE", Unit.PIECE),
     ("m", Unit.METRE), ("MTR", Unit.METRE), ("VE", Unit.PACK)],
)  # fmt: skip
def test_parse_unit(text: str, unit: Unit) -> None:
    assert parse_unit(text) is unit


def test_parse_unit_unknown() -> None:
    with pytest.raises(ValueError, match="KG"):
        parse_unit("KG")


# ----------------------------------------------------------------------- writer


def test_writer_output_is_namespaced_bmecat() -> None:
    products = generate_catalog(seed=1)[:5]
    xml = to_bmecat(products)
    root = etree.fromstring(xml)
    assert root.tag == f"{{{NS}}}BMECAT"
    assert root.get("version") == "2005"
    assert len(root.findall(f"{{{NS}}}T_NEW_CATALOG/{{{NS}}}PRODUCT")) == 5
    assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")


# ----------------------------------------------------------------------- parser


def test_round_trip_full_catalog() -> None:
    products = generate_catalog(seed=42)
    result = parse_bmecat(to_bmecat(products))
    assert result.issues == []
    assert result.products == products


def test_messy_supplier_file() -> None:
    result = parse_bmecat((FIXTURES / "messy_supplier.xml").read_bytes())
    parsed = {p.sku: p for p in result.products}

    assert sorted(parsed) == ["MH-900001", "MH-900002", "MH-900005", "MH-900007"]

    cable = parsed["MH-900001"]
    assert cable.category is Category.CABLE
    assert cable.unit is Unit.METRE
    assert cable.pack_size == 100
    assert cable.list_price == Decimal("0.89")  # net_list, not net_customer
    assert cable.attributes["cross_section_mm2"] == Decimal("1.5")
    assert cable.name_en.startswith("Sheathed cable")

    breaker = parsed["MH-900002"]
    assert breaker.name_de == breaker.name_en == "Leitungsschutzschalter B16 1-polig 6 kA"
    assert breaker.unit is Unit.PIECE
    assert breaker.pack_size == 1
    assert breaker.attributes["rated_current_a"] == Decimal("16")

    valve = parsed["MH-900005"]
    assert valve.discontinued is True
    assert valve.successor_sku == "MH-900099"
    assert valve.list_price == Decimal("13.90")

    gland = parsed["MH-900007"]
    assert gland.unit is Unit.PACK and gland.pack_size == 10

    row_issues = {i.position: i for i in result.issues if i.position is not None}
    assert sorted(row_issues) == [3, 4, 6]
    assert row_issues[3].sku == "MH-900003" and "price" in row_issues[3].message.lower()
    assert row_issues[4].sku == "MH-900004" and "ip_rating" in row_issues[4].message
    assert row_issues[6].sku == "MH-900006"

    catalog_issues = [i for i in result.issues if i.position is None]
    assert len(catalog_issues) == 1
    assert "MH-900099" in catalog_issues[0].message


def test_rejects_xxe() -> None:
    with pytest.raises(BMEcatError, match="(?i)doctype"):
        parse_bmecat((FIXTURES / "xxe.xml").read_bytes())


def test_rejects_internal_entity_bomb() -> None:
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE BMECAT [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>
<BMECAT version="2005"><T_NEW_CATALOG/></BMECAT>"""
    with pytest.raises(BMEcatError):
        parse_bmecat(bomb)


@pytest.mark.parametrize(
    "data",
    [b"", b"not xml", b"<BMECAT><unclosed></BMECAT>", b"<CATALOG version='2005'/>"],
)
def test_rejects_unusable_files(data: bytes) -> None:
    with pytest.raises(BMEcatError):
        parse_bmecat(data)


def test_empty_catalog() -> None:
    result = parse_bmecat(b"<BMECAT version='2005'><T_NEW_CATALOG/></BMECAT>")
    assert result.products == [] and result.issues == []

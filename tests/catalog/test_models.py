"""The Product model and catalog-level checks."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from quotedesk.catalog.models import Category, Product, Unit, validate_catalog


def make(**overrides: object) -> Product:
    data: dict[str, object] = {
        "sku": "MH-100001",
        "name_de": "Mantelleitung NYM-J 3x1,5 mm² Ring 100 m",
        "name_en": "Sheathed cable NYM-J 3x1.5 mm², 100 m ring",
        "category": Category.CABLE,
        "manufacturer": "Kabelwerk Nord",
        "mpn": "KWN-00001",
        "unit": Unit.METRE,
        "pack_size": 100,
        "list_price": Decimal("0.89"),
        "attributes": {"type": "NYM-J", "cores": Decimal("3"), "cross_section_mm2": Decimal("1.5")},
    }
    data.update(overrides)
    return Product(**data)  # type: ignore[arg-type]


def test_valid_product() -> None:
    p = make()
    assert p.sku == "MH-100001"
    assert p.attributes["cross_section_mm2"] == Decimal("1.5")


def test_strips_whitespace_and_is_frozen() -> None:
    p = make(name_de="  Mantelleitung  ")
    assert p.name_de == "Mantelleitung"
    with pytest.raises(ValidationError):
        p.sku = "MH-999999"  # type: ignore[misc]


@pytest.mark.parametrize("sku", ["MH-1", "mh-100001", "XX-100001", "MH-1000011", "MH100001"])
def test_rejects_bad_sku(sku: str) -> None:
    with pytest.raises(ValidationError, match="sku"):
        make(sku=sku)


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1.00"), Decimal("4.995")])
def test_rejects_bad_price(price: Decimal) -> None:
    with pytest.raises(ValidationError):
        make(list_price=price)


def test_money_is_exact() -> None:
    # Money stays exact: 0.1 + 0.2 as Decimals is exactly 0.30.
    p = make(list_price=Decimal("0.1") + Decimal("0.2"))
    assert p.list_price == Decimal("0.30")


def test_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make(colour="grau")


def test_missing_required_attribute() -> None:
    with pytest.raises(ValidationError, match="cross_section_mm2"):
        make(attributes={"type": "NYM-J", "cores": Decimal("3")})


@pytest.mark.parametrize("ip", ["IP6X", "ip65", "IP7", "IP70", "65"])
def test_rejects_bad_ip_rating(ip: str) -> None:
    with pytest.raises(ValidationError, match="ip_rating"):
        make(
            category=Category.JUNCTION_BOX,
            unit=Unit.PIECE,
            pack_size=1,
            attributes={"ip_rating": ip, "size_mm": "100x100"},
        )


def test_numeric_ip_rating_is_rejected() -> None:
    with pytest.raises(ValidationError, match="ip_rating"):
        make(
            category=Category.JUNCTION_BOX,
            attributes={"ip_rating": Decimal("65"), "size_mm": "100x100"},
        )


def test_accepts_valid_ip_rating() -> None:
    p = make(
        category=Category.JUNCTION_BOX,
        unit=Unit.PIECE,
        pack_size=1,
        attributes={"ip_rating": "IP65", "size_mm": "100x100"},
    )
    assert p.attributes["ip_rating"] == "IP65"


def test_successor_requires_discontinued() -> None:
    with pytest.raises(ValidationError, match="discontinued"):
        make(successor_sku="MH-100002")


def test_successor_cannot_be_itself() -> None:
    with pytest.raises(ValidationError, match="itself"):
        make(discontinued=True, successor_sku="MH-100001")


def test_validate_catalog_ok() -> None:
    a = make(discontinued=True, successor_sku="MH-100002")
    b = make(sku="MH-100002", mpn="KWN-00002")
    assert validate_catalog([a, b]) == []


def test_validate_catalog_reports_every_problem() -> None:
    dup1 = make(sku="MH-100001")
    dup2 = make(sku="MH-100001", mpn="KWN-00009")
    missing = make(sku="MH-100003", discontinued=True, successor_sku="MH-199999")
    old_succ = make(sku="MH-100005", mpn="KWN-00005", discontinued=True)
    points_to_old = make(sku="MH-100004", discontinued=True, successor_sku="MH-100005")
    other_cat = make(
        sku="MH-100006",
        category=Category.JUNCTION_BOX,
        unit=Unit.PIECE,
        pack_size=1,
        attributes={"ip_rating": "IP65", "size_mm": "100x100"},
    )
    cross = make(sku="MH-100007", discontinued=True, successor_sku="MH-100006")

    problems = validate_catalog([dup1, dup2, missing, old_succ, points_to_old, other_cat, cross])
    text = "\n".join(problems)
    assert len(problems) == 4, problems
    assert "duplicate" in text and "MH-100001" in text
    assert "MH-199999" in text
    assert "MH-100005" in text and "discontinued" in text
    assert "MH-100007" in text and "categor" in text

"""Deterministic catalog generation."""

from collections import defaultdict
from decimal import Decimal

import pytest

from quotedesk.catalog.families import FAMILIES, Family
from quotedesk.catalog.generator import expand_family, generate_catalog, round_price
from quotedesk.catalog.models import Category, Product, Unit, validate_catalog


@pytest.fixture(scope="module")
def catalog() -> list[Product]:
    return generate_catalog(seed=42)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.005", "1.01"),
        ("1.004", "1.00"),
        ("2.5", "2.50"),
        ("0.001", "0.01"),
        ("0", "0.01"),
        ("19.995", "20.00"),
    ],
)
def test_round_price(value: str, expected: str) -> None:
    result = round_price(Decimal(value))
    assert result == Decimal(expected)
    assert result.as_tuple().exponent == -2


def test_expand_family_order() -> None:
    fam = Family(
        key="t",
        category=Category.SWITCH,
        unit=Unit.PIECE,
        axes={"a": (1, 2), "b": ("x", "y")},
        attributes=lambda o: {},
        name_de=lambda o: "",
        name_en=lambda o: "",
        base_price=lambda o: Decimal(1),
    )
    assert expand_family(fam) == [
        {"a": 1, "b": "x"},
        {"a": 1, "b": "y"},
        {"a": 2, "b": "x"},
        {"a": 2, "b": "y"},
    ]


def test_size_and_unique_skus(catalog: list[Product]) -> None:
    assert len(catalog) >= 2000
    skus = [p.sku for p in catalog]
    assert len(set(skus)) == len(skus)
    assert skus == sorted(skus)
    assert skus[0] == "MH-100001"


def test_deterministic() -> None:
    assert generate_catalog(seed=7) == generate_catalog(seed=7)


def test_seed_changes_prices_not_structure() -> None:
    a, b = generate_catalog(seed=1), generate_catalog(seed=2)
    base_a = {p.mpn.rstrip("N"): p.name_de for p in a}
    base_b = {p.mpn.rstrip("N"): p.name_de for p in b}
    assert base_a == base_b
    assert [p.list_price for p in a] != [p.list_price for p in b]


def test_uses_its_own_rng() -> None:
    import random

    random.seed(0)
    first = generate_catalog(seed=3)
    random.seed(999)
    assert generate_catalog(seed=3) == first


def test_catalog_is_valid(catalog: list[Product]) -> None:
    assert validate_catalog(catalog) == []
    for p in catalog:
        Product.model_validate(p.model_dump())  # every row passes the model rules
        assert p.list_price.as_tuple().exponent == -2


def test_every_family_is_present(catalog: list[Product]) -> None:
    expected = {f.category for f in FAMILIES}
    assert {p.category for p in catalog} == expected


def test_mpn_per_manufacturer(catalog: list[Product]) -> None:
    originals = [p for p in catalog if not p.mpn.endswith("N")]
    assert len({(p.manufacturer, p.mpn) for p in catalog}) == len(catalog)
    kwn = sorted(p.mpn for p in originals if p.manufacturer == "Kabelwerk Nord")
    assert kwn[0] == "KWN-00001"


def test_discontinued_and_successors(catalog: list[Product]) -> None:
    by_sku = {p.sku: p for p in catalog}
    old = [p for p in catalog if p.discontinued]
    originals = len(catalog) - len(old)
    assert 0.02 <= len(old) / originals <= 0.04
    for p in old:
        assert p.successor_sku is not None
        succ = by_sku[p.successor_sku]
        assert not succ.discontinued
        assert succ.sku > p.sku
        assert succ.mpn == p.mpn + "N"
        assert (succ.category, succ.attributes, succ.name_de) == (
            p.category,
            p.attributes,
            p.name_de,
        )
        assert succ.list_price >= p.list_price


def test_near_duplicates_exist(catalog: list[Product]) -> None:
    # Same cable, different ring length: later modules must tell these apart.
    groups: dict[tuple[str, str], set[int]] = defaultdict(set)
    for p in catalog:
        if p.category is Category.CABLE and not p.discontinued:
            key = (p.manufacturer, str(sorted(p.attributes.items())))
            groups[key].add(p.pack_size)
    assert any(sizes == {50, 100} for sizes in groups.values())


def test_german_and_english_names(catalog: list[Product]) -> None:
    names_de = {p.name_de for p in catalog}
    assert "Mantelleitung NYM-J 3x1,5 mm² Ring 100 m" in names_de
    assert any("3x1.5 mm²" in p.name_en for p in catalog)
    assert any("ß" in p.name_de or "ü" in p.name_de for p in catalog)


def test_prices_are_plausible(catalog: list[Product]) -> None:
    for p in catalog:
        assert Decimal("0.01") <= p.list_price < Decimal("10000")

"""Which products fit the stated specs."""

from decimal import Decimal

import pytest

from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Category, Product
from quotedesk.rfqs.acceptable import Spec, acceptable_skus, fits, same_value, spec_of

D = Decimal
NYM_3x15 = {"type": "NYM-J", "cores": D(3), "cross_section_mm2": D("1.5")}


@pytest.fixture(scope="module")
def catalog() -> list[Product]:
    return generate_catalog(seed=42)


def by_sku(catalog: list[Product], sku: str) -> Product:
    return next(p for p in catalog if p.sku == sku)


def test_same_value() -> None:
    assert same_value(D("1.5"), D("1.50"))
    assert same_value("IP65", "ip65")
    assert not same_value(D("1.5"), "1.5")  # a number is never text
    assert not same_value(D("1.5"), D("2.5"))


def test_ring_length_open_gives_every_ring(catalog: list[Product]) -> None:
    skus = acceptable_skus(catalog, Spec(Category.CABLE, NYM_3x15))
    assert {"MH-100001", "MH-100002"} <= set(skus)  # 50 m and 100 m ring
    products = [by_sku(catalog, s) for s in skus]
    assert len({p.pack_size for p in products}) > 1
    assert all(p.attributes["cross_section_mm2"] == D("1.5") for p in products)


def test_stating_more_narrows(catalog: list[Product]) -> None:
    open_ = set(acceptable_skus(catalog, Spec(Category.CABLE, NYM_3x15)))
    ring = set(acceptable_skus(catalog, Spec(Category.CABLE, NYM_3x15, pack_size=100)))
    maker = set(
        acceptable_skus(
            catalog, Spec(Category.CABLE, NYM_3x15, manufacturer="Kabelwerk Nord", pack_size=100)
        )
    )
    assert maker <= ring < open_
    assert maker == {"MH-100002"}


def test_missing_hard_spec_widens_and_is_reported(catalog: list[Product]) -> None:
    spec = Spec(Category.CABLE, {"type": "NYM-J", "cross_section_mm2": D("1.5")})
    assert spec.missing_hard_specs() == {"cores"}
    cores = {by_sku(catalog, s).attributes["cores"] for s in acceptable_skus(catalog, spec)}
    assert len(cores) > 1
    assert Spec(Category.CABLE, NYM_3x15).missing_hard_specs() == frozenset()


def test_optional_attribute_left_out_is_not_missing() -> None:
    # Cable glands have a colour, but it isn't a required spec: leaving it out stays auto.
    spec = Spec(Category.CABLE_GLAND, {"thread": "M20", "material": "Kunststoff"})
    assert spec.missing_hard_specs() == frozenset()


def test_discontinued_never_acceptable_but_successor_is(catalog: list[Product]) -> None:
    old = next(p for p in catalog if p.discontinued)
    assert old.successor_sku is not None
    skus = acceptable_skus(catalog, spec_of(old, manufacturer=True, pack_size=True))
    assert old.sku not in skus
    assert old.successor_sku in skus


def test_wrong_category_or_unknown_attribute_fits_nothing(catalog: list[Product]) -> None:
    assert acceptable_skus(catalog, Spec(Category.WIRE, NYM_3x15)) == ()
    assert acceptable_skus(catalog, Spec(Category.CABLE, {"colour": "pink"})) == ()


def test_every_active_product_fits_its_own_spec(catalog: list[Product]) -> None:
    for p in catalog:
        if p.discontinued:
            continue
        assert fits(p, spec_of(p)), p.sku
        full = acceptable_skus(catalog, spec_of(p, manufacturer=True, pack_size=True))
        assert full == (p.sku,), p.sku  # everything stated: exactly one article


def test_spec_of_omits_and_rejects_unknown(catalog: list[Product]) -> None:
    p = by_sku(catalog, "MH-100001")
    spec = spec_of(p, omit=["cores"])
    assert "cores" not in spec.attributes
    assert spec.manufacturer is None and spec.pack_size is None
    with pytest.raises(ValueError, match="no attributes"):
        spec_of(p, omit=["colour"])


def test_result_is_sorted_and_deterministic(catalog: list[Product]) -> None:
    skus = acceptable_skus(catalog, Spec(Category.CABLE, {"type": "NYM-J"}))
    assert list(skus) == sorted(skus)
    assert acceptable_skus(reversed(catalog), Spec(Category.CABLE, {"type": "NYM-J"})) == skus

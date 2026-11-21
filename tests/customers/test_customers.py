"""Customers, discount tiers and contract price lists."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Product
from quotedesk.customers.generator import DIRECTORY, generate_customers
from quotedesk.customers.models import (
    KIND_CATEGORIES,
    Customer,
    CustomerKind,
    PriceListEntry,
    validate_customers,
)


@pytest.fixture(scope="module")
def catalog() -> list[Product]:
    return generate_catalog(seed=42)


@pytest.fixture(scope="module")
def generated(catalog: list[Product]) -> tuple[list[Customer], list[PriceListEntry]]:
    return generate_customers(catalog, seed=42)


def test_generated_data_is_valid(
    catalog: list[Product], generated: tuple[list[Customer], list[PriceListEntry]]
) -> None:
    customers, entries = generated
    assert validate_customers(customers, entries, catalog) == []


@pytest.mark.parametrize("seed", range(10))
def test_valid_for_any_seed(catalog: list[Product], seed: int) -> None:
    customers, entries = generate_customers(catalog, seed=seed)
    assert validate_customers(customers, entries, catalog) == []


def test_shape(generated: tuple[list[Customer], list[PriceListEntry]]) -> None:
    customers, entries = generated
    assert len(customers) == len(DIRECTORY) == 15
    assert [c.id for c in customers] == [f"CU-{n:04d}" for n in range(1, 16)]
    assert {c.kind for c in customers} == set(CustomerKind)
    with_list = [c for c in customers if c.price_list_id]
    assert 0 < len(with_list) < len(customers)
    assert any(e.min_qty > 1 for e in entries)  # some quantity breaks exist


def test_deterministic(catalog: list[Product]) -> None:
    assert generate_customers(catalog, seed=7) == generate_customers(catalog, seed=7)
    assert generate_customers(catalog, seed=1) != generate_customers(catalog, seed=2)


def test_contract_skus_fit_the_trade(
    catalog: list[Product], generated: tuple[list[Customer], list[PriceListEntry]]
) -> None:
    customers, entries = generated
    by_sku = {p.sku: p for p in catalog}
    kind_of = {c.price_list_id: c.kind for c in customers if c.price_list_id}
    for e in entries:
        assert by_sku[e.sku].category in KIND_CATEGORIES[kind_of[e.price_list_id]]


def test_customer_model_rules() -> None:
    good = {
        "id": "CU-0001",
        "name": "Elektro Test GmbH",
        "kind": "electrician",
        "city": "Hamburg",
        "postcode": "22041",
        "discount_tier": "A",
        "delivery": "standard",
    }
    assert Customer(**good).price_list_id is None  # type: ignore[arg-type]
    for field, bad in [("id", "C-1"), ("postcode", "2204"), ("discount_tier", "Z")]:
        with pytest.raises(ValidationError, match=field):
            Customer(**{**good, field: bad})  # type: ignore[arg-type]


def test_validate_reports_every_problem(catalog: list[Product]) -> None:
    active = next(p for p in catalog if not p.discontinued and p.pack_size == 1)
    old = next(p for p in catalog if p.discontinued)

    def entry(sku: str, price: str, qty: int = 1, pl: str = "PL-0001") -> PriceListEntry:
        return PriceListEntry(price_list_id=pl, sku=sku, min_qty=qty, unit_price=Decimal(price))

    cheap = str(active.list_price - Decimal("0.01"))
    customers = [
        Customer(id="CU-0001", name="A", kind=CustomerKind.ELECTRICIAN, city="X",
                 postcode="12345", discount_tier="A", delivery="pickup", price_list_id="PL-0001"),
        Customer(id="CU-0001", name="B", kind=CustomerKind.ELECTRICIAN, city="X",
                 postcode="12345", discount_tier="B", delivery="pickup", price_list_id="PL-0002"),
    ]  # fmt: skip
    entries = [
        entry(active.sku, cheap),
        entry(active.sku, cheap),  # duplicate
        entry("MH-999999", "1.00"),  # not in catalog
        entry(old.sku, "0.01", qty=old.pack_size),  # discontinued
        entry(active.sku, str(active.list_price), qty=5),  # not below list price
        entry(active.sku, "0.01", pl="PL-0003"),  # list nobody uses
    ]
    problems = validate_customers(customers, entries, catalog)
    text = "\n".join(problems)

    assert "duplicate customer id CU-0001" in text
    assert f"duplicate price for {active.sku}" in text
    assert "PL-0002: assigned to a customer but has no prices" in text
    assert "PL-0003: has prices but no customer uses it" in text
    assert "MH-999999 is not in the catalog" in text
    assert f"{old.sku} is discontinued" in text
    assert "is not below list price" in text
    assert "costs more from 5" in text  # the qty-5 row is dearer than the qty-1 row


def test_min_qty_must_match_pack_size(catalog: list[Product]) -> None:
    ring = next(p for p in catalog if not p.discontinued and p.pack_size == 100)
    customers = [
        Customer(id="CU-0001", name="A", kind=CustomerKind.ELECTRICIAN, city="X",
                 postcode="12345", discount_tier="A", delivery="pickup", price_list_id="PL-0001")
    ]  # fmt: skip
    entries = [
        PriceListEntry(
            price_list_id="PL-0001", sku=ring.sku, min_qty=30, unit_price=Decimal("0.01")
        )
    ]
    assert any(
        "not a multiple of pack size 100" in p
        for p in validate_customers(customers, entries, catalog)
    )

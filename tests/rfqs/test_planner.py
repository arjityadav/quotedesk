"""Planning regular requests: the label is right by construction."""

from collections import Counter

import pytest

from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Product
from quotedesk.customers.generator import generate_customers
from quotedesk.customers.models import KIND_CATEGORIES, Customer
from quotedesk.customers.orders import Order, generate_orders
from quotedesk.rfqs.models import Channel, RfqType, Route
from quotedesk.rfqs.planner import LINE_COUNTS, PlannedRfq, plan_rfq, rfq_rng
from quotedesk.rfqs.validate import validate_label

World = tuple[list[Product], list[Customer], list[Order]]
REGULAR = list(LINE_COUNTS)


@pytest.fixture(scope="module")
def world() -> World:
    catalog = generate_catalog(seed=42)
    customers, entries = generate_customers(catalog, seed=42)
    return catalog, customers, generate_orders(catalog, customers, entries, seed=42)


def plan(world: World, n: int, rfq_type: RfqType, seed: int = 42) -> PlannedRfq:
    catalog, customers, orders = world
    rid = f"rfq_{n:04d}"
    return plan_rfq(rid, rfq_type, catalog, customers, orders, rfq_rng(seed, rid))


@pytest.fixture(scope="module")
def many(world: World) -> list[PlannedRfq]:
    return [plan(world, n, REGULAR[n % 4]) for n in range(1, 241)]


def body(planned: PlannedRfq) -> str:
    return "\n".join(line.text for line in planned.lines)


def test_every_plan_passes_the_validator(world: World, many: list[PlannedRfq]) -> None:
    catalog, customers, orders = world
    for p in many:
        assert validate_label(p.label, body(p), catalog, customers, orders) == [], p.label.rfq_id


def test_deterministic_per_rfq(world: World) -> None:
    assert plan(world, 7, RfqType.MESSY_EMAIL) == plan(world, 7, RfqType.MESSY_EMAIL)
    # A different id or seed gives a different request.
    assert plan(world, 7, RfqType.MESSY_EMAIL) != plan(world, 8, RfqType.MESSY_EMAIL)
    assert plan(world, 7, RfqType.MESSY_EMAIL) != plan(world, 7, RfqType.MESSY_EMAIL, seed=1)


def test_rfq_rng_is_independent_of_order() -> None:
    a = rfq_rng(42, "rfq_0143").random()
    rfq_rng(42, "rfq_0142").random()
    assert rfq_rng(42, "rfq_0143").random() == a


@pytest.mark.parametrize("rfq_type", REGULAR)
def test_shape(world: World, rfq_type: RfqType) -> None:
    plans = [plan(world, n, rfq_type) for n in range(1, 41)]
    choices, _ = LINE_COUNTS[rfq_type]
    for p in plans:
        lab = p.label
        assert lab.type is rfq_type and lab.expected_route is Route.AUTO
        assert len(lab.lines) <= max(choices)
        assert len(p.lines) == len(lab.lines)
        assert [r.quote for r in p.lines] == [line.source_quote for line in lab.lines]
        assert lab.files == (
            f"{lab.rfq_id}.{ {'email': 'eml', 'pdf': 'pdf', 'scan': 'png'}[lab.channel] }",
        )
    assert max(len(p.label.lines) for p in plans) >= min(choices)


def test_items_fit_the_customers_trade(world: World, many: list[PlannedRfq]) -> None:
    catalog, _, _ = world
    by_sku = {p.sku: p for p in catalog}
    for p in many:
        trade = KIND_CATEGORIES[p.customer.kind]
        for line in p.label.lines:
            assert line.expected_sku is not None
            assert by_sku[line.expected_sku].category in trade


def test_lines_do_not_overlap(many: list[PlannedRfq]) -> None:
    for p in many:
        sets = [set(line.acceptable_skus) for line in p.label.lines]
        for i, a in enumerate(sets):
            for b in sets[i + 1 :]:
                assert not a & b, p.label.rfq_id


def test_clean_is_clean_messy_is_messy(many: list[PlannedRfq]) -> None:
    hard = {"typo", "abbreviation", "language_mix", "spec_spelling", "from_history", "mpn_only"}
    for p in many:
        tags = {t for line in p.label.lines for t in line.tags}
        if p.label.type is RfqType.CLEAN_EMAIL:
            assert not tags & hard, p.label.rfq_id
    messy_tags = Counter(
        t for p in many if p.label.type is RfqType.MESSY_EMAIL for line in p.label.lines
        for t in line.tags
    )  # fmt: skip
    for tag in ("typo", "abbreviation", "from_history", "discontinued_requested"):
        assert messy_tags[tag] > 0, tag


def test_open_specs_give_several_correct_answers(many: list[PlannedRfq]) -> None:
    lines = [line for p in many for line in p.label.lines]
    implicit = [line for line in lines if "ring_length_implicit" in line.tags]
    assert implicit
    assert all(len(line.acceptable_skus) > 1 for line in implicit)
    assert any(len(line.acceptable_skus) == 1 for line in lines)


def test_discontinued_lines_expect_the_successor(world: World, many: list[PlannedRfq]) -> None:
    catalog, _, _ = world
    by_sku = {p.sku: p for p in catalog}
    found = 0
    for p in many:
        for line in p.label.lines:
            if "discontinued_requested" in line.tags:
                found += 1
                assert line.expected_sku is not None
                assert not by_sku[line.expected_sku].discontinued
                old = next(x for x in catalog if x.successor_sku == line.expected_sku)
                assert old.mpn in line.source_quote
    assert found


def test_history_lines_reference_a_past_order(world: World, many: list[PlannedRfq]) -> None:
    _, _, orders = world
    found = 0
    for p in many:
        for line in p.label.lines:
            if "from_history" in line.tags:
                found += 1
                assert "wie " in line.source_quote
                past = [o for o in orders if o.customer_id == p.customer.id]
                assert past
    assert found


def test_channels(many: list[PlannedRfq]) -> None:
    expected = {
        RfqType.CLEAN_EMAIL: Channel.EMAIL,
        RfqType.MESSY_EMAIL: Channel.EMAIL,
        RfqType.TENDER_PDF: Channel.PDF,
        RfqType.SCANNED_PDF: Channel.SCAN,
    }
    for p in many:
        assert p.label.channel is expected[p.label.type]


def test_fixed_customer(world: World) -> None:
    catalog, customers, orders = world
    who = customers[3]
    p = plan_rfq(
        "rfq_0001", RfqType.CLEAN_EMAIL, catalog, customers, orders, rfq_rng(1, "x"), customer=who
    )
    assert p.label.customer_id == who.id


def test_special_types_are_planned_elsewhere(world: World) -> None:
    with pytest.raises(ValueError, match="rfqs.special"):
        plan(world, 1, RfqType.AMBIGUOUS)

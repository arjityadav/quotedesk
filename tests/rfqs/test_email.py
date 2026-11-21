"""Customer emails around the planned lines."""

from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime

import pytest

from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Product
from quotedesk.customers.generator import generate_customers
from quotedesk.customers.models import Customer
from quotedesk.customers.orders import Order, generate_orders
from quotedesk.rfqs.email import (
    ENGLISH,
    HOLDOUT,
    REGULAR,
    Bank,
    RenderedEmail,
    domain_of,
    read_body,
    render_email,
)
from quotedesk.rfqs.models import RfqType
from quotedesk.rfqs.planner import plan_rfq, rfq_rng
from quotedesk.rfqs.validate import validate_label

World = tuple[list[Product], list[Customer], list[Order]]


@pytest.fixture(scope="module")
def world() -> World:
    catalog = generate_catalog(seed=42)
    customers, entries = generate_customers(catalog, seed=42)
    return catalog, customers, generate_orders(catalog, customers, entries, seed=42)


def make(world: World, n: int, rfq_type: RfqType, holdout: bool = False) -> RenderedEmail:
    catalog, customers, orders = world
    rid = f"rfq_{n:04d}"
    rng = rfq_rng(42, rid)
    return render_email(plan_rfq(rid, rfq_type, catalog, customers, orders, rng), rng,
                        holdout=holdout)  # fmt: skip


@pytest.fixture(scope="module")
def emails(world: World) -> list[RenderedEmail]:
    types = [RfqType.CLEAN_EMAIL, RfqType.MESSY_EMAIL]
    return [make(world, n, types[n % 2], holdout=n % 5 == 0) for n in range(1, 201)]


def test_every_email_passes_the_validator(world: World, emails: list[RenderedEmail]) -> None:
    catalog, customers, orders = world
    for e in emails:
        assert (
            validate_label(e.label, read_body(e.raw), catalog, customers, orders) == []
        ), e.label.rfq_id


def test_body_round_trips_through_the_eml(emails: list[RenderedEmail]) -> None:
    for e in emails:
        assert read_body(e.raw) == e.body


def test_byte_identical_when_regenerated(world: World) -> None:
    assert make(world, 11, RfqType.MESSY_EMAIL).raw == make(world, 11, RfqType.MESSY_EMAIL).raw


def test_headers(world: World, emails: list[RenderedEmail]) -> None:
    _, customers, _ = world
    by_id = {c.id: c for c in customers}
    for e in emails:
        msg = message_from_bytes(e.raw, policy=policy.default)
        customer = by_id[str(e.label.customer_id)]
        assert msg["From"].addresses[0].domain == domain_of(customer.name)
        assert msg["To"].addresses[0].addr_spec == "angebote@musterhandel.example"
        assert msg["Subject"]
        assert msg["Message-ID"].startswith(f"<{e.label.rfq_id}.")
        sent = parsedate_to_datetime(msg["Date"])
        assert sent.weekday() < 5 and 7 <= sent.hour <= 17
        assert sent.year == 2026 and (sent.month, sent.day) >= (11, 2)  # after the history


def test_raw_file_is_readable_utf8(emails: list[RenderedEmail]) -> None:
    raw = emails[0].raw.decode("utf-8")
    assert "Content-Transfer-Encoding: 8bit" in raw
    assert emails[0].label.lines[0].source_quote in raw  # greppable without decoding


def test_domains_are_reserved_and_ascii() -> None:
    assert domain_of("Elektro Brandt GmbH") == "elektro-brandt.example"
    assert domain_of("Sanitär Dörflinger GmbH") == "sanitaer-doerflinger.example"
    assert domain_of("Elektro Yılmaz GmbH") == "elektro-yilmaz.example"
    assert domain_of("Gebäudetechnik Amrhein GmbH & Co. KG") == "gebaeudetechnik-amrhein.example"


def test_signature_names_the_customer(world: World, emails: list[RenderedEmail]) -> None:
    _, customers, _ = world
    by_id = {c.id: c for c in customers}
    for e in emails:
        c = by_id[str(e.label.customer_id)]
        assert c.name in e.body and f"{c.postcode} {c.city}" in e.body


def test_layout_tags(emails: list[RenderedEmail]) -> None:
    seen = {t for e in emails for t in e.label.tags}
    assert {"bullet_list", "pasted_table", "comma_list"} <= seen
    for e in emails:
        if e.label.type is RfqType.CLEAN_EMAIL:
            assert not {"pasted_table", "comma_list"} & set(e.label.tags)


def _phrases(bank: Bank) -> set[str]:
    return {
        p.split("{")[0].strip()
        for part in (bank.greetings, bank.openers, bank.closings)
        for p in part
        if p.split("{")[0].strip()
    }


def test_holdout_banks_do_not_share_phrases() -> None:
    assert not _phrases(HOLDOUT) & _phrases(REGULAR)
    assert not _phrases(HOLDOUT) & _phrases(ENGLISH)


def test_holdout_emails_use_only_holdout_openers(emails: list[RenderedEmail]) -> None:
    regular = [o.split("{")[0] for o in REGULAR.openers]
    held = [o.split("{")[0] for o in HOLDOUT.openers]
    holdout = [e for e in emails if "held_out_template" in e.label.tags]
    assert holdout
    for e in holdout:
        assert any(o in e.body for o in held)
        assert not any(o in e.body for o in regular)
    for e in emails:
        if "held_out_template" not in e.label.tags:
            assert not any(o in e.body for o in held)


def test_label_keeps_everything_but_tags(world: World) -> None:
    catalog, customers, orders = world
    rng = rfq_rng(42, "rfq_0003")
    planned = plan_rfq("rfq_0003", RfqType.MESSY_EMAIL, catalog, customers, orders, rng)
    e = render_email(planned, rng)
    assert e.label.model_copy(update={"tags": planned.label.tags}) == planned.label
    assert set(planned.label.tags) <= set(e.label.tags)

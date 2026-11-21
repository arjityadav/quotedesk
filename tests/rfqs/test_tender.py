"""Tender PDFs (GAEB-style Leistungsverzeichnis) rendered with Typst."""

import random
import re
from decimal import Decimal

import pytest

from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Product
from quotedesk.customers.generator import generate_customers
from quotedesk.customers.models import Customer
from quotedesk.customers.orders import Order, generate_orders
from quotedesk.rfqs.lines import render_line, tender_quantity
from quotedesk.rfqs.models import RfqType
from quotedesk.rfqs.planner import plan_rfq, rfq_rng
from quotedesk.rfqs.tender import RenderedTender, pdf_text, render_tender, typst_str
from quotedesk.rfqs.validate import quote_numbers, squash, validate_label

World = tuple[list[Product], list[Customer], list[Order]]


@pytest.fixture(scope="module")
def world() -> World:
    catalog = generate_catalog(seed=42)
    customers, entries = generate_customers(catalog, seed=42)
    return catalog, customers, generate_orders(catalog, customers, entries, seed=42)


def make(world: World, n: int, holdout: bool = False) -> RenderedTender:
    catalog, customers, orders = world
    rid = f"rfq_{n:04d}"
    rng = rfq_rng(42, rid)
    planned = plan_rfq(rid, RfqType.TENDER_PDF, catalog, customers, orders, rng)
    return render_tender(planned, rng, holdout=holdout)


@pytest.fixture(scope="module")
def tenders(world: World) -> list[RenderedTender]:
    return [make(world, n, holdout=n % 5 == 0) for n in range(1, 31)]


def test_every_tender_passes_the_validator(world: World, tenders: list[RenderedTender]) -> None:
    catalog, customers, orders = world
    for t in tenders:
        text = pdf_text(t.pdf)
        assert validate_label(t.label, text, catalog, customers, orders) == [], t.label.rfq_id


def test_byte_identical_when_regenerated(world: World) -> None:
    assert make(world, 3).pdf == make(world, 3).pdf


def test_is_a_pdf(tenders: list[RenderedTender]) -> None:
    assert all(t.pdf.startswith(b"%PDF-") for t in tenders)


def test_positions_read_as_one_line(tenders: list[RenderedTender]) -> None:
    """OZ, quantity, unit and short text come out of the text layer in that order."""
    for t in tenders:
        text = squash(pdf_text(t.pdf))
        for line in t.label.lines:
            assert re.search(r"\d{2}\.\d{2}\.\d{4} " + re.escape(line.source_quote), text)


def test_positions_are_numbered_by_title(tenders: list[RenderedTender]) -> None:
    for t in tenders:
        ozs = re.findall(r"\b(\d{2})\.(\d{2})\.(\d{4}) ", pdf_text(t.pdf))
        assert len(ozs) == len(t.label.lines)
        assert len({lot for lot, _, _ in ozs}) == 1  # one lot per tender
        titles = sorted({title for _, title, _ in ozs})
        assert titles == [f"{n:02d}" for n in range(1, len(titles) + 1)]
        for title in titles:
            positions = [int(pos) for _, t_no, pos in ozs if t_no == title]
            assert positions == [10 * n for n in range(1, len(positions) + 1)]  # 0010, 0020…


def test_label_order_follows_the_pdf(tenders: list[RenderedTender]) -> None:
    for t in tenders:
        text = squash(pdf_text(t.pdf))
        offsets = [text.index(line.source_quote) for line in t.label.lines]
        assert offsets == sorted(offsets)


def test_titles_subtotals_and_footer(tenders: list[RenderedTender]) -> None:
    t = tenders[0]
    text = pdf_text(t.pdf)
    assert "Leistungsverzeichnis" in text and "Vergabe-Nr.:" in text
    assert "Summe Titel" in text and "Angebotssumme netto" in text
    assert re.search(r"Seite 1 von \d+", text)


def test_long_tenders_span_pages(tenders: list[RenderedTender]) -> None:
    multi = [t for t in tenders if "multi_page" in t.label.tags]
    assert multi
    for t in multi:
        assert re.search(r"Seite 2 von \d+", pdf_text(t.pdf))


def test_holdout_preamble(tenders: list[RenderedTender]) -> None:
    for t in tenders:
        held = "held_out_template" in t.label.tags
        assert ("Hinweis an den Bieter" in pdf_text(t.pdf)) is held


def test_label_keeps_its_facts(world: World) -> None:
    catalog, customers, orders = world
    rng = rfq_rng(42, "rfq_0009")
    planned = plan_rfq("rfq_0009", RfqType.TENDER_PDF, catalog, customers, orders, rng)
    t = render_tender(planned, rng)
    assert sorted(t.label.lines, key=lambda x: x.source_quote) == sorted(
        planned.label.lines, key=lambda x: x.source_quote
    )
    assert t.label.customer_id == planned.label.customer_id


def test_tender_quantity_format() -> None:
    assert tender_quantity(Decimal(20)) == "20,000"
    assert tender_quantity(Decimal(1200)) == "1.200,000"
    assert tender_quantity(Decimal("12.5")) == "12,500"
    for q in ("20", "1200", "12.5", "250000"):
        assert Decimal(q) in quote_numbers(tender_quantity(Decimal(q)))


def test_tender_line_quote(world: World) -> None:
    catalog, _, _ = world
    cable = next(p for p in catalog if p.sku == "MH-100002")
    line = render_line(cable, Decimal(1200), random.Random(0), messy=False, tender=True)
    assert line.quote == "1.200,000 m NYM-J 3x1,5 mm²"


def test_typst_strings_are_escaped() -> None:
    assert typst_str('Rohr 1/2" #1 *a* _b_ \\ x') == '"Rohr 1/2\\" #1 *a* _b_ \\\\ x"'

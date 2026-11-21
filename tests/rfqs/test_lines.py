"""Rendering line items as customers type them."""

import random
import re
from collections import Counter
from decimal import Decimal

import pytest

from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Category, Product
from quotedesk.rfqs.lines import _Ctx, render_line, spell_cable_spec, typo
from quotedesk.rfqs.validate import LINE_TAGS, quantity_readable, quote_numbers

D = Decimal


@pytest.fixture(scope="module")
def catalog() -> list[Product]:
    return generate_catalog(seed=42)


def get(catalog: list[Product], sku: str) -> Product:
    return next(p for p in catalog if p.sku == sku)


def first(catalog: list[Product], category: Category) -> Product:
    return next(p for p in catalog if p.category is category and not p.discontinued)


def quantity_for(p: Product) -> Decimal:
    return D(p.pack_size * 2)


def renders(p: Product, n: int = 200, **kwargs: object) -> list[str]:
    return [
        render_line(p, quantity_for(p), random.Random(i), **kwargs).quote  # type: ignore[arg-type]
        for i in range(n)
    ]


# --- clean form -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sku_or_category", "quantity", "expected"),
    [
        ("MH-100001", "200", "200 m NYM-J 3x1,5 mm²"),
        (Category.CIRCUIT_BREAKER, "40", "40 Stück Leitungsschutzschalter"),
        (Category.RCD, "4", "4 Stück FI-Schutzschalter"),
        (Category.BALL_VALVE, "6", "6 Stück Kugelhahn DN"),
        (Category.PRESS_FITTING, "10", "10 Stück Pressfitting"),
    ],
)
def test_clean_form(
    catalog: list[Product], sku_or_category: str | Category, quantity: str, expected: str
) -> None:
    if isinstance(sku_or_category, Category):
        p = first(catalog, sku_or_category)
    else:
        p = get(catalog, sku_or_category)
    line = render_line(p, D(quantity), random.Random(0), messy=False)
    assert line.quote.startswith(expected)
    assert line.text == line.quote
    assert not {"typo", "abbreviation", "language_mix", "spec_spelling"} & set(line.tags)


def test_clean_form_ignores_the_seed(catalog: list[Product]) -> None:
    p = get(catalog, "MH-100001")
    assert len({render_line(p, D(200), random.Random(i), messy=False) for i in range(20)}) == 1


# --- every product, many seeds --------------------------------------------------------


def test_every_line_is_valid_for_the_label(catalog: list[Product]) -> None:
    """Whatever comes out must satisfy the validator's quantity rule and tag vocabulary."""
    rng = random.Random(7)
    for p in catalog:
        for i in range(3):
            q = quantity_for(p) * (i + 1)
            line = render_line(p, q, random.Random(rng.random()), ring=p.unit == "MTR" and i == 2)
            assert line.quote and "\n" not in line.text, p.sku
            assert line.quote in line.text
            assert set(line.tags) <= LINE_TAGS, line.tags
            assert quantity_readable(q, line.quote, line.tags, p.pack_size), (p.sku, line)


def test_deterministic(catalog: list[Product]) -> None:
    p = first(catalog, Category.RCD)
    assert renders(p, 30) == renders(p, 30)


def test_messy_gives_variety(catalog: list[Product]) -> None:
    for category in Category:
        p = first(catalog, category)
        assert len(set(renders(p))) >= 8, category


# --- spellings ------------------------------------------------------------------------


def test_cable_spellings() -> None:
    seen = set()
    for i in range(300):
        ctx = _Ctx(rng=random.Random(i), messy=True, english=False, comma=True)
        seen.add(spell_cable_spec("3", "1,5", ctx))
    assert seen == {"3x1,5 mm²", "3x1,5", "3 x 1,5", "3G1,5", "3*1,5mm²", "3x1,5qmm"}


def test_decimal_comma_and_point(catalog: list[Product]) -> None:
    p = get(catalog, "MH-100001")
    lines = [render_line(p, D(200), random.Random(i)) for i in range(300)]
    comma = [x for x in lines if "1,5" in x.quote]
    point = [x for x in lines if "1.5" in x.quote]
    assert comma and point
    assert all("decimal_comma" in x.tags for x in comma)
    assert all("language_mix" in x.tags for x in point)  # English lines use the point


def test_units(catalog: list[Product]) -> None:
    cable = get(catalog, "MH-100002")
    quotes = " | ".join(renders(cable, 300))
    for form in ("200 m ", "200m ", "200 Meter ", ": 200m"):
        assert form in quotes, form

    piece = first(catalog, Category.CIRCUIT_BREAKER)
    quotes = " | ".join(render_line(piece, D(40), random.Random(i)).quote for i in range(300))
    for form in ("40 Stück ", "40 Stk ", "40 St. ", "40Stk ", "40x ", "- 40 Stk", ", 40 Stück"):
        assert form in quotes, form


def test_rings(catalog: list[Product]) -> None:
    cable = get(catalog, "MH-100002")  # 100 m ring
    line = render_line(cable, D(200), random.Random(0), messy=False, ring=True)
    assert line.quote.startswith("2 Ringe à 100 m ")
    assert "unit_rings" in line.tags
    one = render_line(cable, D(100), random.Random(0), messy=False, ring=True)
    assert one.quote.startswith("1 Ring à 100 m ")
    with pytest.raises(ValueError, match="whole number of 100 m rings"):
        render_line(cable, D(150), random.Random(0), ring=True)
    with pytest.raises(ValueError, match="not sold by the metre"):
        render_line(first(catalog, Category.RCD), D(2), random.Random(0), ring=True)


def test_thousands_separator(catalog: list[Product]) -> None:
    cable = get(catalog, "MH-100002")
    quotes = [render_line(cable, D(1000), random.Random(i)).quote for i in range(200)]
    assert any("1.000" in q for q in quotes)
    assert all(quantity_readable(D(1000), q, (), 100) for q in quotes)


def test_rcd_shorthand(catalog: list[Product]) -> None:
    rcd = next(
        p
        for p in catalog
        if p.category is Category.RCD
        and p.attributes["rated_current_a"] == 40
        and p.attributes["residual_current_ma"] == 30
    )
    quotes = [render_line(rcd, D(2), random.Random(i)).quote for i in range(300)]
    assert any("FI 40/0,03" in q for q in quotes)
    assert any("40 A 30 mA" in q for q in quotes)


def test_abbreviations(catalog: list[Product]) -> None:
    for category, short in [
        (Category.CIRCUIT_BREAKER, "LS "),
        (Category.JUNCTION_BOX, "AP-Dose"),
        (Category.CABLE_GLAND, "KV "),
        (Category.BALL_VALVE, "KH "),
    ]:
        lines = [render_line(first(catalog, category), D(5), random.Random(i)) for i in range(200)]
        hits = [x for x in lines if short in x.quote]
        assert hits, category
        assert all("abbreviation" in x.tags for x in hits)


# --- what is (not) stated -------------------------------------------------------------


def test_omitted_specs_do_not_appear(catalog: list[Product]) -> None:
    cable = get(catalog, "MH-100001")  # NYM-J 3x1,5
    for i in range(200):
        no_cores = render_line(cable, D(200), random.Random(i), omit=["cores"]).quote
        assert not re.search(r"3\s*[xX*G]|3-adrig|3 Adern", no_cores), no_cores
        no_cs = render_line(cable, D(200), random.Random(i), omit=["cross_section_mm2"]).quote
        assert not re.search(r"1[.,]5", no_cs), no_cs
        no_type = render_line(cable, D(200), random.Random(i), omit=["type"]).quote
        assert "NYM" not in no_type


def test_manufacturer_and_mpn(catalog: list[Product]) -> None:
    p = first(catalog, Category.SOCKET)
    named = render_line(p, D(10), random.Random(0), manufacturer=True)
    assert p.manufacturer in named.quote
    by_mpn = [render_line(p, D(10), random.Random(i), mpn=True) for i in range(30)]
    assert all(p.mpn in x.quote and "mpn_only" in x.tags for x in by_mpn)
    plain = render_line(p, D(10), random.Random(0), messy=False)
    assert p.manufacturer not in plain.quote


def test_remark_is_outside_the_quote(catalog: list[Product]) -> None:
    p = first(catalog, Category.SWITCH)
    lines = [render_line(p, D(3), random.Random(i)) for i in range(200)]
    with_remark = [x for x in lines if x.text != x.quote]
    assert with_remark
    assert all(x.text.startswith(x.quote + " ") for x in with_remark)


def test_tag_mix_is_plausible(catalog: list[Product]) -> None:
    tags: Counter[str] = Counter()
    for p in catalog[::20]:
        for i in range(5):
            tags.update(render_line(p, quantity_for(p), random.Random(i)).tags)
    for tag in ("typo", "abbreviation", "language_mix", "qty_after_item", "unit_glued"):
        assert tags[tag] > 0, tag


# --- typos ----------------------------------------------------------------------------


def test_typo_never_touches_numbers() -> None:
    text = "40 Stk Leitungsschutzschalter B16 1-polig, 3x1,5 mm²"
    for i in range(500):
        broken = typo(text, random.Random(i))
        assert quote_numbers(broken) == quote_numbers(text)
        assert re.findall(r"\d", broken) == re.findall(r"\d", text)
        assert len(broken) in (len(text), len(text) - 1)


def test_typo_changes_one_word() -> None:
    text = "Abzweigdose AP 100x100 IP65 grau"
    changed = {typo(text, random.Random(i)) for i in range(100)} - {text}
    assert changed
    for t in changed:
        assert t.split()[1:] == text.split()[1:]  # only "Abzweigdose" is long enough


def test_typo_without_long_words() -> None:
    assert typo("LS B16 1p", random.Random(0)) == "LS B16 1p"

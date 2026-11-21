"""Label checks against catalog, customers, order history and text."""

from decimal import Decimal
from typing import Any

import pytest

from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Product
from quotedesk.customers.generator import generate_customers
from quotedesk.customers.models import Customer
from quotedesk.customers.orders import Order, generate_orders
from quotedesk.rfqs.models import Label, LabelLine
from quotedesk.rfqs.validate import quantity_readable, quote_numbers, validate_label

World = tuple[list[Product], list[Customer], list[Order]]

CABLE = "200m NYM-J 3x1,5"
TEXT = f"Hallo,\n\nbitte um Angebot für:\n- {CABLE}\n\nDanke und Gruß\nM. Beispiel\n"


@pytest.fixture(scope="module")
def world() -> World:
    catalog = generate_catalog(seed=42)
    customers, entries = generate_customers(catalog, seed=42)
    return catalog, customers, generate_orders(catalog, customers, entries, seed=42)


def cable_line(**overrides: Any) -> LabelLine:
    data: dict[str, Any] = {
        "source_quote": CABLE,
        "quantity": "200",
        "unit": "MTR",
        "expected_sku": "MH-100002",  # NYM-J 3x1,5, 100 m ring
        "acceptable_skus": ["MH-100001", "MH-100002"],  # 50 m ring fits too
        "expected_route": "auto",
        "tags": ["unit_glued", "decimal_comma", "ring_length_implicit"],
    }
    return LabelLine.model_validate(data | overrides)


def make_label(world: World, lines: list[LabelLine] | None = None, **overrides: Any) -> Label:
    _, customers, _ = world
    data: dict[str, Any] = {
        "rfq_id": "rfq_0001",
        "type": "clean_email",
        "intent": "rfq",
        "channel": "email",
        "customer_id": customers[0].id,
        "files": ["rfq_0001.eml"],
        "lines": lines if lines is not None else [cable_line()],
        "expected_route": "auto",
    }
    data |= overrides
    if "expected_route" not in overrides:
        review = any(line.expected_route == "review" for line in data["lines"])
        if review or data["type"] in {"adversarial", "non_rfq"}:
            data["expected_route"] = "review"
    return Label.model_validate(data)


def problems(world: World, label: Label, text: str = TEXT) -> str:
    catalog, customers, orders = world
    return "\n".join(validate_label(label, text, catalog, customers, orders))


def test_valid(world: World) -> None:
    assert problems(world, make_label(world)) == ""


# --- numbers and quantities ---------------------------------------------------------


def test_quote_numbers() -> None:
    assert quote_numbers("200m NYM-J 3x1,5") == {Decimal(200), Decimal(3), Decimal("1.5")}
    assert quote_numbers("3 x 1.5") == {Decimal(3), Decimal("1.5")}
    assert Decimal(1000) in quote_numbers("1.000 m")


@pytest.mark.parametrize(
    ("quantity", "quote", "tags", "pack", "ok"),
    [
        ("200", "200m NYM-J", [], None, True),
        ("1000", "1.000 m NYM-J", [], None, True),
        ("12.5", "12,5 m", [], None, True),
        ("300", "200m NYM-J", [], None, False),
        ("200", "2 Ringe à 100 m NYM-J", ["unit_rings"], 100, True),  # product of two numbers
        ("200", "2 Ringe à 100 m NYM-J", [], 100, False),  # ...only when tagged
        ("40", "4 VE Klemmen", ["unit_packs"], 10, True),  # packs times pack size
        ("300", "200m NYM-J 3x1,5", [], 100, False),  # 3 x pack 100 is not a quantity
    ],
)
def test_quantity_readable(
    quantity: str, quote: str, tags: list[str], pack: int | None, ok: bool
) -> None:
    assert quantity_readable(Decimal(quantity), quote, tags, pack) is ok


def test_quantity_must_be_readable(world: World) -> None:
    label = make_label(world, [cable_line(quantity="300")])
    assert "quantity 300 cannot be read" in problems(world, label)


# --- catalog ------------------------------------------------------------------------


def test_unknown_sku(world: World) -> None:
    line = cable_line(acceptable_skus=["MH-100002", "MH-999999"])
    assert "MH-999999 is not in the catalog" in problems(world, make_label(world, [line]))


def test_discontinued_is_never_correct(world: World) -> None:
    catalog, _, _ = world
    old = next(p for p in catalog if p.discontinued and p.unit == "MTR")
    line = cable_line(acceptable_skus=["MH-100002", old.sku])
    text = problems(world, make_label(world, [line]))
    assert f"{old.sku} is discontinued" in text


def test_unit_must_fit_product(world: World) -> None:
    line = cable_line(unit="C62")
    assert "unit C62 does not fit MH-100002" in problems(world, make_label(world, [line]))


def test_acceptable_skus_share_a_category(world: World) -> None:
    catalog, _, _ = world
    other = next(p for p in catalog if p.category != "cable" and not p.discontinued)
    line = cable_line(acceptable_skus=["MH-100002", other.sku])
    assert f"{other.sku} is a {other.category}" in problems(world, make_label(world, [line]))


# --- customer -----------------------------------------------------------------------


def test_unknown_customer(world: World) -> None:
    label = make_label(world, customer_id="CU-9999")
    assert "unknown customer CU-9999" in problems(world, label)


def test_customer_required_unless_anonymous_type(world: World) -> None:
    assert "needs a customer_id" in problems(world, make_label(world, customer_id=None))
    spam = make_label(world, [], type="non_rfq", intent="spam", customer_id=None)
    assert problems(world, spam) == ""


# --- text ---------------------------------------------------------------------------


def test_quote_missing_from_text(world: World) -> None:
    text = TEXT.replace(CABLE, "200 m NYM-J 3x1,5")
    assert "is not in the text" in problems(world, make_label(world), text)


def test_quote_appears_twice(world: World) -> None:
    assert "appears 2 times" in problems(world, make_label(world), TEXT + CABLE)


def test_quote_inside_another(world: World) -> None:
    short = cable_line(source_quote="NYM-J 3x1,5", quantity="3", tags=["ring_length_implicit"])
    label = make_label(world, [cable_line(), short])
    assert "'NYM-J 3x1,5' is inside '200m NYM-J 3x1,5'" in problems(world, label)


# --- tags and routes ----------------------------------------------------------------


def test_unknown_tags(world: World) -> None:
    label = make_label(world, [cable_line(tags=["unit_gleud"])], tags=["typoz"])
    text = problems(world, label)
    assert "line 1: unknown tag 'unit_gleud'" in text
    assert "rfq_0001: unknown tag 'typoz'" in text


def test_review_tag_forces_review(world: World) -> None:
    label = make_label(world, [cable_line(tags=["missing_spec"])])
    assert "route must be review because it has a review tag" in problems(world, label)


def test_review_without_reason_is_rejected(world: World) -> None:
    label = make_label(world, [cable_line(expected_route="review")])
    assert "route must be auto because nothing needs review" in problems(world, label)


def test_ambiguous_needs_a_missing_spec(world: World) -> None:
    label = make_label(world, type="ambiguous")
    assert "needs a line with a missing or unclear spec" in problems(world, label)
    line = cable_line(tags=["missing_spec"], expected_route="review")
    assert problems(world, make_label(world, [line], type="ambiguous")) == ""


def test_adversarial_needs_something_adversarial(world: World) -> None:
    label = make_label(world, type="adversarial")
    assert "needs forbidden_actions or an absurd line" in problems(world, label)
    attack = make_label(world, type="adversarial", forbidden_actions=["send_quote"])
    assert problems(world, attack) == ""


def test_unstocked_lines_only_in_not_in_catalog(world: World) -> None:
    quote = "10 Stk Wandleuchte Typ Sonderform"
    unstocked = LabelLine(
        source_quote=quote,
        quantity=Decimal(10),
        unit="C62",
        expected_sku=None,
        substitute_skus=("MH-100002",),
        expected_route="review",
        tags=("not_in_catalog",),
    )
    text = TEXT + quote
    assert "only not_in_catalog" in problems(world, make_label(world, [unstocked]), text)
    ok = make_label(world, [unstocked], type="not_in_catalog")
    assert problems(world, ok, text) == ""
    missing = make_label(world, type="not_in_catalog")
    assert "needs an unstocked line" in problems(world, missing)


# --- history ------------------------------------------------------------------------


def history_line(world: World, discontinued: bool) -> tuple[Label, str]:
    """A "wie letztes Mal" line for a past order item (optionally a now-discontinued one)."""
    catalog, _, orders = world
    by_sku = {p.sku: p for p in catalog}
    order, ol = next(
        (o, ol) for o in orders for ol in o.lines if by_sku[ol.sku].discontinued is discontinued
    )
    product = by_sku[ol.sku]
    sku = product.successor_sku if discontinued else product.sku
    assert sku is not None
    quote = "nochmal die Leitung wie letztes Mal"
    line = LabelLine(
        source_quote=quote,
        quantity=Decimal(ol.quantity),
        unit=product.unit,
        expected_sku=sku,
        acceptable_skus=(sku,),
        expected_route="auto",
        tags=("from_history",),
    )
    return make_label(world, [line], customer_id=order.customer_id), f"Hallo, {quote}. Gruß"


@pytest.mark.parametrize("discontinued", [False, True])
def test_from_history(world: World, discontinued: bool) -> None:
    label, text = history_line(world, discontinued)
    assert problems(world, label, text) == ""


def test_from_history_wrong_sku_or_quantity(world: World) -> None:
    label, text = history_line(world, discontinued=False)
    line = label.lines[0]
    wrong_qty = line.model_copy(update={"quantity": line.quantity + 1})
    msg = problems(world, label.model_copy(update={"lines": (wrong_qty,)}), text)
    assert f"quantity {wrong_qty.quantity} was never ordered" in msg

    _, customers, orders = world
    ordered = {ol.sku for o in orders if o.customer_id == label.customer_id for ol in o.lines}
    never = next(s for s in ("MH-100001", "MH-100002", "MH-100003") if s not in ordered)
    other = label.lines[0].model_copy(
        update={"expected_sku": never, "acceptable_skus": (never,), "unit": "MTR"}
    )
    msg = problems(world, label.model_copy(update={"lines": (other,)}), text)
    assert f"never ordered {never}" in msg


def test_from_history_without_orders(world: World) -> None:
    label, text = history_line(world, discontinued=False)
    catalog, customers, _ = world
    assert "no past orders" in "\n".join(validate_label(label, text, catalog, customers, []))


# --- discontinued by MPN ------------------------------------------------------------


def test_discontinued_mpn_expects_successor(world: World) -> None:
    catalog, _, _ = world
    old = next(p for p in catalog if p.discontinued and p.unit == "MTR")
    assert old.successor_sku is not None
    quote = f"{old.pack_size}m {old.mpn}"
    line = LabelLine(
        source_quote=quote,
        quantity=Decimal(old.pack_size),
        unit=old.unit,
        expected_sku=old.successor_sku,
        acceptable_skus=(old.successor_sku,),
        expected_route="auto",
        tags=("discontinued_requested", "mpn_only"),
    )
    text = f"Bitte um Preis für {quote}."
    assert problems(world, make_label(world, [line]), text) == ""

    # Expecting anything other than the successor is wrong.
    wrong = line.model_copy(update={"expected_sku": "MH-100002", "acceptable_skus": ("MH-100002",)})
    msg = problems(world, make_label(world, [wrong]), text)
    assert f"must be the successor ({old.successor_sku})" in msg

    no_mpn = line.model_copy(update={"source_quote": f"{old.pack_size}m NYM-J"})
    msg = problems(world, make_label(world, [no_mpn]), f"Preis für {old.pack_size}m NYM-J")
    assert "names no discontinued MPN" in msg

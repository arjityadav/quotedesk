"""Check a label against the catalog, the customers, the order history and the rendered text.

`rfqs.models` checks a label on its own. This module checks everything a label claims about
the outside world: the SKUs exist and are sellable, every quoted line is really in the text,
the quantity can be read from its quote, history references resolve, and the route follows
the documented rules. Generators must produce labels that pass; the eval set relies on it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
from itertools import combinations

from quotedesk.catalog.models import Product
from quotedesk.customers.models import Customer
from quotedesk.customers.orders import Order
from quotedesk.rfqs.models import Label, LabelLine, RfqType, Route

# A line with any of these tags needs a human, so it must route to review.
REVIEW_TAGS = frozenset(
    {"missing_spec", "unclear_unit", "conflicting_specs", "absurd_quantity", "price_question"}
)

# What makes a line hard. Error analysis groups failures by these.
LINE_TAGS = REVIEW_TAGS | {
    "from_history",  # "wie letztes Mal": SKU and quantity come from a past order
    "discontinued_requested",  # asked for an old MPN; the successor is correct
    "not_in_catalog",
    "ring_length_implicit",
    "manufacturer_implicit",
    "spec_spelling",  # 3G1,5 / 3*1,5mm² / 3x1,5qmm
    "decimal_comma",
    "unit_glued",  # 200m
    "unit_word",  # 200 Meter, 40 Stück
    "unit_rings",  # 2 Ringe à 100 m
    "unit_packs",  # 4 VE
    "abbreviation",  # LS B16, FI 40/0,03
    "typo",
    "language_mix",
    "qty_after_item",
    "mpn_only",
}

# What makes a whole request hard.
RFQ_TAGS = frozenset(
    {
        "typos",
        "language_mix",
        "bullet_list",
        "comma_list",
        "pasted_table",
        "long_table",
        "multi_page",
        "degraded_scan",
        "prompt_injection",
        "absurd_quantity",
        "held_out_template",
    }
)

# Customer may be unknown only for these types (cold mail, spam, attacks).
ANONYMOUS_OK = frozenset({RfqType.NON_RFQ, RfqType.ADVERSARIAL})

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_THOUSANDS = re.compile(r"^\d{1,3}(?:\.\d{3})+$")  # 1.000 / 12.500 in German


def quote_numbers(quote: str) -> set[Decimal]:
    """All numbers in a quote. "1,5" and "1.5" both read as 1.5; "1.000" also as 1000."""
    numbers: set[Decimal] = set()
    for raw in _NUMBER.findall(quote):
        if _THOUSANDS.fullmatch(raw):
            numbers.add(Decimal(raw.replace(".", "")))
        if raw.count(".") + raw.count(",") <= 1:
            numbers.add(Decimal(raw.replace(",", ".")))
    return numbers


def quantity_readable(
    quantity: Decimal, quote: str, tags: Iterable[str] = (), pack_size: int | None = None
) -> bool:
    """Can the label quantity be read from the quote?

    By default the quantity must be one of the quote's numbers (200m). Multiplying is only
    allowed when the line says so, otherwise "3x1,5" would "contain" 300 or 4.5:
    `unit_rings` allows a product of two numbers (2 Ringe à 100 m), `unit_packs` a number
    times the pack size (4 VE of 10).
    """
    numbers = quote_numbers(quote)
    candidates = set(numbers)
    if "unit_rings" in tags:
        candidates |= {a * b for a, b in combinations(sorted(numbers), 2)}
    if "unit_packs" in tags and pack_size is not None:
        candidates |= {n * pack_size for n in numbers}
    return quantity in candidates


def validate_label(
    label: Label,
    text: str,
    catalog: list[Product],
    customers: list[Customer],
    orders: list[Order],
) -> list[str]:
    """Return every problem with this label (empty = valid). Messages start with the rfq_id."""
    by_sku = {p.sku: p for p in catalog}
    rid = label.rfq_id
    problems: list[str] = []

    problems += _check_customer(label, {c.id for c in customers})
    problems += [f"{rid}: unknown tag {t!r}" for t in label.tags if t not in RFQ_TAGS]
    problems += _check_quotes_in_text(label, text)
    problems += _check_type(label)

    history = [o for o in orders if o.customer_id == label.customer_id]
    for n, line in enumerate(label.lines, start=1):
        where = f"{rid} line {n}"
        problems += [f"{where}: {p}" for p in _check_line(line, by_sku, history)]

    return problems


def _check_customer(label: Label, customer_ids: set[str]) -> list[str]:
    rid = label.rfq_id
    if label.customer_id is None:
        if label.type in ANONYMOUS_OK:
            return []
        return [f"{rid}: a {label.type} request needs a customer_id"]
    if label.customer_id not in customer_ids:
        return [f"{rid}: unknown customer {label.customer_id}"]
    return []


def _check_quotes_in_text(label: Label, text: str) -> list[str]:
    """Every quote occurs exactly once, and no quote hides inside another."""
    rid = label.rfq_id
    problems: list[str] = []
    quotes = [line.source_quote for line in label.lines]
    for q in quotes:
        count = text.count(q)
        if count == 0:
            problems.append(f"{rid}: source_quote {q!r} is not in the text")
        elif count > 1:
            problems.append(f"{rid}: source_quote {q!r} appears {count} times in the text")
    for a, b in combinations(quotes, 2):
        if a in b or b in a:
            inner, outer = (a, b) if a in b else (b, a)
            problems.append(f"{rid}: source_quote {inner!r} is inside {outer!r}")
    return problems


def _check_type(label: Label) -> list[str]:
    """Each special type must actually contain what makes it special."""
    rid = label.rfq_id
    lines = label.lines
    problems: list[str] = []

    def any_tag(tags: Iterable[str]) -> bool:
        wanted = set(tags)
        return any(wanted & set(line.tags) for line in lines)

    if label.type is RfqType.AMBIGUOUS and not any_tag(
        {"missing_spec", "unclear_unit", "conflicting_specs"}
    ):
        problems.append(f"{rid}: an ambiguous request needs a line with a missing or unclear spec")
    if (
        label.type is RfqType.ADVERSARIAL
        and not label.forbidden_actions
        and not any_tag({"absurd_quantity"})
    ):
        problems.append(f"{rid}: an adversarial request needs forbidden_actions or an absurd line")

    unstocked = [line for line in lines if line.expected_sku is None]
    if label.type is RfqType.NOT_IN_CATALOG:
        if not any(line.substitute_skus for line in unstocked):
            problems.append(f"{rid}: a not_in_catalog request needs an unstocked line")
    elif unstocked:
        problems.append(f"{rid}: only not_in_catalog requests may have unstocked lines")
    return problems


def _check_line(line: LabelLine, by_sku: dict[str, Product], history: list[Order]) -> list[str]:
    problems = [f"unknown tag {t!r}" for t in line.tags if t not in LINE_TAGS]

    # Catalog: every SKU exists; correct answers and substitutes are sellable.
    bad_skus: list[str] = []
    for sku in (*line.acceptable_skus, *line.substitute_skus):
        product = by_sku.get(sku)
        if product is None:
            bad_skus.append(f"{sku} is not in the catalog")
        elif product.discontinued:
            bad_skus.append(f"{sku} is discontinued and cannot be a correct answer")
    if bad_skus:
        return problems + bad_skus  # the checks below need valid products

    expected = by_sku[line.expected_sku] if line.expected_sku else None
    if expected is not None:
        if line.unit is not expected.unit:
            problems.append(f"unit {line.unit} does not fit {expected.sku} ({expected.unit})")
        for sku in line.acceptable_skus:
            if by_sku[sku].category is not expected.category:
                problems.append(f"{sku} is a {by_sku[sku].category}, not a {expected.category}")

    # Quantity: readable from the quote, unless it comes from history.
    from_history = "from_history" in line.tags
    if not from_history:
        pack = expected.pack_size if expected else None
        if not quantity_readable(line.quantity, line.source_quote, line.tags, pack):
            problems.append(f"quantity {line.quantity} cannot be read from {line.source_quote!r}")

    if from_history and expected is not None:
        problems += _check_history(line, expected, by_sku, history)
    if "discontinued_requested" in line.tags:
        problems += _check_discontinued(line, by_sku)

    # Route follows the tags, never a free choice.
    review = line.expected_sku is None or bool(REVIEW_TAGS & set(line.tags))
    required = Route.REVIEW if review else Route.AUTO
    if line.expected_route is not required:
        reason = "it has a review tag" if review else "nothing needs review"
        problems.append(f"route must be {required} because {reason}")

    return problems


def _check_history(
    line: LabelLine, expected: Product, by_sku: dict[str, Product], history: list[Order]
) -> list[str]:
    """The SKU (or the item it replaced) was ordered before, in this quantity."""
    if not history:
        return ["from_history, but the customer has no past orders"]
    # Past SKUs that this product stands for: itself, or a discontinued item it succeeds.
    stands_for = {expected.sku} | {
        p.sku for p in by_sku.values() if p.successor_sku == expected.sku
    }
    past = [ol for o in history for ol in o.lines if ol.sku in stands_for]
    if not past:
        return [f"from_history, but the customer never ordered {expected.sku} or its predecessor"]
    if all(Decimal(ol.quantity) != line.quantity for ol in past):
        quantities = sorted({ol.quantity for ol in past})
        return [f"from_history, but quantity {line.quantity} was never ordered ({quantities})"]
    return []


def _check_discontinued(line: LabelLine, by_sku: dict[str, Product]) -> list[str]:
    """The quote names an old MPN, and the label expects that product's successor."""
    old = [p for p in by_sku.values() if p.discontinued and p.mpn in line.source_quote]
    if not old:
        return ["discontinued_requested, but the quote names no discontinued MPN"]
    if line.expected_sku not in {p.successor_sku for p in old}:
        successors = ", ".join(sorted(str(p.successor_sku) for p in old))
        return [f"discontinued_requested, so expected_sku must be the successor ({successors})"]
    return []

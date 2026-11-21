"""Plan one request: who asks, for what, how much, and what a perfect system should answer.

`plan_rfq` decides the facts first (customer, products, quantities, which specs are left
out, history and discontinued references), renders each line, and builds the Label from
those facts. The email or PDF layout around the lines comes later (`rfqs.email`,
`rfqs.tender`); they only arrange `PlannedRfq.lines` and must keep every quote intact.

This module plans the regular types (clean and messy email, tender and scanned PDF). The
special types (not in catalog, ambiguous, adversarial, non-RFQ) build on it in
`rfqs.special`.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from quotedesk.catalog.models import Category, Product, Unit
from quotedesk.customers.models import KIND_CATEGORIES, Customer
from quotedesk.customers.orders import Order, OrderLine, last_order
from quotedesk.rfqs.acceptable import acceptable_skus, spec_of
from quotedesk.rfqs.lines import RenderedLine, render_line
from quotedesk.rfqs.models import Channel, Intent, Label, LabelLine, RfqType, Route

# How many lines each type has: (choices, weights). Most emails are short; tenders are long.
LINE_COUNTS: dict[RfqType, tuple[Sequence[int], Sequence[int]]] = {
    RfqType.CLEAN_EMAIL: ((1, 2, 3, 4, 5), (3, 3, 2, 1, 1)),
    RfqType.MESSY_EMAIL: ((1, 2, 3, 4, 5, 6, 8), (2, 3, 3, 2, 2, 1, 1)),
    RfqType.TENDER_PDF: ((5, 6, 8, 10, 12, 15), (1, 2, 2, 2, 1, 1)),
    RfqType.SCANNED_PDF: ((3, 4, 5, 6, 8, 10), (1, 2, 2, 2, 1, 1)),
}

CHANNEL: dict[RfqType, Channel] = {
    RfqType.CLEAN_EMAIL: Channel.EMAIL,
    RfqType.MESSY_EMAIL: Channel.EMAIL,
    RfqType.TENDER_PDF: Channel.PDF,
    RfqType.SCANNED_PDF: Channel.SCAN,
}
EXTENSION = {Channel.EMAIL: "eml", Channel.PDF: "pdf", Channel.SCAN: "png"}

# Attributes a customer may leave out while the request stays unambiguous: every product
# that fits is a correct answer. Everything else is a hard spec (`REQUIRED_ATTRIBUTES`) or
# changes the product too much (a 600x600 panel is not a 1200x300 panel).
OPTIONAL_SPECS: dict[Category, frozenset[str]] = {
    Category.CABLE_GLAND: frozenset({"colour", "ip_rating"}),
    Category.JUNCTION_BOX: frozenset({"colour"}),
    Category.CONDUIT: frozenset({"colour"}),
    Category.BALL_VALVE: frozenset({"handle"}),
    Category.CIRCUIT_BREAKER: frozenset({"breaking_capacity_ka"}),
}

PIECES = (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50)
TENDER_PIECES = (4, 6, 8, 10, 12, 16, 20, 24, 30, 36, 40, 48, 60, 80, 100, 120)
ROUND_METRES = (25, 50, 75, 150, 200, 250, 300, 400, 500)

# Per-line chances in a messy email.
P_FROM_BASKET = 0.4  # re-use an item the customer bought before
P_HISTORY = 0.12  # "wie letztes Mal"
P_DISCONTINUED = 0.08  # old MPN, successor expected
P_MPN = 0.05
P_OMIT_OPTIONAL = 0.5
P_MANUFACTURER = 0.15
P_RING = 0.25

MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip

MAX_TRIES = 20


@dataclass(frozen=True)
class PlannedRfq:
    label: Label
    customer: Customer
    lines: tuple[RenderedLine, ...]  # same order as label.lines
    categories: tuple[Category, ...]  # category of each line's product, same order


def rfq_rng(seed: int, rfq_id: str) -> random.Random:
    """One generator per request: regenerating rfq_0142 never changes rfq_0143."""
    return random.Random(f"{seed}:{rfq_id}")


class _World:
    """Catalog and history lookups shared by every line of one request."""

    def __init__(self, catalog: list[Product], customer: Customer, orders: list[Order]) -> None:
        self.by_sku = {p.sku: p for p in catalog}
        self.catalog = catalog
        categories = KIND_CATEGORIES[customer.kind]
        self.active = [p for p in catalog if not p.discontinued and p.category in categories]
        self.old = [p for p in catalog if p.discontinued and p.category in categories]
        self.history = sorted(
            (o for o in orders if o.customer_id == customer.id), key=lambda o: (o.order_date, o.id)
        )
        self.last = last_order(orders, customer.id)
        basket = {ol.sku for o in self.history for ol in o.lines}
        self.basket = sorted(s for s in basket if not self.by_sku[s].discontinued)

    def current(self, sku: str) -> Product:
        """The product to sell for a SKU: itself, or its successor if discontinued."""
        p = self.by_sku[sku]
        return self.by_sku[p.successor_sku] if p.successor_sku else p


@dataclass(frozen=True)
class _Planned:
    line: LabelLine
    rendered: RenderedLine
    category: Category


def _quantity(p: Product, rng: random.Random, tender: bool, ring: bool) -> Decimal:
    if p.unit is Unit.METRE:
        if ring or rng.random() < 0.6:
            return Decimal(p.pack_size * rng.choice((1, 2, 3, 4, 5) if not tender else (2, 5, 10)))
        return Decimal(rng.choice(ROUND_METRES) * (4 if tender else 1))
    return Decimal(rng.choice(TENDER_PIECES if tender else PIECES))


def _implicit_tags(skus: Sequence[str], world: _World) -> list[str]:
    products = [world.by_sku[s] for s in skus]
    tags = []
    if len({p.pack_size for p in products}) > 1:
        tags.append("ring_length_implicit")
    if len({p.manufacturer for p in products}) > 1:
        tags.append("manufacturer_implicit")
    return tags


def _line(
    product: Product,
    quantity: Decimal,
    rendered: RenderedLine,
    acceptable: Sequence[str],
    world: _World,
    extra_tags: Sequence[str] = (),
) -> _Planned:
    expected = world.current(product.sku).sku
    implicit = _implicit_tags(acceptable, world) if len(acceptable) > 1 else []
    tags = sorted({*rendered.tags, *implicit, *extra_tags})
    line = LabelLine(
        source_quote=rendered.quote,
        quantity=quantity,
        unit=product.unit,
        expected_sku=expected,
        acceptable_skus=tuple(acceptable),
        expected_route=Route.AUTO,
        tags=tuple(tags),
    )
    return _Planned(line, rendered, product.category)


def _plain_line(
    product: Product, world: _World, rng: random.Random, messy: bool, tender: bool
) -> _Planned:
    """Describe the product by its specs, maybe leaving out optional ones."""
    optional = OPTIONAL_SPECS.get(product.category, frozenset())
    omit = [k for k in sorted(optional) if messy and rng.random() < P_OMIT_OPTIONAL]
    manufacturer = messy and rng.random() < P_MANUFACTURER
    ring = not tender and product.unit is Unit.METRE and rng.random() < P_RING
    quantity = _quantity(product, rng, tender, ring)
    rendered = render_line(
        product,
        quantity,
        rng,
        messy=messy,
        omit=omit,
        manufacturer=manufacturer,
        ring=ring,
        tender=tender,
    )
    spec = spec_of(product, omit, manufacturer=manufacturer, pack_size=ring)
    return _line(product, quantity, rendered, acceptable_skus(world.catalog, spec), world)


def _mpn_line(product: Product, world: _World, rng: random.Random, tender: bool) -> _Planned:
    """Ask by part number. For a discontinued product the successor is expected."""
    quantity = _quantity(product, rng, tender, ring=False)
    rendered = render_line(product, quantity, rng, mpn=True)
    expected = world.current(product.sku).sku
    extra = ["discontinued_requested"] if product.discontinued else []
    return _line(product, quantity, rendered, [expected], world, extra)


def _history_line(ol: OrderLine, order: Order, world: _World, rng: random.Random) -> _Planned:
    """A "wie letztes Mal" line: SKU and quantity come from a past order."""
    product = world.by_sku[ol.sku]
    if order == world.last:
        phrase = rng.choice(("wie letztes Mal", "wie beim letzten Mal", "wie zuletzt"))
    else:
        phrase = f"wie im {MONTHS[order.order_date.month - 1]}"
    quantity = Decimal(ol.quantity)
    rendered = render_line(product, quantity, rng, history=phrase)
    expected = world.current(product.sku).sku
    return _line(product, quantity, rendered, [expected], world)


def _draw_line(
    world: _World, rng: random.Random, rfq_type: RfqType, used: set[str]
) -> _Planned | None:
    messy = rfq_type is RfqType.MESSY_EMAIL
    tender = rfq_type in (RfqType.TENDER_PDF, RfqType.SCANNED_PDF)
    roll = rng.random()

    if messy and world.history and roll < P_HISTORY:
        recent = world.last if world.last and rng.random() < 0.7 else rng.choice(world.history)
        assert recent is not None
        ol = rng.choice(recent.lines)
        if world.current(ol.sku).sku in used:
            return None
        return _history_line(ol, recent, world, rng)
    roll -= P_HISTORY
    if messy and world.old and roll < P_DISCONTINUED:
        return _mpn_line(rng.choice(world.old), world, rng, tender)
    roll -= P_DISCONTINUED
    if messy and roll < P_MPN:
        return _mpn_line(rng.choice(world.active), world, rng, tender)

    if world.basket and rng.random() < P_FROM_BASKET:
        product = world.by_sku[rng.choice(world.basket)]
    else:
        product = rng.choice(world.active)
    return _plain_line(product, world, rng, messy, tender)


def _clashes(new: _Planned, planned: list[_Planned]) -> bool:
    """Two lines must not ask for the same thing or quote each other."""
    q = new.rendered.quote
    for other in planned:
        o = other.rendered.quote
        if q in o or o in q:
            return True
        if set(new.line.acceptable_skus) & set(other.line.acceptable_skus):
            return True
    return False


def plan_rfq(
    rfq_id: str,
    rfq_type: RfqType,
    catalog: list[Product],
    customers: list[Customer],
    orders: list[Order],
    rng: random.Random,
    *,
    customer: Customer | None = None,
) -> PlannedRfq:
    """Plan one regular request (clean or messy email, tender or scanned PDF)."""
    if rfq_type not in LINE_COUNTS:
        raise ValueError(f"{rfq_type} requests are planned in rfqs.special")
    who = customer or rng.choice(customers)
    world = _World(catalog, who, orders)
    choices, weights = LINE_COUNTS[rfq_type]
    wanted = rng.choices(choices, weights)[0]

    planned: list[_Planned] = []
    tries = 0
    while len(planned) < wanted and tries < wanted * MAX_TRIES:
        tries += 1
        used = {s for p in planned for s in p.line.acceptable_skus}
        new = _draw_line(world, rng, rfq_type, used)
        if new is not None and not _clashes(new, planned):
            planned.append(new)
    if not planned:
        raise RuntimeError(f"{rfq_id}: could not plan any line")

    lines = tuple(p.line for p in planned)
    tags: set[str] = set()
    if any("typo" in line.tags for line in lines):
        tags.add("typos")
    if any("language_mix" in line.tags for line in lines):
        tags.add("language_mix")
    if rfq_type is RfqType.TENDER_PDF and len(lines) >= 10:
        tags.add("long_table")
    if rfq_type is RfqType.SCANNED_PDF:
        tags.add("degraded_scan")

    channel = CHANNEL[rfq_type]
    label = Label(
        rfq_id=rfq_id,
        type=rfq_type,
        intent=Intent.RFQ,
        channel=channel,
        customer_id=who.id,
        files=(f"{rfq_id}.{EXTENSION[channel]}",),
        lines=lines,
        expected_route=Route.AUTO,
        tags=tuple(sorted(tags)),
        notes=_notes(lines),
    )
    return PlannedRfq(
        label=label,
        customer=who,
        lines=tuple(p.rendered for p in planned),
        categories=tuple(p.category for p in planned),
    )


def _notes(lines: Sequence[LabelLine]) -> str:
    """Short human-readable hints for whoever reads the label."""
    notes = []
    for n, line in enumerate(lines, start=1):
        if "from_history" in line.tags:
            notes.append(f"line {n}: quantity and item from order history")
        if "discontinued_requested" in line.tags:
            notes.append(f"line {n}: discontinued MPN, successor {line.expected_sku}")
        if len(line.acceptable_skus) > 1:
            notes.append(f"line {n}: {len(line.acceptable_skus)} products fit")
    return "; ".join(notes)

"""Render one requested item as an electrician would type it.

`render_line` turns a product and a quantity into line text such as "200m NYM-J 3x1,5" or
"40 Stk LS B16 1p", and reports which kinds of messiness it used as tags. Only the
attributes the planner decided to state are rendered; everything left out (`omit`) must not
appear in the text, so the label's `acceptable_skus` stay honest.

Facts are never randomised: numbers are rendered exactly, typos only touch letters, and the
quantity always stays readable from the quote (see `rfqs.validate.quantity_readable`).
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from quotedesk.catalog.models import AttrValue, Category, Product, Unit

MESSY_ENGLISH = 0.15  # share of messy lines with English words mixed in
MESSY_TYPO = 0.20
MESSY_REMARK = 0.15

Option = tuple[str, str | None]  # (text, tag it adds)

REMARKS = ("(bitte Lieferzeit angeben)", "– dringend", "(oder gleichwertig)", "– gerne Alternative")


@dataclass(frozen=True)
class RenderedLine:
    text: str  # what goes into the email or PDF
    quote: str  # the part the label points at (`source_quote`)
    tags: tuple[str, ...]


@dataclass
class _Ctx:
    """Random choices and the tags they produce, for one line."""

    rng: random.Random
    messy: bool
    english: bool
    comma: bool  # decimal comma (1,5) or point (1.5); German default is comma
    tags: set[str] = field(default_factory=set)

    def choose(self, options: list[Option]) -> str:
        """Clean mode takes the first option. Messy mode picks one at random.

        Options tagged `language_mix` are used only (and then preferably) for English lines.
        """
        if not self.messy:
            text, tag = options[0]
        else:
            english = [o for o in options if o[1] == "language_mix"]
            german = [o for o in options if o[1] != "language_mix"]
            pool = english if self.english and english else german
            text, tag = self.rng.choice(pool)
        if tag:
            self.tags.add(tag)
        return text

    def num(self, value: AttrValue) -> str:
        """An exact number, with decimal comma or point. Never rounded."""
        if not isinstance(value, Decimal):
            return value
        text = format(value.normalize(), "f")
        if "." in text and self.comma:
            self.tags.add("decimal_comma")
            return text.replace(".", ",")
        return text


def _join(*parts: str | None) -> str:
    return " ".join(p for p in parts if p)


# --- one describer per category ------------------------------------------------------
# Each gets only the stated attributes and returns the item text without quantity.

Attrs = Mapping[str, AttrValue]


def spell_cable_spec(cores: str, cs: str, ctx: _Ctx) -> str:
    """3x1,5 mm² and its real-world spellings."""
    return ctx.choose(
        [
            (f"{cores}x{cs} mm²", None),
            (f"{cores}x{cs}", None),
            (f"{cores} x {cs}", "spec_spelling"),
            (f"{cores}G{cs}", "spec_spelling"),
            (f"{cores}*{cs}mm²", "spec_spelling"),
            (f"{cores}x{cs}qmm", "spec_spelling"),
        ]
    )


def _cable(a: Attrs, ctx: _Ctx) -> str:
    word = None
    if "type" not in a:
        word = ctx.choose([("Mantelleitung", None), ("Leitung", None), ("cable", "language_mix")])
    cores = ctx.num(a["cores"]) if "cores" in a else None
    cs = ctx.num(a["cross_section_mm2"]) if "cross_section_mm2" in a else None
    if cores and cs:
        spec = spell_cable_spec(cores, cs, ctx)
    elif cs:
        spec = ctx.choose([(f"{cs} mm²", None), (f"{cs}qmm", "spec_spelling"), (cs, None)])
    elif cores:
        spec = ctx.choose([(f"{cores}-adrig", None), (f"{cores} Adern", None)])
    else:
        spec = None
    return _join(word, str(a["type"]) if "type" in a else None, spec)


COLOUR_SHORT = {"schwarz": "sw", "blau": "bl", "grün-gelb": "gnge", "braun": "br", "grau": "gr"}


def _wire(a: Attrs, ctx: _Ctx) -> str:
    word = ctx.choose(
        [("Aderleitung", None), ("PVC-Ader", None), ("Einzelader", None), ("wire", "language_mix")]
    )
    typ = str(a["type"]) if "type" in a else None
    cs = None
    if "cross_section_mm2" in a:
        n = ctx.num(a["cross_section_mm2"])
        cs = ctx.choose([(f"{n} mm²", None), (f"{n}mm²", None), (f"{n}qmm", "spec_spelling")])
    colour = None
    if "colour" in a:
        c = str(a["colour"])
        short = COLOUR_SHORT.get(c)
        colour = ctx.choose([(c, None)] + ([(short, "abbreviation")] if short else []))
    keep_word = typ is None or not ctx.messy or ctx.rng.random() < 0.5
    return _join(word if keep_word else None, typ, cs, colour)


def _cable_gland(a: Attrs, ctx: _Ctx) -> str:
    word = ctx.choose(
        [
            ("Kabelverschraubung", None),
            ("KV", "abbreviation"),
            ("Verschraubung", None),
            ("cable gland", "language_mix"),
        ]
    )
    return _join(
        word, *(str(a[k]) for k in ("thread", "material", "colour", "ip_rating") if k in a)
    )


def _junction_box(a: Attrs, ctx: _Ctx) -> str:
    word = ctx.choose(
        [
            ("Abzweigdose AP", None),
            ("AP-Dose", "abbreviation"),
            ("Abzweigkasten AP", None),
            ("junction box", "language_mix"),
        ]
    )
    size = None
    if "size_mm" in a:
        size = ctx.choose([(f"{a['size_mm']} mm", None), (str(a["size_mm"]), None)])
    return _join(word, size, *(str(a[k]) for k in ("ip_rating", "colour") if k in a))


def _poles(n: str, ctx: _Ctx) -> str:
    return ctx.choose(
        [(f"{n}-polig", None), (f"{n}p", "abbreviation"), (f"{n}-pol.", "abbreviation")]
    )


def _circuit_breaker(a: Attrs, ctx: _Ctx) -> str:
    char = str(a["characteristic"]) if "characteristic" in a else ""
    amps = ctx.num(a["rated_current_a"]) if "rated_current_a" in a else ""
    word = ctx.choose(
        [
            ("Leitungsschutzschalter", None),
            ("LS", "abbreviation"),
            ("LS-Schalter", "abbreviation"),
            ("MCB", "language_mix"),
        ]
    )
    if char and amps:
        rating = ctx.choose([(f"{char}{amps}", None), (f"{char} {amps}A", None)])
    elif amps:
        rating = f"{amps}A"
    else:
        rating = char
    poles = _poles(ctx.num(a["poles"]), ctx) if "poles" in a else None
    return _join(word, rating, poles)


def _rcd(a: Attrs, ctx: _Ctx) -> str:
    amps = ctx.num(a["rated_current_a"]) if "rated_current_a" in a else None
    ma = a.get("residual_current_ma")
    poles = _poles(ctx.num(a["poles"]), ctx) if "poles" in a else None
    typ = None
    if "rcd_type" in a:
        typ = ctx.choose(
            [(f"Typ {a['rcd_type']}", None), (f"type {a['rcd_type']}", "language_mix")]
        )
    word = ctx.choose(
        [("FI-Schutzschalter", None), ("FI", "abbreviation"), ("RCD", "language_mix")]
    )
    if amps and isinstance(ma, Decimal) and ctx.messy and ctx.rng.random() < 0.5:
        # Trade shorthand: FI 40/0,03 = 40 A, 30 mA (residual current in amperes).
        ctx.tags.add("abbreviation")
        rating = f"{amps}/{ctx.num(ma / 1000)}"
    else:
        rating = _join(f"{amps} A" if amps else None, f"{ctx.num(ma)} mA" if ma else None)
    return _join(word, rating, poles, typ)


SOCKET_EN = {
    "Schuko-Steckdose": "Schuko socket",
    "Schuko-Steckdose mit Klappdeckel": "Schuko socket with flap",
    "USB-Steckdose": "USB socket",
    "Ausschalter": "on/off switch",
    "Wechselschalter": "two-way switch",
    "Kreuzschalter": "intermediate switch",
    "Serienschalter": "series switch",
    "Taster": "push button",
}


def _wiring_device(generic: str) -> Callable[[Attrs, _Ctx], str]:
    def describe(a: Attrs, ctx: _Ctx) -> str:
        variant = str(a.get("variant", generic))
        name = ctx.choose([(variant, None), (SOCKET_EN.get(variant, variant), "language_mix")])
        series = None
        if "series" in a:
            series = ctx.choose([(f"UP Serie {a['series']}", None), (str(a["series"]), None)])
        return _join(name, series, str(a["colour"]) if "colour" in a else None)

    return describe


def _lighting(a: Attrs, ctx: _Ctx) -> str:
    variant = str(a.get("variant", "LED-Leuchte"))
    name = ctx.choose([(variant, None), (variant.replace("-", " "), None)])
    size = None
    if "size_mm" in a:
        size = ctx.choose([(f"{a['size_mm']} mm", None), (str(a["size_mm"]), None)])
    elif "diameter_mm" in a:
        d = ctx.num(a["diameter_mm"])
        size = ctx.choose([(f"Ø{d} mm", None), (f"{d}mm", None)])
    watts = None
    if "power_w" in a:
        w = ctx.num(a["power_w"])
        watts = ctx.choose([(f"{w} W", None), (f"{w}W", None)])
    kelvin = None
    if "colour_temperature_k" in a:
        k = ctx.num(a["colour_temperature_k"])
        kelvin = ctx.choose([(f"{k} K", None), (f"{k}K", None)])
    return _join(name, size, watts, kelvin, str(a["ip_rating"]) if "ip_rating" in a else None)


def _conduit(a: Attrs, ctx: _Ctx) -> str:
    variant = a.get("variant")
    if variant == "starr":
        word = ctx.choose([("Isolierrohr starr", None), ("Isorohr", "abbreviation")])
    elif variant == "flexibel":
        word = ctx.choose([("Wellrohr flexibel", None), ("Flexrohr", None)])
    else:
        word = ctx.choose([("Elektroinstallationsrohr", None), ("Rohr", None)])
    d = None
    if "diameter_mm" in a:
        n = ctx.num(a["diameter_mm"])
        d = ctx.choose([(f"{n} mm", None), (f"{n}mm", None), (f"M{n}", "abbreviation")])
    return _join(word, d, str(a["colour"]) if "colour" in a else None)


FITTING_EN = {
    "T-Stück": "tee",
    "Winkel 90°": "elbow 90°",
    "Bogen 45°": "bend 45°",
    "Muffe": "coupling",
    "Schiebemuffe": "slip coupling",
    "Endkappe": "end cap",
    "Übergang AG": "adapter male",
    "Übergang IG": "adapter female",
}


def _press_fitting(a: Attrs, ctx: _Ctx) -> str:
    shape = str(a["shape"]) if "shape" in a else None
    english = FITTING_EN.get(shape, "") if shape else ""
    word, shape_text = (
        ("press fitting", english)
        if ctx.choose([("de", None), ("en", "language_mix")]) == "en"
        else (ctx.choose([("Pressfitting", None), ("Press", "abbreviation")]), shape)
    )
    d = None
    if "diameter_mm" in a:
        n = ctx.num(a["diameter_mm"])
        d = ctx.choose([(f"{n} mm", None), (f"{n}mm", None), (n, None)])
    return _join(word, shape_text, d, str(a["material"]) if "material" in a else None)


def _ball_valve(a: Attrs, ctx: _Ctx) -> str:
    word = ctx.choose([("Kugelhahn", None), ("KH", "abbreviation"), ("ball valve", "language_mix")])
    dn = None
    if "dn" in a:
        n = ctx.num(a["dn"])
        dn = ctx.choose([(f"DN{n}", None), (f"DN {n}", None)])
    rest = (str(a[k]) for k in ("connection", "handle", "material") if k in a)
    return _join(word, dn, *rest)


DESCRIBE: dict[Category, Callable[[Attrs, _Ctx], str]] = {
    Category.CABLE: _cable,
    Category.WIRE: _wire,
    Category.CABLE_GLAND: _cable_gland,
    Category.JUNCTION_BOX: _junction_box,
    Category.CIRCUIT_BREAKER: _circuit_breaker,
    Category.RCD: _rcd,
    Category.SOCKET: _wiring_device("Steckdose"),
    Category.SWITCH: _wiring_device("Schalter"),
    Category.LIGHTING: _lighting,
    Category.CONDUIT: _conduit,
    Category.PRESS_FITTING: _press_fitting,
    Category.BALL_VALVE: _ball_valve,
}

# --- typos ----------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-zÄÖÜäöüß]{6,}")


def typo(text: str, rng: random.Random) -> str:
    """Swap or drop one letter inside one long word. Digits and codes are never touched."""
    words = [m for m in _WORD.finditer(text)]
    if not words:
        return text
    m = rng.choice(words)
    w = m.group()
    i = rng.randrange(1, len(w) - 2)  # keep first and last letter
    if rng.random() < 0.5 and w[i] != w[i + 1]:
        broken = w[:i] + w[i + 1] + w[i] + w[i + 2 :]
    else:
        broken = w[:i] + w[i + 1 :]
    return text[: m.start()] + broken + text[m.end() :]


# --- quantity -------------------------------------------------------------------------


def _quantity_number(q: Decimal, ctx: _Ctx) -> str:
    german = ctx.comma
    if german and q == q.to_integral_value() and q >= 1000 and ctx.messy and ctx.rng.random() < 0.5:
        return f"{int(q):,}".replace(",", ".")  # 1.000
    return ctx.num(q)


def _with_quantity(desc: str, q: Decimal, unit: Unit, pack: int, ring: bool, ctx: _Ctx) -> str:
    n = _quantity_number(q, ctx)
    if unit is Unit.METRE and ring:
        rings = q / pack
        if rings != rings.to_integral_value() or rings < 1:
            raise ValueError(f"{q} m is not a whole number of {pack} m rings")
        r = int(rings)
        ctx.tags.add("unit_rings")
        word = "Ring" if r == 1 else "Ringe"
        return ctx.choose(
            [
                (f"{r} {word} à {pack} m {desc}", None),
                (f"{r}x{pack}m {desc}", None),
                (f"{desc} {r} {word} à {pack}m", "qty_after_item"),
            ]
        )
    if unit is Unit.METRE:
        return ctx.choose(
            [
                (f"{n} m {desc}", None),
                (f"{n}m {desc}", "unit_glued"),
                (f"{n} Meter {desc}", "unit_word"),
                (f"{desc}: {n}m", "qty_after_item"),
                (f"{n} meters {desc}", "language_mix"),
            ]
        )
    if q != q.to_integral_value():
        raise ValueError(f"{q} is not a whole number of pieces")
    return ctx.choose(
        [
            (f"{n} Stück {desc}", "unit_word"),
            (f"{n} Stk {desc}", None),
            (f"{n} St. {desc}", None),
            (f"{n}Stk {desc}", "unit_glued"),
            (f"{n}x {desc}", None),
            (f"{desc} - {n} Stk", "qty_after_item"),
            (f"{desc}, {n} Stück", "qty_after_item"),
            (f"{n} pcs {desc}", "language_mix"),
        ]
    )


# --- the entry point ------------------------------------------------------------------


def render_line(
    product: Product,
    quantity: Decimal,
    rng: random.Random,
    *,
    messy: bool = True,
    omit: Iterable[str] = (),
    manufacturer: bool = False,
    ring: bool = False,
    mpn: bool = False,
    history: str | None = None,
) -> RenderedLine:
    """Render one line item.

    messy:        random spellings, abbreviations, typos, English words; else the clean form
    omit:         attributes the customer leaves out (must not appear in the text)
    manufacturer: name the manufacturer
    ring:         state the ring length as "2 Ringe à 100 m" (cables only)
    mpn:          ask by manufacturer part number instead of describing the item
    history:      refer to a past order instead of a quantity, e.g. "wie letztes Mal"
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if ring and product.unit is not Unit.METRE:
        raise ValueError(f"{product.sku} is not sold by the metre")
    left_out = set(omit)
    english = messy and rng.random() < MESSY_ENGLISH
    ctx = _Ctx(rng=rng, messy=messy, english=english, comma=not english)

    if mpn:
        ctx.tags.add("mpn_only")
        desc = ctx.choose(
            [
                (f"{product.manufacturer} {product.mpn}", None),
                (f"Art.-Nr. {product.mpn}", None),
                (product.mpn, None),
            ]
        )
    else:
        stated = {k: v for k, v in product.attributes.items() if k not in left_out}
        desc = DESCRIBE[product.category](stated, ctx)
        if messy and rng.random() < MESSY_TYPO:
            broken = typo(desc, rng)
            if broken != desc:
                ctx.tags.add("typo")
                desc = broken
        if manufacturer:
            desc = ctx.choose(
                [
                    (f"{desc} {product.manufacturer}", None),
                    (f"{desc} ({product.manufacturer})", None),
                ]
            )

    if history is not None:
        ctx.tags.add("from_history")
        quote = ctx.choose(
            [
                (f"{desc}, Menge {history}", None),
                (f"nochmal {desc} {history}", None),
                (f"{desc} – gleiche Menge {history}", None),
            ]
        )
    else:
        quote = _with_quantity(desc, quantity, product.unit, product.pack_size, ring, ctx)
    text = quote
    if messy and rng.random() < MESSY_REMARK:
        text = f"{quote} {rng.choice(REMARKS)}"
    if english:
        ctx.tags.add("language_mix")
    return RenderedLine(text=text, quote=quote, tags=tuple(sorted(ctx.tags)))

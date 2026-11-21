"""Render a tender (Leistungsverzeichnis, GAEB style) as PDF with Typst.

A German tender lists numbered positions (OZ "01.02.0030") grouped under titles, each with
quantity, unit, a short text, a long text and blanks for unit and total price. The planner
already rendered each position's quote as "20,000 St <short text>"; here the positions are
laid out so the PDF text layer reads `OZ quantity unit short-text` on one line. The long
text and price blanks go below, so they never split a quote.

Typst runs in-process (the `typst` package bundles the compiler and fonts). Hyphenation
and ligatures are off so the text layer keeps every word intact. Output is byte-for-byte
reproducible.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pypdfium2 as pdfium
import typst

from quotedesk.catalog.models import Category
from quotedesk.rfqs.lines import RenderedLine
from quotedesk.rfqs.models import Label, LabelLine
from quotedesk.rfqs.planner import PlannedRfq

FIRST_DUE = date(2026, 12, 7)
STREETS = ("Lindenstraße", "Schillerstraße", "Am Klosterhof", "Parkallee", "Industriestraße")
BUILDINGS = ("Neubau Kita", "Sanierung Grundschule", "Neubau MFH", "Umbau Rathaus",
             "Erweiterung Pflegeheim", "Neubau Feuerwache")  # fmt: skip

TITLES: dict[Category, str] = {
    Category.CABLE: "Kabel und Leitungen",
    Category.WIRE: "Kabel und Leitungen",
    Category.CONDUIT: "Verlegesysteme",
    Category.CABLE_GLAND: "Verlegesysteme",
    Category.JUNCTION_BOX: "Verlegesysteme",
    Category.CIRCUIT_BREAKER: "Unterverteilung",
    Category.RCD: "Unterverteilung",
    Category.SOCKET: "Installationsgeräte",
    Category.SWITCH: "Installationsgeräte",
    Category.LIGHTING: "Beleuchtung",
    Category.PRESS_FITTING: "Rohrleitungen und Formteile",
    Category.BALL_VALVE: "Armaturen",
}
TITLE_ORDER = list(dict.fromkeys(TITLES.values()))

# Long texts say how, never what: no specs, so a quote can't appear twice.
LONG_TEXTS: dict[str, tuple[str, ...]] = {
    "Kabel und Leitungen": (
        "Liefern und verlegen, einschließlich Befestigungsmaterial, Abrechnung nach Aufmaß.",
        "Liefern, in vorhandene Verlegesysteme einziehen und beidseitig anschließen.",
    ),
    "Verlegesysteme": (
        "Liefern und montieren, einschließlich Kleinmaterial und Befestigung.",
        "Liefern und fachgerecht setzen, Ausführung nach Angabe der Bauleitung.",
    ),
    "Unterverteilung": (
        "Liefern, auf Hutschiene montieren und verdrahten, Beschriftung nach Plan.",
        "Liefern und in vorhandene Verteilung einbauen, einschließlich Prüfung.",
    ),
    "Installationsgeräte": (
        "Liefern und in vorhandene Unterputzdose montieren, komplett mit Rahmen.",
        "Liefern, montieren und anschließen, Schalterprogramm wie beschrieben.",
    ),
    "Beleuchtung": (
        "Liefern, montieren und anschließen, einschließlich Betriebsgerät.",
        "Liefern und in Decke einbauen, Leuchtmittel im Lieferumfang.",
    ),
    "Rohrleitungen und Formteile": (
        "Liefern und mit Pressverbindung montieren, Systemzulassung beachten.",
        "Liefern und einbauen, einschließlich Dichtheitsprüfung.",
    ),
    "Armaturen": (
        "Liefern und in Rohrleitung einbauen, einschließlich Dichtmittel.",
        "Liefern und montieren, Funktionsprüfung nach Einbau.",
    ),
}

PREAMBLES = (
    "Vorbemerkung: Alle Preise sind netto ohne Umsatzsteuer anzugeben. Gleichwertige "
    "Fabrikate sind zulässig, sofern sie im Angebot benannt werden.",
    "Vorbemerkung: Die Mengen sind geschätzt, abgerechnet wird nach Aufmaß. Angebote "
    "gleichwertiger Produkte sind mit Datenblatt einzureichen.",
)
HOLDOUT_PREAMBLES = (
    "Hinweis an den Bieter: Einheitspreise verstehen sich frei Baustelle. Abweichende "
    "Fabrikate bitte gesondert kennzeichnen.",
)


@dataclass(frozen=True)
class RenderedTender:
    label: Label  # lines reordered to PDF order, layout tags added
    source: str  # the Typst source, kept for debugging
    pdf: bytes


@dataclass(frozen=True)
class _Position:
    oz: str
    rendered: RenderedLine
    line: LabelLine
    long_text: str


def typst_str(text: str) -> str:
    """A Typst string literal. Code mode keeps *, _, #, / and friends literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _split_quote(quote: str) -> tuple[str, str, str]:
    """'20,000 St Kurztext' -> ('20,000', 'St', 'Kurztext')."""
    quantity, unit, short = quote.split(" ", 2)
    return quantity, unit, short


def _position_block(p: _Position) -> str:
    quantity, unit, short = _split_quote(p.rendered.quote)
    cells = ", ".join(
        [typst_str(p.oz), f"align(right, {typst_str(quantity)})", typst_str(unit), typst_str(short)]
    )
    return (
        "#block(breakable: false, below: 1.1em)[\n"
        f"  #grid(columns: (2.3cm, 2cm, 0.9cm, 1fr), column-gutter: 6pt, {cells})\n"
        f"  #pad(left: 5.2cm + 18pt, text(size: 8pt, {typst_str(p.long_text)}))\n"
        '  #align(right, text(size: 8pt, "EP ____________ €    GP ____________ €"))\n'
        "]\n"
    )


def render_tender(
    planned: PlannedRfq, rng: random.Random, *, holdout: bool = False
) -> RenderedTender:
    """Lay the planned positions out as a GAEB-style tender and compile it to PDF."""
    label, customer = planned.label, planned.customer
    by_title: dict[str, list[tuple[RenderedLine, LabelLine]]] = {}
    for rendered, line, category in zip(
        planned.lines, label.lines, planned.categories, strict=True
    ):
        by_title.setdefault(TITLES[category], []).append((rendered, line))

    lot = rng.randint(1, 3)
    titles: list[tuple[str, list[_Position]]] = []
    for t_no, title in enumerate((t for t in TITLE_ORDER if t in by_title), start=1):
        items = [
            _Position(
                oz=f"{lot:02d}.{t_no:02d}.{n * 10:04d}",
                rendered=rendered,
                line=line,
                long_text=rng.choice(LONG_TEXTS[title]),
            )
            for n, (rendered, line) in enumerate(by_title[title], start=1)
        ]
        titles.append((f"{lot:02d}.{t_no:02d} {title}", items))

    building = f"{rng.choice(BUILDINGS)}, {rng.choice(STREETS)} {rng.randint(1, 80)}"
    number = f"VG-2026-{rng.randint(100, 999)}"
    due = FIRST_DUE + timedelta(days=rng.randrange(0, 28))
    preamble = rng.choice(HOLDOUT_PREAMBLES if holdout else PREAMBLES)
    source = _document(titles, building, number, due, customer.name, customer.city, preamble)
    pdf = typst.compile(source.encode("utf-8"))
    assert isinstance(pdf, bytes)

    ordered = tuple(p.line for _, items in titles for p in items)
    tags = set(label.tags)
    if _pages(pdf) > 1:
        tags.add("multi_page")
    if holdout:
        tags.add("held_out_template")
    new_label = label.model_copy(update={"lines": ordered, "tags": tuple(sorted(tags))})
    return RenderedTender(label=new_label, source=source, pdf=pdf)


def _document(
    titles: Sequence[tuple[str, Sequence[_Position]]],
    building: str,
    number: str,
    due: date,
    client: str,
    city: str,
    preamble: str,
) -> str:
    header = typst_str(f"Leistungsverzeichnis {number}")
    footer = typst_str(f"{number} · {building} · Seite ")
    out = [
        "#set document(title: " + header + ", date: none)\n",
        '#set text(font: "Libertinus Serif", lang: "de", size: 9.5pt, hyphenate: false,'
        " ligatures: false)\n",
        "#set par(justify: false)\n",
        '#set page(paper: "a4", margin: (x: 2cm, y: 2.2cm),\n'
        f"  header: align(right, text(size: 8pt, {header})),\n"
        f"  footer: context align(center, text(size: 8pt, {footer} + "
        'counter(page).display() + " von " + str(counter(page).final().first()))))\n',
        f'#text(size: 16pt, weight: "bold", {typst_str("Leistungsverzeichnis")})\n\n',
        "#grid(columns: (3.5cm, 1fr), row-gutter: 4pt,\n",
        f"  {typst_str('Bauvorhaben:')}, {typst_str(building)},\n",
        f"  {typst_str('Auftraggeber:')}, {typst_str(f'{client}, {city}')},\n",
        f"  {typst_str('Vergabe-Nr.:')}, {typst_str(number)},\n",
        f"  {typst_str('Angebotsabgabe bis:')}, {typst_str(due.strftime('%d.%m.%Y'))},\n",
        ")\n\n",
        f"#text(size: 8.5pt, {typst_str(preamble)})\n\n",
        "#grid(columns: (2.3cm, 2cm, 0.9cm, 1fr), column-gutter: 6pt,"
        ' text(weight: "bold", "OZ"), align(right, text(weight: "bold", "Menge")),'
        ' text(weight: "bold", "ME"), text(weight: "bold", "Kurztext / Langtext"))\n',
        "#line(length: 100%, stroke: 0.5pt)\n\n",
    ]
    for title, items in titles:
        out.append(f'#block(above: 1.4em, text(weight: "bold", {typst_str("Titel " + title)}))\n\n')
        out += [_position_block(p) for p in items]
        out.append(
            f'#align(right, text(weight: "bold", {typst_str("Summe Titel " + title)}'
            ' + "   ____________ €"))\n\n'
        )
    out.append("#line(length: 100%, stroke: 0.5pt)\n")
    out.append('#align(right, text(weight: "bold", "Angebotssumme netto   ____________ €"))\n')
    return "".join(out)


def _pages(pdf: bytes) -> int:
    return len(pdfium.PdfDocument(pdf))


def pdf_text(pdf: bytes) -> str:
    """The PDF's text layer, page by page, as the validator and the extractor see it."""
    doc = pdfium.PdfDocument(pdf)
    try:
        pages = [page.get_textpage().get_text_range() for page in doc]
    finally:
        doc.close()
    return "\n".join(pages).replace("\r\n", "\n")

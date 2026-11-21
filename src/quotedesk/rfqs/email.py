"""Wrap planned line items in a believable customer email and write it as .eml.

The line items come from the planner and are pasted in unchanged; this module only adds
what surrounds them: subject, greeting, context, a layout (bullets, numbers, a comma list,
a table pasted from Excel), delivery wishes, a closing and a signature.

Some phrasing templates are reserved for the holdout split (`holdout=True`) and never
appear elsewhere, so the holdout score shows how the system copes with wording it has
not been tuned on.

The body is written as 8-bit UTF-8 so a .eml stays readable and byte-for-byte
reproducible. Use `read_body` to get the text back for validation.
"""

from __future__ import annotations

import random
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr

from quotedesk.customers.models import Customer
from quotedesk.rfqs.models import Label
from quotedesk.rfqs.planner import PlannedRfq

SALES_ADDRESS = ("Musterhandel GmbH Vertrieb", "angebote@musterhandel.example")
FIRST_DATE = datetime(2026, 11, 2, tzinfo=timezone(timedelta(hours=1)))  # after the history
DATE_SPAN_DAYS = 56

FIRST_NAMES = (
    "Jan", "Sabine", "Thomas", "Aylin", "Markus", "Katrin", "Stefan", "Mehmet", "Julia",
    "Andreas", "Petra", "Lukas", "Nina", "Frank", "Olga", "Tobias", "Ines", "Dariusz",
)  # fmt: skip
LAST_NAMES = (
    "Becker", "Schulte", "Krämer", "Demir", "Vogt", "Lorenz", "Hartmann", "Nowak", "Seidel",
    "Brandl", "Kühn", "Aksoy", "Wendt", "Fuchs", "Engel", "Roth", "Haas", "Baumann",
)  # fmt: skip
STREETS = (
    "Lindenstraße", "Am Mühlbach", "Hauptstraße", "Gartenweg", "Schulstraße", "Ringstraße",
    "Kirchplatz", "Bahnhofstraße", "Am Sportplatz", "Feldstraße",
)  # fmt: skip
# Dialling codes of the customers' cities, so signatures look local.
AREA_CODES = {
    "Hamburg": "040", "Bremen": "0421", "Stuttgart": "0711", "Köln": "0221",
    "Freiburg im Breisgau": "0761", "Hannover": "0511", "Berlin": "030", "Kiel": "0431",
    "Frankfurt am Main": "069", "München": "089", "Leipzig": "0341", "Dortmund": "0231",
    "Augsburg": "0821", "Rostock": "0381", "Würzburg": "0931",
}  # fmt: skip
PROJECTS = ("EFH", "MFH", "Kita", "Praxis", "Bürogebäude", "Schule", "Sanierung", "Neubau")


@dataclass(frozen=True)
class Bank:
    """Phrasing templates for one part of an email. Placeholders: {project}, {name}, {city}."""

    subjects: tuple[str, ...]
    greetings: tuple[str, ...]
    openers: tuple[str, ...]
    wishes: tuple[str, ...]
    closings: tuple[str, ...]


REGULAR = Bank(
    subjects=(
        "Anfrage {project}",
        "Angebotsanfrage",
        "Preisanfrage {project}",
        "Material für {project}",
        "Bitte um Angebot",
        "AW: Anfrage",
    ),
    greetings=(
        "Sehr geehrte Damen und Herren,",
        "Guten Tag,",
        "Hallo zusammen,",
        "Hallo,",
        "Hallo Frau Schneider,",
        "Guten Morgen,",
    ),
    openers=(
        "bitte schicken Sie uns ein Angebot über folgende Positionen:",
        "für unser Bauvorhaben {project} benötigen wir:",
        "wir bitten um Ihr Angebot für:",
        "können Sie mir bitte Preise für folgendes Material schicken?",
        "für die Baustelle {project} brauchen wir kurzfristig:",
        "anbei unsere Anfrage:",
    ),
    wishes=(
        "Lieferung bitte direkt auf die Baustelle.",
        "Wir holen selbst ab.",
        "Bitte mit Lieferzeit.",
        "Wir bräuchten das Material bis Ende nächster Woche.",
        "",
        "",
    ),
    closings=(
        "Mit freundlichen Grüßen",
        "Viele Grüße",
        "Danke und Gruß",
        "Beste Grüße",
        "Gruß",
    ),
)

# Reserved for the holdout split only. No phrase here appears in REGULAR.
HOLDOUT = Bank(
    subjects=("Bedarf {project}", "Kurze Preisabfrage", "Materialliste {project}"),
    greetings=("Moin,", "Servus,", "Grüß Gott,"),
    openers=(
        "folgendes bräuchten wir als Angebot:",
        "anbei die Liste für {project}, bitte Preis machen:",
        "wir hätten Bedarf an:",
    ),
    wishes=("Anlieferung gerne vormittags.", "Teillieferung ist ok.", ""),
    closings=("Schönen Gruß", "Danke vorab und viele Grüße", "Bis dann"),
)

# English bits for requests already tagged `language_mix`.
ENGLISH = Bank(
    subjects=("Request for quotation", "RFQ {project}"),
    greetings=("Hello,", "Hi,"),
    openers=("please send us a quote for:", "we need the following for {project}:"),
    wishes=("Delivery to site please.", ""),
    closings=("Best regards", "Thanks and regards"),
)

MESSY_SIGN_OFFS = ("Von unterwegs gesendet", "Gesendet von meinem Handy")


@dataclass(frozen=True)
class RenderedEmail:
    label: Label  # the planner's label plus layout tags
    body: str
    raw: bytes  # the .eml file


_GERMAN = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "ı": "i"}
)


def domain_of(company: str) -> str:
    """'Elektro Brandt GmbH' -> 'elektro-brandt.example' (reserved TLD, never resolves)."""
    text = company.translate(_GERMAN)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"\b(GmbH|KG|Co|Sohn)\b", " ", text)
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return f"{slug}.example"


def _layout(texts: Sequence[str], rng: random.Random, messy: bool) -> tuple[str, str | None]:
    """Arrange the line texts. Returns the block and the request tag it earns (if any)."""
    styles = ["dash", "numbered"]
    if messy:
        styles += ["plain", "bullet", "table"]
        if len(texts) <= 4:
            styles.append("comma")
    style = rng.choice(styles)
    if style == "dash":
        return "\n".join(f"- {t}" for t in texts), "bullet_list"
    if style == "bullet":
        return "\n".join(f"• {t}" for t in texts), "bullet_list"
    if style == "numbered":
        return "\n".join(f"{n}. {t}" for n, t in enumerate(texts, start=1)), "bullet_list"
    if style == "table":
        rows = ["Pos.\tArtikel / Menge"] + [f"{n}\t{t}" for n, t in enumerate(texts, start=1)]
        return "\n".join(rows), "pasted_table"
    if style == "comma":
        joined = texts[0] if len(texts) == 1 else ", ".join(texts[:-1]) + " und " + texts[-1]
        return f"{joined}.", "comma_list"
    return "\n".join(texts), None


def _signature(customer: Customer, first: str, last: str, rng: random.Random) -> str:
    area = AREA_CODES.get(customer.city, "0" + customer.postcode[1:4])
    phone = f"{area} {rng.randint(100000, 999999)}"
    return "\n".join([f"{first} {last}", customer.name, f"{customer.postcode} {customer.city}",
                      f"Tel. {phone}"])  # fmt: skip


def render_email(
    planned: PlannedRfq, rng: random.Random, *, holdout: bool = False
) -> RenderedEmail:
    """Build the email around the planned lines."""
    label, customer = planned.label, planned.customer
    messy = label.type.value != "clean_email"
    english = "language_mix" in label.tags and rng.random() < 0.5
    bank = ENGLISH if english else HOLDOUT if holdout else REGULAR

    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    fill = {
        "project": f"{rng.choice(PROJECTS)} {rng.choice(STREETS)} {rng.randint(1, 60)}",
        "name": last,
        "city": customer.city,
    }

    block, layout_tag = _layout([line.text for line in planned.lines], rng, messy)
    greeting = rng.choice(bank.greetings)
    opener = rng.choice(bank.openers).format(**fill)
    wish = rng.choice(bank.wishes)
    closing = rng.choice(bank.closings)
    if messy and rng.random() < 0.3:
        greeting = greeting.lower()
    parts = [greeting, "", opener, "", block, ""]
    if wish:
        parts += [wish, ""]
    parts += [closing, _signature(customer, first, last, rng)]
    if messy and rng.random() < 0.15:
        parts += ["", rng.choice(MESSY_SIGN_OFFS)]
    body = "\n".join(parts) + "\n"

    tags = set(label.tags)
    if layout_tag:
        tags.add(layout_tag)
    if holdout and not english:
        tags.add("held_out_template")
    new_label = label.model_copy(update={"tags": tuple(sorted(tags))})

    workdays = [d for d in range(DATE_SPAN_DAYS) if (FIRST_DATE + timedelta(days=d)).weekday() < 5]
    sent = FIRST_DATE + timedelta(
        days=rng.choice(workdays), hours=rng.randint(7, 17), minutes=rng.randrange(60)
    )
    domain = domain_of(customer.name)
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = formataddr((f"{first} {last}", f"{first.lower()}.{_ascii(last)}@{domain}"))
    msg["To"] = formataddr(SALES_ADDRESS)
    msg["Subject"] = rng.choice(bank.subjects).format(**fill)
    msg["Date"] = format_datetime(sent)
    msg["Message-ID"] = f"<{label.rfq_id}.{sent:%Y%m%d%H%M}@{domain}>"
    msg.set_content(body, cte="8bit")
    return RenderedEmail(label=new_label, body=body, raw=msg.as_bytes())


def _ascii(name: str) -> str:
    return domain_of(name).removesuffix(".example")


def read_body(raw: bytes) -> str:
    """The text body of an .eml file, as the validator and the extractor see it."""
    msg = message_from_bytes(raw, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    part = msg.get_body(preferencelist=("plain",))
    if part is None:
        raise ValueError("email has no text/plain body")
    assert isinstance(part, EmailMessage)
    content = part.get_content()
    assert isinstance(content, str)
    return content.replace("\r\n", "\n")

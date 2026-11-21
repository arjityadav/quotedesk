"""Labels for synthetic requests for quotes (RFQs).

A Label is the answer key for one incoming request: what a perfect system would extract,
match and route. Labels are planned first and the text is rendered from them, so the label
cannot disagree with the text. Rules that only need the label itself live here; rules that
need the catalog, the rendered text or the order history live in `rfqs.validate`.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quotedesk.catalog.models import SKU_PATTERN, Unit

RFQ_ID_PATTERN = re.compile(r"^rfq_\d{4}$")
CUSTOMER_ID_PATTERN = re.compile(r"^CU-\d{4}$")


class RfqType(StrEnum):
    CLEAN_EMAIL = "clean_email"
    MESSY_EMAIL = "messy_email"
    TENDER_PDF = "tender_pdf"
    SCANNED_PDF = "scanned_pdf"
    NOT_IN_CATALOG = "not_in_catalog"
    AMBIGUOUS = "ambiguous"
    ADVERSARIAL = "adversarial"
    NON_RFQ = "non_rfq"


class Intent(StrEnum):
    RFQ = "rfq"
    ORDER = "order"
    QUESTION = "question"
    COMPLAINT = "complaint"
    SPAM = "spam"


class Route(StrEnum):
    AUTO = "auto"  # a perfect system may approve without a human
    REVIEW = "review"  # a human must look


class Channel(StrEnum):
    EMAIL = "email"
    PDF = "pdf"
    SCAN = "scan"


# Types whose channel is fixed. The special types can arrive on any channel.
TYPE_CHANNEL: dict[RfqType, Channel] = {
    RfqType.CLEAN_EMAIL: Channel.EMAIL,
    RfqType.MESSY_EMAIL: Channel.EMAIL,
    RfqType.TENDER_PDF: Channel.PDF,
    RfqType.SCANNED_PDF: Channel.SCAN,
    RfqType.NON_RFQ: Channel.EMAIL,
}

# Types that always need a human, whatever their lines say.
ALWAYS_REVIEW = frozenset({RfqType.ADVERSARIAL, RfqType.NON_RFQ})

_CONFIG = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")


class LabelLine(BaseModel):
    """One requested item, as a perfect system would read it."""

    model_config = _CONFIG

    source_quote: str = Field(min_length=1)  # exact text of this line in the email or PDF
    quantity: Decimal = Field(gt=0)  # in the base unit: "2 Ringe à 100 m" -> 200 MTR
    unit: Unit
    expected_sku: str | None  # None: we don't stock it
    acceptable_skus: tuple[str, ...] = ()
    substitute_skus: tuple[str, ...] = ()  # only for items we don't stock
    expected_route: Route
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_rules(self) -> LabelLine:
        """SKU format, expected within acceptable, rules for items not in the catalog."""
        quote = self.source_quote
        for sku in (self.expected_sku, *self.acceptable_skus, *self.substitute_skus):
            if sku is not None and not SKU_PATTERN.fullmatch(sku):
                raise ValueError(f"{quote!r}: sku {sku!r} must look like MH-123456")

        if len(set(self.acceptable_skus)) != len(self.acceptable_skus):
            raise ValueError(f"{quote!r}: acceptable_skus contains duplicates")

        if self.expected_sku is None:
            if self.acceptable_skus:
                raise ValueError(f"{quote!r}: no expected_sku, so acceptable_skus must be empty")
            if self.expected_route is not Route.REVIEW:
                raise ValueError(f"{quote!r}: an item not in the catalog must route to review")
        else:
            if self.expected_sku not in self.acceptable_skus:
                raise ValueError(
                    f"{quote!r}: expected_sku {self.expected_sku} is not in acceptable_skus"
                )
            if self.substitute_skus:
                raise ValueError(f"{quote!r}: substitute_skus only when there is no expected_sku")

        return self


class Label(BaseModel):
    """The answer key for one incoming request."""

    model_config = _CONFIG

    rfq_id: str
    type: RfqType
    intent: Intent
    channel: Channel
    customer_id: str | None  # None: unknown sender
    files: tuple[str, ...] = Field(min_length=1)
    lines: tuple[LabelLine, ...] = ()
    expected_route: Route
    tags: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()  # e.g. "send_quote", for prompt injection
    notes: str = ""

    @model_validator(mode="after")
    def check_rules(self) -> Label:
        """Ids, type/intent/channel consistency, lines, RFQ route = worst line route."""
        rid = self.rfq_id
        if not RFQ_ID_PATTERN.fullmatch(rid):
            raise ValueError(f"rfq_id {rid!r} must look like rfq_0001")
        if self.customer_id is not None and not CUSTOMER_ID_PATTERN.fullmatch(self.customer_id):
            raise ValueError(f"{rid}: customer_id {self.customer_id!r} must look like CU-0001")
        for name in self.files:
            if not name.startswith(f"{rid}."):
                raise ValueError(f"{rid}: file {name!r} must be named {rid}.<ext>")

        is_rfq = self.type is not RfqType.NON_RFQ
        if is_rfq != (self.intent is Intent.RFQ):
            raise ValueError(f"{rid}: type {self.type} does not fit intent {self.intent}")
        expected_channel = TYPE_CHANNEL.get(self.type)
        if expected_channel is not None and self.channel is not expected_channel:
            raise ValueError(f"{rid}: type {self.type} must arrive by {expected_channel}")

        if is_rfq and not self.lines:
            raise ValueError(f"{rid}: an rfq needs at least one line")
        if not is_rfq and self.lines:
            raise ValueError(f"{rid}: a non_rfq email has no lines")

        quotes = [line.source_quote for line in self.lines]
        if len(set(quotes)) != len(quotes):
            raise ValueError(f"{rid}: two lines share the same source_quote")

        needs_review = self.type in ALWAYS_REVIEW or any(
            line.expected_route is Route.REVIEW for line in self.lines
        )
        required = Route.REVIEW if needs_review else Route.AUTO
        if self.expected_route is not required:
            raise ValueError(f"{rid}: expected_route must be {required} (worst line route)")

        if self.forbidden_actions and self.type is not RfqType.ADVERSARIAL:
            raise ValueError(f"{rid}: forbidden_actions only on adversarial requests")

        return self

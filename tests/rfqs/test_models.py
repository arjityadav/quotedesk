"""Label model rules."""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from quotedesk.rfqs.models import Label, LabelLine, Route


def line(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_quote": "200m NYM-J 3x1,5",
        "quantity": "200",
        "unit": "MTR",
        "expected_sku": "MH-100002",
        "acceptable_skus": ["MH-100001", "MH-100002"],
        "expected_route": "auto",
        "tags": ["unit_glued", "decimal_comma"],
    }
    return data | overrides


def label(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "rfq_id": "rfq_0142",
        "type": "messy_email",
        "intent": "rfq",
        "channel": "email",
        "customer_id": "CU-0007",
        "files": ["rfq_0142.eml"],
        "lines": [line()],
        "expected_route": "auto",
        "tags": ["typos"],
        "notes": "ring length not stated; 50 m and 100 m rings both fit",
    }
    return data | overrides


NOT_STOCKED = line(
    source_quote="10 Stk Hager XYZ",
    quantity="10",
    unit="C62",
    expected_sku=None,
    acceptable_skus=[],
    substitute_skus=["MH-100050"],
    expected_route="review",
)


def test_valid_label() -> None:
    lab = Label.model_validate(label())
    assert lab.lines[0].quantity == Decimal("200")
    assert lab.expected_route is Route.AUTO


def test_json_round_trip_keeps_quantity_exact() -> None:
    lab = Label.model_validate(label(lines=[line(quantity="12.5")]))
    text = lab.model_dump_json()
    assert '"quantity":"12.5"' in text  # a string in the file, never a float
    assert Label.model_validate_json(text) == lab


def test_frozen() -> None:
    lab = Label.model_validate(label())
    with pytest.raises(ValidationError):
        lab.notes = "changed"  # type: ignore[misc]


def test_not_in_catalog_line() -> None:
    lab = Label.model_validate(
        label(type="not_in_catalog", lines=[NOT_STOCKED], expected_route="review")
    )
    assert lab.lines[0].substitute_skus == ("MH-100050",)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"expected_sku": "X-1"}, "must look like MH-123456"),
        ({"acceptable_skus": ["MH-100002", "mh-1"]}, "must look like MH-123456"),
        ({"acceptable_skus": ["MH-100002", "MH-100002"]}, "duplicates"),
        ({"expected_sku": "MH-100003"}, "is not in acceptable_skus"),
        ({"substitute_skus": ["MH-100050"]}, "only when there is no expected_sku"),
        ({"expected_sku": None, "expected_route": "review"}, "acceptable_skus must be empty"),
        ({"expected_sku": None, "acceptable_skus": []}, "must route to review"),
        ({"quantity": "0"}, "greater than 0"),
        ({"source_quote": "  "}, "at least 1 character"),
        ({"unit": "KG"}, "unit"),
    ],
)
def test_line_rules(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        LabelLine.model_validate(line(**overrides))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"rfq_id": "rfq_142"}, "must look like rfq_0001"),
        ({"customer_id": "C-7"}, "must look like CU-0001"),
        ({"files": []}, "at least 1 item"),
        ({"files": ["rfq_0143.eml"]}, "must be named rfq_0142"),
        ({"intent": "order"}, "does not fit intent"),
        ({"type": "non_rfq", "lines": [], "expected_route": "review"}, "does not fit intent"),
        ({"channel": "pdf"}, "must arrive by email"),
        ({"type": "tender_pdf"}, "must arrive by pdf"),
        ({"lines": []}, "at least one line"),
        ({"lines": [line(), line()]}, "share the same source_quote"),
        ({"expected_route": "review"}, "must be auto"),
        ({"lines": [line(expected_route="review")]}, "must be review"),
        ({"type": "adversarial"}, "must be review"),
        ({"forbidden_actions": ["send_quote"]}, "only on adversarial"),
        ({"extra": 1}, "Extra inputs"),
    ],
)
def test_label_rules(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Label.model_validate(label(**overrides))


def test_non_rfq() -> None:
    lab = Label.model_validate(
        label(type="non_rfq", intent="complaint", lines=[], expected_route="review", tags=[])
    )
    assert lab.lines == ()
    with pytest.raises(ValidationError, match="has no lines"):
        Label.model_validate(label(type="non_rfq", intent="order", expected_route="review"))


def test_adversarial_routes_to_review_with_forbidden_actions() -> None:
    lab = Label.model_validate(
        label(type="adversarial", expected_route="review", forbidden_actions=["send_quote"])
    )
    assert lab.expected_route is Route.REVIEW
    assert lab.lines[0].expected_route is Route.AUTO  # the line itself is fine

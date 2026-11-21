"""Which products fit what the customer actually said.

A customer rarely names one exact article. "200 m NYM-J 3x1,5" fits a 50 m and a 100 m
ring from two manufacturers, and any of them is a correct answer. This module turns stated
specs into that set. The RFQ planner uses it to fill `acceptable_skus`, and error analysis
can use it later to ask whether a wrong pick at least fit the stated specs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from quotedesk.catalog.models import REQUIRED_ATTRIBUTES, AttrValue, Category, Product


@dataclass(frozen=True)
class Spec:
    """What the customer stated about one item. Anything left out is open."""

    category: Category
    attributes: Mapping[str, AttrValue] = field(default_factory=dict)
    manufacturer: str | None = None
    pack_size: int | None = None  # ring length for cables, e.g. 100 for a 100 m ring

    def missing_hard_specs(self) -> frozenset[str]:
        """Required attributes the customer did not state. Non-empty means review."""
        return REQUIRED_ATTRIBUTES[self.category] - self.attributes.keys()


def same_value(a: AttrValue, b: AttrValue) -> bool:
    """Decimals compare exactly (1.5 == 1.50); text ignores case ("IP65" == "ip65")."""
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return isinstance(a, Decimal) and isinstance(b, Decimal) and a == b
    return a.casefold() == b.casefold()


def fits(product: Product, spec: Spec) -> bool:
    """Does the product satisfy every stated spec?"""
    if product.category is not spec.category:
        return False
    if spec.manufacturer is not None and product.manufacturer != spec.manufacturer:
        return False
    if spec.pack_size is not None and product.pack_size != spec.pack_size:
        return False
    for key, wanted in spec.attributes.items():
        have = product.attributes.get(key)
        if have is None or not same_value(have, wanted):
            return False
    return True


def acceptable_skus(catalog: Iterable[Product], spec: Spec) -> tuple[str, ...]:
    """All active products that fit the spec, sorted. Discontinued products never count."""
    return tuple(sorted(p.sku for p in catalog if not p.discontinued and fits(p, spec)))


def spec_of(
    product: Product,
    omit: Iterable[str] = (),
    *,
    manufacturer: bool = False,
    pack_size: bool = False,
) -> Spec:
    """The spec a customer states when asking for this product but leaving out `omit`.

    By default manufacturer and ring length are not stated, as in most real requests.
    """
    left_out = set(omit)
    unknown = left_out - product.attributes.keys()
    if unknown:
        raise ValueError(f"{product.sku} has no attributes {sorted(unknown)}")
    return Spec(
        category=product.category,
        attributes={k: v for k, v in product.attributes.items() if k not in left_out},
        manufacturer=product.manufacturer if manufacturer else None,
        pack_size=product.pack_size if pack_size else None,
    )

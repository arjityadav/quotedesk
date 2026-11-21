"""Command line entry point.

quotedesk generate --seed 42 --out data/bmecat/catalog.xml
quotedesk seed --seed 42          # generate -> BMEcat -> parse -> migrate -> load into Postgres
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from quotedesk.catalog.bmecat import parse_bmecat, to_bmecat
from quotedesk.catalog.generator import generate_catalog

DEFAULT_OUT = Path("data/bmecat/catalog.xml")


def _generate(seed: int, out: Path) -> Path:
    products = generate_catalog(seed=seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(to_bmecat(products))
    print(f"wrote {len(products)} products to {out}")
    return out


def _seed(seed: int, out: Path) -> int:
    # Imported here so `generate` works without a database driver.
    import psycopg

    from quotedesk.customers.generator import generate_customers
    from quotedesk.customers.models import validate_customers
    from quotedesk.customers.orders import generate_orders, validate_orders
    from quotedesk.db.loader import load_customers, load_orders, load_products
    from quotedesk.db.migrate import upgrade

    path = _generate(seed, out)
    result = parse_bmecat(path.read_bytes())  # load via the parser: same path as a supplier file
    for issue in result.issues:
        print(f"issue: {issue}", file=sys.stderr)
    if result.issues:
        return 1

    customers, prices = generate_customers(result.products, seed=seed)
    orders = generate_orders(result.products, customers, prices, seed=seed)
    problems = validate_customers(customers, prices, result.products)
    problems += validate_orders(orders, customers, result.products)
    for problem in problems:
        print(f"issue: {problem}", file=sys.stderr)
    if problems:
        return 1

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    upgrade(url)  # bring the schema to the latest migration first
    with psycopg.connect(url) as conn:
        n = load_products(conn, result.products)
        c = load_customers(conn, customers, prices)
        o = load_orders(conn, orders)
    print(f"loaded {n} products, {c} customers, {len(prices)} contract prices, {o} orders")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quotedesk")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "seed"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--seed", type=int, default=42)
        cmd.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    if args.command == "generate":
        _generate(args.seed, args.out)
        return 0
    return _seed(args.seed, args.out)


if __name__ == "__main__":
    raise SystemExit(main())

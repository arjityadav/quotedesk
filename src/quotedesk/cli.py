"""Command line entry point.

quotedesk generate --seed 42 --out data/bmecat/catalog.xml
quotedesk seed --seed 42          # generate -> BMEcat -> parse -> load into Postgres
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
    import psycopg  # imported here so `generate` works without a database driver

    from quotedesk.db.loader import apply_schema, load_products

    path = _generate(seed, out)
    result = parse_bmecat(path.read_bytes())  # load via the parser: same path as a supplier file
    for issue in result.issues:
        print(f"issue: {issue}", file=sys.stderr)
    if result.issues:
        return 1

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    with psycopg.connect(url) as conn:
        apply_schema(conn)
        conn.commit()
        n = load_products(conn, result.products)
    print(f"loaded {n} products")
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

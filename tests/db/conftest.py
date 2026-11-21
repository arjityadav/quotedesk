"""Shared database fixtures. Each test gets its own throwaway Postgres schema."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from quotedesk.catalog.generator import generate_catalog
from quotedesk.catalog.models import Product
from quotedesk.db.loader import apply_schema


@pytest.fixture
def db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set (run `make db-up`, then `make test-db`)")
    return url


@pytest.fixture
def empty_schema(db_url: str) -> Iterator[str]:
    """A new, empty schema; dropped afterwards."""
    psycopg = pytest.importorskip("psycopg")
    schema = f"test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
        try:
            yield schema
        finally:
            c.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def conn(db_url: str, empty_schema: str) -> Iterator[Any]:
    """An autocommit connection to a fresh schema with all tables created."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute(f"SET search_path TO {empty_schema}")
        apply_schema(c)
        yield c


@pytest.fixture(scope="session")
def catalog() -> list[Product]:
    return generate_catalog(seed=42)

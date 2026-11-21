"""Alembic migrations. Needs PostgreSQL: `make db-up`, `make test-db`."""

from __future__ import annotations

from typing import Any

import pytest

from quotedesk.db.migrate import alembic_config, sql_files, sqlalchemy_url

pytestmark = pytest.mark.db


def tables(conn: Any, schema: str) -> set[str]:
    rows = conn.exec_driver_sql(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)
    )
    return {r[0] for r in rows}


def test_upgrade_and_downgrade(db_url: str, empty_schema: str) -> None:
    from alembic import command
    from sqlalchemy import create_engine

    engine = create_engine(
        sqlalchemy_url(db_url), connect_args={"options": f"-csearch_path={empty_schema}"}
    )
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        assert tables(conn, empty_schema) == {
            "alembic_version", "products", "price_lists", "customers", "prices",
            "orders", "order_lines",
        }  # fmt: skip
        version = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
        assert version == "0003"

        command.downgrade(cfg, "base")
        assert tables(conn, empty_schema) == {"alembic_version"}
    engine.dispose()


def test_one_revision_per_sql_file() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    revisions = sorted(r.revision for r in script.walk_revisions())
    assert revisions == [p.name[:4] for p in sql_files()]


def test_sqlalchemy_url() -> None:
    assert sqlalchemy_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert sqlalchemy_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert sqlalchemy_url("postgresql+psycopg://x") == "postgresql+psycopg://x"

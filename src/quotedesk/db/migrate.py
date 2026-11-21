"""Database migrations (Alembic).

The SQL files in `sql/` are the single source of truth for the schema. Each Alembic
revision in `migrations/versions/` runs one of them, so:

  * real databases are upgraded with Alembic, which records the version, and
  * tests apply the same files directly to a throwaway schema (see loader.apply_schema).

Usage: `make migrate` (or `uv run alembic upgrade head`); `quotedesk seed` runs it too.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

HERE = Path(__file__).parent
SQL_DIR = HERE / "sql"
MIGRATIONS_DIR = HERE / "migrations"


def sql_files() -> list[Path]:
    """Schema files in the order they must run (0001_..., 0002_..., ...)."""
    return sorted(SQL_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def read_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def run_sql_file(name: str) -> None:
    """Run one schema file inside the current Alembic migration.

    Goes through the psycopg connection directly: the files hold several statements and
    `::` casts, which SQLAlchemy's text() would try to parse as bind parameters. It is
    the same connection, so the same transaction.
    """
    from alembic import op

    driver = op.get_bind().connection.driver_connection
    if driver is None:
        raise RuntimeError("no database connection for the migration")
    driver.execute(read_sql(name))


def sqlalchemy_url(url: str) -> str:
    """postgresql://... -> postgresql+psycopg://... (SQLAlchemy needs the driver named)."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url:
        # Config values go through ConfigParser interpolation: escape % (e.g. in passwords).
        cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)

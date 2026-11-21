"""Product catalog.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from alembic import op

from quotedesk.db.migrate import run_sql_file

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0001_products.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS products")

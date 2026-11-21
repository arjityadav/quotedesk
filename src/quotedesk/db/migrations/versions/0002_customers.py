"""Customers and contract price lists.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

from quotedesk.db.migrate import run_sql_file

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0002_customers.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS prices")
    op.execute("DROP TABLE IF EXISTS customers")
    op.execute("DROP TABLE IF EXISTS price_lists")

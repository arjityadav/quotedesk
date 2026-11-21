"""Order history.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

from quotedesk.db.migrate import run_sql_file

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0003_orders.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS order_lines")
    op.execute("DROP TABLE IF EXISTS orders")

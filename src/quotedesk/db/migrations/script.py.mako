"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

from __future__ import annotations

from alembic import op

from quotedesk.db.migrate import run_sql_file

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Put the change in a new numbered file in src/quotedesk/db/sql/ and name it here.
    run_sql_file("${up_revision}_${message.lower().replace(' ', '_')}.sql")


def downgrade() -> None:
    op.execute("-- undo the change here")

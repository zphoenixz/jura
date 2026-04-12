"""add epics_police_decisions table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "epics_police_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("week_monday", sa.Date(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("orphan_identifier", sa.String(50), nullable=False),
        sa.Column("orphan_labels", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("orphan_squad", sa.String(100), nullable=True),
        sa.Column("suggested_parent_id", sa.String(50), nullable=True),
        sa.Column("suggested_confidence", sa.Integer(), nullable=True),
        sa.Column("suggested_signals", postgresql.JSONB(), nullable=True),
        sa.Column("match_source", sa.String(20), nullable=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("actual_parent_id", sa.String(50), nullable=True),
        sa.Column("inferred", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_epics_police_decisions_week", "epics_police_decisions", ["week_monday"])
    op.create_index("ix_epics_police_decisions_orphan", "epics_police_decisions", ["orphan_identifier"])
    op.create_index("ix_epics_police_decisions_decision", "epics_police_decisions", ["decision"])


def downgrade() -> None:
    op.drop_index("ix_epics_police_decisions_decision", table_name="epics_police_decisions")
    op.drop_index("ix_epics_police_decisions_orphan", table_name="epics_police_decisions")
    op.drop_index("ix_epics_police_decisions_week", table_name="epics_police_decisions")
    op.drop_table("epics_police_decisions")

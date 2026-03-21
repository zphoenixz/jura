"""initial tables

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-04-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- configs ---
    op.create_table(
        "configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "key", name="uq_configs_source_key"),
    )

    # --- persons ---
    op.create_table(
        "persons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("slack_user_id", sa.String(50), nullable=True),
        sa.Column("linear_user_id", sa.String(100), nullable=True),
        sa.Column("fireflies_name", sa.String(200), nullable=True),
        sa.Column("squad", sa.String(100), nullable=True),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("slack_user_id"),
        sa.UniqueConstraint("linear_user_id"),
    )
    op.create_index("ix_persons_display_name", "persons", ["display_name"])

    # --- weeks ---
    op.create_table(
        "weeks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monday_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("monday_date"),
    )

    # --- fetch_logs ---
    op.create_table(
        "fetch_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "week_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weeks.id"),
            nullable=False,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fetch_logs_week_id_source", "fetch_logs", ["week_id", "source"]
    )

    # --- slack_messages ---
    op.create_table(
        "slack_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "week_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weeks.id"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id"),
            nullable=True,
        ),
        sa.Column("channel", sa.String(200), nullable=False),
        sa.Column("channel_id", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("slack_ts", sa.String(50), nullable=False),
        sa.Column("thread_ts", sa.String(50), nullable=True),
        sa.Column("is_dm", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "is_thread_reply", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("reactions", postgresql.JSONB(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_id", "channel_id", "slack_ts", name="uq_slack_messages_week_channel_ts"
        ),
    )
    op.create_index(
        "ix_slack_messages_week_channel", "slack_messages", ["week_id", "channel"]
    )
    op.create_index(
        "ix_slack_messages_week_is_dm", "slack_messages", ["week_id", "is_dm"]
    )
    op.create_index(
        "ix_slack_messages_week_person", "slack_messages", ["week_id", "person_id"]
    )

    # --- linear_tickets ---
    op.create_table(
        "linear_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "week_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weeks.id"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id"),
            nullable=True,
        ),
        sa.Column("linear_id", sa.String(100), nullable=False),
        sa.Column("identifier", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(100), nullable=False),
        sa.Column("status_type", sa.String(50), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("priority_label", sa.String(50), nullable=False),
        sa.Column(
            "labels",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("cycle_number", sa.Integer(), nullable=True),
        sa.Column("cycle_name", sa.String(200), nullable=True),
        sa.Column("parent_identifier", sa.String(50), nullable=True),
        sa.Column("child_identifiers", postgresql.JSONB(), nullable=True),
        sa.Column("attachments", postgresql.JSONB(), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("linear_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linear_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_id", "linear_id", name="uq_linear_tickets_week_linear"
        ),
    )
    op.create_index(
        "ix_linear_tickets_week_status_type",
        "linear_tickets",
        ["week_id", "status_type"],
    )
    op.create_index(
        "ix_linear_tickets_week_person", "linear_tickets", ["week_id", "person_id"]
    )
    op.create_index(
        "ix_linear_tickets_week_identifier",
        "linear_tickets",
        ["week_id", "identifier"],
    )

    # --- linear_comments ---
    op.create_table(
        "linear_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "ticket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("linear_tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("linear_comment_id", sa.String(100), nullable=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id"),
            nullable=True,
        ),
        sa.Column("author_name", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("linear_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- meetings ---
    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "week_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weeks.id"),
            nullable=False,
        ),
        sa.Column("fireflies_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("meeting_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("keywords", postgresql.JSONB(), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("short_summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("action_items", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_id", "fireflies_id", name="uq_meetings_week_fireflies"
        ),
    )
    op.create_index("ix_meetings_week_id", "meetings", ["week_id"])

    # --- meeting_attendees ---
    op.create_table(
        "meeting_attendees",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id"),
            nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "meeting_id", "email", name="uq_meeting_attendees_meeting_email"
        ),
    )

    # --- epics ---
    op.create_table(
        "epics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "week_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weeks.id"),
            nullable=False,
        ),
        sa.Column("notion_page_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(100), nullable=False),
        sa.Column(
            "team",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("pm_lead", sa.String(200), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("dates", postgresql.JSONB(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("properties", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_id", "notion_page_id", name="uq_epics_week_notion_page"
        ),
    )
    op.create_index("ix_epics_week_status", "epics", ["week_id", "status"])

    # --- epic_sub_pages ---
    op.create_table(
        "epic_sub_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "epic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("epics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("notion_page_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("epic_sub_pages")
    op.drop_table("epics")
    op.drop_table("meeting_attendees")
    op.drop_table("meetings")
    op.drop_table("linear_comments")
    op.drop_table("linear_tickets")
    op.drop_table("slack_messages")
    op.drop_table("fetch_logs")
    op.drop_table("weeks")
    op.drop_table("persons")
    op.drop_table("configs")

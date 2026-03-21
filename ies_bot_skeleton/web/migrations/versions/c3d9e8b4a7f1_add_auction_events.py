"""add auction events flow table

Revision ID: c3d9e8b4a7f1
Revises: a2c4f7e91b0d
Create Date: 2026-03-21 14:20:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "c3d9e8b4a7f1"
down_revision = "a2c4f7e91b0d"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "auction_events" in inspector.get_table_names():
        return

    op.create_table(
        "auction_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False, server_default="watch"),
        sa.Column("bid_level", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("budget_effect", sa.Float(), nullable=False, server_default="0"),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["game_sessions.id"]),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auction_event_session_created_desc",
        "auction_events",
        ["session_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_auction_event_session_lot_outcome",
        "auction_events",
        ["session_id", "lot_id", "outcome"],
        unique=False,
    )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "auction_events" not in inspector.get_table_names():
        return
    op.drop_index("ix_auction_event_session_lot_outcome", table_name="auction_events")
    op.drop_index("ix_auction_event_session_created_desc", table_name="auction_events")
    op.drop_table("auction_events")

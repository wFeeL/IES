"""forecast only analysis and lot portfolio purchases

Revision ID: 7a4f6d2c1b0e
Revises: 6d5c7a1f8c2e
Create Date: 2026-03-12 15:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "7a4f6d2c1b0e"
down_revision = "6d5c7a1f8c2e"
branch_labels = None
depends_on = None


def _column_map(table_name: str) -> dict[str, dict]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return {}
    return {col["name"]: col for col in inspector.get_columns(table_name)}


def upgrade():
    session_cols = _column_map("game_sessions")
    if session_cols:
        with op.batch_alter_table("game_sessions", schema=None) as batch_op:
            if "analysis_mode" in session_cols:
                batch_op.drop_column("analysis_mode")
            if "corridor_settings_json" in session_cols:
                batch_op.drop_column("corridor_settings_json")

    lot_cols = _column_map("lots")
    if lot_cols:
        with op.batch_alter_table("lots", schema=None) as batch_op:
            if "purchase_price" not in lot_cols:
                batch_op.add_column(sa.Column("purchase_price", sa.Float(), nullable=True))
            if "purchased_at" not in lot_cols:
                batch_op.add_column(sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    lot_cols = _column_map("lots")
    if lot_cols:
        with op.batch_alter_table("lots", schema=None) as batch_op:
            if "purchased_at" in lot_cols:
                batch_op.drop_column("purchased_at")
            if "purchase_price" in lot_cols:
                batch_op.drop_column("purchase_price")

    session_cols = _column_map("game_sessions")
    if session_cols:
        with op.batch_alter_table("game_sessions", schema=None) as batch_op:
            if "analysis_mode" not in session_cols:
                batch_op.add_column(sa.Column("analysis_mode", sa.String(length=32), nullable=False, server_default="forecast"))
            if "corridor_settings_json" not in session_cols:
                batch_op.add_column(sa.Column("corridor_settings_json", sa.JSON(), nullable=False, server_default="{}"))

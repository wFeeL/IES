"""add session analysis settings

Revision ID: 6d5c7a1f8c2e
Revises: 9f71a6b2d4ef
Create Date: 2026-03-11 12:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "6d5c7a1f8c2e"
down_revision = "9f71a6b2d4ef"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _column_map(table_name: str) -> dict[str, dict]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return {}
    return {col["name"]: col for col in inspector.get_columns(table_name)}


def _fk_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {fk.get("name") for fk in inspector.get_foreign_keys(table_name) if fk.get("name")}


def upgrade():
    if not _table_exists("game_sessions"):
        return

    session_cols = _column_map("game_sessions")
    session_fks = _fk_names("game_sessions")

    needs_batch = (
        "analysis_mode" not in session_cols
        or "selected_forecast_id" not in session_cols
        or "corridor_settings_json" not in session_cols
        or "fk_game_sessions_selected_forecast_id" not in session_fks
    )
    if needs_batch:
        with op.batch_alter_table("game_sessions", schema=None) as batch_op:
            if "analysis_mode" not in session_cols:
                batch_op.add_column(sa.Column("analysis_mode", sa.String(length=32), nullable=True))
            if "selected_forecast_id" not in session_cols:
                batch_op.add_column(sa.Column("selected_forecast_id", sa.Integer(), nullable=True))
            if "corridor_settings_json" not in session_cols:
                batch_op.add_column(sa.Column("corridor_settings_json", sa.JSON(), nullable=True))

            refreshed_cols = _column_map("game_sessions")
            refreshed_fks = _fk_names("game_sessions")
            if (
                "selected_forecast_id" in refreshed_cols
                and "fk_game_sessions_selected_forecast_id" not in refreshed_fks
            ):
                batch_op.create_foreign_key(
                    "fk_game_sessions_selected_forecast_id",
                    "forecasts",
                    ["selected_forecast_id"],
                    ["id"],
                )

    session_cols = _column_map("game_sessions")
    if "analysis_mode" in session_cols:
        op.execute("UPDATE game_sessions SET analysis_mode = 'no_forecast' WHERE analysis_mode IS NULL")
    if "corridor_settings_json" in session_cols:
        op.execute("UPDATE game_sessions SET corridor_settings_json = '{}' WHERE corridor_settings_json IS NULL")

    session_cols = _column_map("game_sessions")
    if session_cols.get("analysis_mode", {}).get("nullable", True):
        with op.batch_alter_table("game_sessions", schema=None) as batch_op:
            batch_op.alter_column("analysis_mode", nullable=False)
    session_cols = _column_map("game_sessions")
    if session_cols.get("corridor_settings_json", {}).get("nullable", True):
        with op.batch_alter_table("game_sessions", schema=None) as batch_op:
            batch_op.alter_column("corridor_settings_json", nullable=False)


def downgrade():
    if not _table_exists("game_sessions"):
        return
    session_cols = _column_map("game_sessions")
    session_fks = _fk_names("game_sessions")
    with op.batch_alter_table("game_sessions", schema=None) as batch_op:
        if "fk_game_sessions_selected_forecast_id" in session_fks:
            batch_op.drop_constraint("fk_game_sessions_selected_forecast_id", type_="foreignkey")
        if "corridor_settings_json" in session_cols:
            batch_op.drop_column("corridor_settings_json")
        if "selected_forecast_id" in session_cols:
            batch_op.drop_column("selected_forecast_id")
        if "analysis_mode" in session_cols:
            batch_op.drop_column("analysis_mode")

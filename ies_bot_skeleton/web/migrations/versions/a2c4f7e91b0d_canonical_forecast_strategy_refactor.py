"""canonical forecast schema and strategy metadata

Revision ID: a2c4f7e91b0d
Revises: 7a4f6d2c1b0e
Create Date: 2026-03-13 10:30:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "a2c4f7e91b0d"
down_revision = "7a4f6d2c1b0e"
branch_labels = None
depends_on = None


def _column_map(table_name: str) -> dict[str, dict]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return {}
    return {col["name"]: col for col in inspector.get_columns(table_name)}


def upgrade():
    object_type_cols = _column_map("object_types")
    if object_type_cols:
        with op.batch_alter_table("object_types", schema=None) as batch_op:
            if "forecast_profile_key" not in object_type_cols:
                batch_op.add_column(
                    sa.Column("forecast_profile_key", sa.String(length=128), nullable=True)
                )
            if "resource_dependencies_json" not in object_type_cols:
                batch_op.add_column(
                    sa.Column("resource_dependencies_json", sa.JSON(), nullable=True)
                )
            if "forecast_model_type" not in object_type_cols:
                batch_op.add_column(
                    sa.Column("forecast_model_type", sa.String(length=64), nullable=True)
                )
            if "economic_role" not in object_type_cols:
                batch_op.add_column(sa.Column("economic_role", sa.String(length=32), nullable=True))

    op.execute(
        "UPDATE object_types SET forecast_profile_key = '' WHERE forecast_profile_key IS NULL"
    )
    op.execute(
        "UPDATE object_types SET resource_dependencies_json = '[]' WHERE resource_dependencies_json IS NULL"
    )
    op.execute(
        "UPDATE object_types SET forecast_model_type = 'direct_profile' WHERE forecast_model_type IS NULL"
    )
    op.execute("UPDATE object_types SET economic_role = 'auto' WHERE economic_role IS NULL")

    object_type_cols = _column_map("object_types")
    if object_type_cols.get("forecast_profile_key", {}).get("nullable", True):
        with op.batch_alter_table("object_types", schema=None) as batch_op:
            batch_op.alter_column("forecast_profile_key", nullable=False)
    object_type_cols = _column_map("object_types")
    if object_type_cols.get("resource_dependencies_json", {}).get("nullable", True):
        with op.batch_alter_table("object_types", schema=None) as batch_op:
            batch_op.alter_column("resource_dependencies_json", nullable=False)
    object_type_cols = _column_map("object_types")
    if object_type_cols.get("forecast_model_type", {}).get("nullable", True):
        with op.batch_alter_table("object_types", schema=None) as batch_op:
            batch_op.alter_column("forecast_model_type", nullable=False)
    object_type_cols = _column_map("object_types")
    if object_type_cols.get("economic_role", {}).get("nullable", True):
        with op.batch_alter_table("object_types", schema=None) as batch_op:
            batch_op.alter_column("economic_role", nullable=False)

    forecast_cols = _column_map("forecasts")
    if forecast_cols:
        with op.batch_alter_table("forecasts", schema=None) as batch_op:
            if "normalization_map_json" not in forecast_cols:
                batch_op.add_column(sa.Column("normalization_map_json", sa.JSON(), nullable=True))
            if "compatibility_report_json" not in forecast_cols:
                batch_op.add_column(
                    sa.Column("compatibility_report_json", sa.JSON(), nullable=True)
                )
            if "is_compatible" not in forecast_cols:
                batch_op.add_column(sa.Column("is_compatible", sa.Boolean(), nullable=True))
            if "incompatibility_reason" not in forecast_cols:
                batch_op.add_column(sa.Column("incompatibility_reason", sa.Text(), nullable=True))

    op.execute(
        "UPDATE forecasts SET normalization_map_json = '{}' WHERE normalization_map_json IS NULL"
    )
    op.execute(
        "UPDATE forecasts SET compatibility_report_json = '{}' WHERE compatibility_report_json IS NULL"
    )
    op.execute("UPDATE forecasts SET is_compatible = 1 WHERE is_compatible IS NULL")
    op.execute(
        "UPDATE forecasts SET incompatibility_reason = '' WHERE incompatibility_reason IS NULL"
    )

    forecast_cols = _column_map("forecasts")
    if forecast_cols.get("normalization_map_json", {}).get("nullable", True):
        with op.batch_alter_table("forecasts", schema=None) as batch_op:
            batch_op.alter_column("normalization_map_json", nullable=False)
    forecast_cols = _column_map("forecasts")
    if forecast_cols.get("compatibility_report_json", {}).get("nullable", True):
        with op.batch_alter_table("forecasts", schema=None) as batch_op:
            batch_op.alter_column("compatibility_report_json", nullable=False)
    forecast_cols = _column_map("forecasts")
    if forecast_cols.get("is_compatible", {}).get("nullable", True):
        with op.batch_alter_table("forecasts", schema=None) as batch_op:
            batch_op.alter_column("is_compatible", nullable=False)
    forecast_cols = _column_map("forecasts")
    if forecast_cols.get("incompatibility_reason", {}).get("nullable", True):
        with op.batch_alter_table("forecasts", schema=None) as batch_op:
            batch_op.alter_column("incompatibility_reason", nullable=False)

    period_cols = _column_map("forecast_periods")
    if period_cols:
        with op.batch_alter_table("forecast_periods", schema=None) as batch_op:
            if "factors_json" not in period_cols:
                batch_op.add_column(sa.Column("factors_json", sa.JSON(), nullable=True))
            if "profiles_json" not in period_cols:
                batch_op.add_column(sa.Column("profiles_json", sa.JSON(), nullable=True))

    op.execute("UPDATE forecast_periods SET factors_json = '{}' WHERE factors_json IS NULL")
    op.execute("UPDATE forecast_periods SET profiles_json = '{}' WHERE profiles_json IS NULL")

    period_cols = _column_map("forecast_periods")
    if period_cols.get("factors_json", {}).get("nullable", True):
        with op.batch_alter_table("forecast_periods", schema=None) as batch_op:
            batch_op.alter_column("factors_json", nullable=False)
    period_cols = _column_map("forecast_periods")
    if period_cols.get("profiles_json", {}).get("nullable", True):
        with op.batch_alter_table("forecast_periods", schema=None) as batch_op:
            batch_op.alter_column("profiles_json", nullable=False)


def downgrade():
    period_cols = _column_map("forecast_periods")
    if period_cols:
        with op.batch_alter_table("forecast_periods", schema=None) as batch_op:
            if "profiles_json" in period_cols:
                batch_op.drop_column("profiles_json")
            if "factors_json" in period_cols:
                batch_op.drop_column("factors_json")

    forecast_cols = _column_map("forecasts")
    if forecast_cols:
        with op.batch_alter_table("forecasts", schema=None) as batch_op:
            if "incompatibility_reason" in forecast_cols:
                batch_op.drop_column("incompatibility_reason")
            if "is_compatible" in forecast_cols:
                batch_op.drop_column("is_compatible")
            if "compatibility_report_json" in forecast_cols:
                batch_op.drop_column("compatibility_report_json")
            if "normalization_map_json" in forecast_cols:
                batch_op.drop_column("normalization_map_json")

    object_type_cols = _column_map("object_types")
    if object_type_cols:
        with op.batch_alter_table("object_types", schema=None) as batch_op:
            if "economic_role" in object_type_cols:
                batch_op.drop_column("economic_role")
            if "forecast_model_type" in object_type_cols:
                batch_op.drop_column("forecast_model_type")
            if "resource_dependencies_json" in object_type_cols:
                batch_op.drop_column("resource_dependencies_json")
            if "forecast_profile_key" in object_type_cols:
                batch_op.drop_column("forecast_profile_key")

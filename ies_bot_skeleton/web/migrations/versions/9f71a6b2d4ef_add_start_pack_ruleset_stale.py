"""add start pack templates, ruleset bindings, stale evaluation fields

Revision ID: 9f71a6b2d4ef
Revises: b266c1d655ae
Create Date: 2026-03-10 22:05:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "9f71a6b2d4ef"
down_revision = "b266c1d655ae"
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


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {idx.get("name") for idx in inspector.get_indexes(table_name) if idx.get("name")}


def upgrade():
    # Cleanup of leftovers from interrupted SQLite batch migration runs.
    if _table_exists("_alembic_tmp_rulesets"):
        op.drop_table("_alembic_tmp_rulesets")

    if not _table_exists("start_pack_templates"):
        op.create_table(
            "start_pack_templates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("is_builtin", sa.Boolean(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
        )

    if not _table_exists("start_pack_template_items"):
        op.create_table(
            "start_pack_template_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("template_id", sa.Integer(), nullable=False),
            sa.Column("object_type_id", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("custom_name", sa.String(length=128), nullable=False),
            sa.Column("district", sa.String(length=64), nullable=False),
            sa.Column("parameters_json", sa.JSON(), nullable=False),
            sa.Column("parent_item_id", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(["object_type_id"], ["object_types.id"]),
            sa.ForeignKeyConstraint(["parent_item_id"], ["start_pack_template_items.id"]),
            sa.ForeignKeyConstraint(["template_id"], ["start_pack_templates.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    ruleset_cols = _column_map("rulesets")
    ruleset_fks = _fk_names("rulesets")

    needs_ruleset_batch = (
        "model_settings_json" not in ruleset_cols
        or "active_start_pack_template_id" not in ruleset_cols
        or "fk_rulesets_active_start_pack_template_id" not in ruleset_fks
    )
    if needs_ruleset_batch:
        with op.batch_alter_table("rulesets", schema=None) as batch_op:
            if "model_settings_json" not in ruleset_cols:
                batch_op.add_column(sa.Column("model_settings_json", sa.JSON(), nullable=True))
            if "active_start_pack_template_id" not in ruleset_cols:
                batch_op.add_column(
                    sa.Column("active_start_pack_template_id", sa.Integer(), nullable=True)
                )

            # Create FK only when missing and target column exists.
            refreshed_cols = _column_map("rulesets")
            refreshed_fks = _fk_names("rulesets")
            if (
                "active_start_pack_template_id" in refreshed_cols
                and "fk_rulesets_active_start_pack_template_id" not in refreshed_fks
            ):
                batch_op.create_foreign_key(
                    "fk_rulesets_active_start_pack_template_id",
                    "start_pack_templates",
                    ["active_start_pack_template_id"],
                    ["id"],
                )

    if "model_settings_json" in _column_map("rulesets"):
        op.execute(
            "UPDATE rulesets SET model_settings_json = '{}' WHERE model_settings_json IS NULL"
        )
        if _column_map("rulesets").get("model_settings_json", {}).get("nullable", True):
            with op.batch_alter_table("rulesets", schema=None) as batch_op:
                batch_op.alter_column("model_settings_json", nullable=False)

    eval_cols = _column_map("evaluation_results")
    if "is_stale" not in eval_cols:
        op.add_column("evaluation_results", sa.Column("is_stale", sa.Boolean(), nullable=True))
    if "stale_reason" not in eval_cols:
        op.add_column("evaluation_results", sa.Column("stale_reason", sa.Text(), nullable=True))
    if "stale_marked_at" not in eval_cols:
        op.add_column(
            "evaluation_results",
            sa.Column("stale_marked_at", sa.DateTime(timezone=True), nullable=True),
        )

    eval_cols = _column_map("evaluation_results")
    if "is_stale" in eval_cols:
        op.execute("UPDATE evaluation_results SET is_stale = 0 WHERE is_stale IS NULL")
    if "stale_reason" in eval_cols:
        op.execute("UPDATE evaluation_results SET stale_reason = '' WHERE stale_reason IS NULL")

    # Indexes are created via raw SQL to avoid SQLite batch issues with expression indexes.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_session_stale_created_desc "
        "ON evaluation_results (session_id, is_stale, created_at DESC)"
    )

    # Ensure legacy evaluation index exists as expected by previous migration.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_session_lot_mode_created_desc "
        "ON evaluation_results (session_id, lot_id, mode, created_at DESC)"
    )


def downgrade():
    if _table_exists("evaluation_results"):
        eval_cols = _column_map("evaluation_results")
        eval_indexes = _index_names("evaluation_results")
        with op.batch_alter_table("evaluation_results", schema=None) as batch_op:
            if "ix_eval_session_stale_created_desc" in eval_indexes:
                batch_op.drop_index("ix_eval_session_stale_created_desc")
            if "stale_marked_at" in eval_cols:
                batch_op.drop_column("stale_marked_at")
            if "stale_reason" in eval_cols:
                batch_op.drop_column("stale_reason")
            if "is_stale" in eval_cols:
                batch_op.drop_column("is_stale")

    if _table_exists("rulesets"):
        ruleset_cols = _column_map("rulesets")
        ruleset_fks = _fk_names("rulesets")
        with op.batch_alter_table("rulesets", schema=None) as batch_op:
            if "fk_rulesets_active_start_pack_template_id" in ruleset_fks:
                batch_op.drop_constraint(
                    "fk_rulesets_active_start_pack_template_id",
                    type_="foreignkey",
                )
            if "active_start_pack_template_id" in ruleset_cols:
                batch_op.drop_column("active_start_pack_template_id")
            if "model_settings_json" in ruleset_cols:
                batch_op.drop_column("model_settings_json")

    if _table_exists("start_pack_template_items"):
        op.drop_table("start_pack_template_items")
    if _table_exists("start_pack_templates"):
        op.drop_table("start_pack_templates")

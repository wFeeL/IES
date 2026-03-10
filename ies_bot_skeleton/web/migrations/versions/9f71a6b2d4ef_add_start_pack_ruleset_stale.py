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


def upgrade():
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

    with op.batch_alter_table("rulesets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("model_settings_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("active_start_pack_template_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_rulesets_active_start_pack_template_id",
            "start_pack_templates",
            ["active_start_pack_template_id"],
            ["id"],
        )

    op.execute("UPDATE rulesets SET model_settings_json = '{}' WHERE model_settings_json IS NULL")

    with op.batch_alter_table("rulesets", schema=None) as batch_op:
        batch_op.alter_column("model_settings_json", nullable=False)

    with op.batch_alter_table("evaluation_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_stale", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("stale_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("stale_marked_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE evaluation_results SET is_stale = 0 WHERE is_stale IS NULL")
    op.execute("UPDATE evaluation_results SET stale_reason = '' WHERE stale_reason IS NULL")

    with op.batch_alter_table("evaluation_results", schema=None) as batch_op:
        batch_op.alter_column("is_stale", nullable=False)
        batch_op.alter_column("stale_reason", nullable=False)
        batch_op.create_index(
            "ix_eval_session_stale_created_desc",
            ["session_id", "is_stale", sa.literal_column("created_at DESC")],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("evaluation_results", schema=None) as batch_op:
        batch_op.drop_index("ix_eval_session_stale_created_desc")
        batch_op.drop_column("stale_marked_at")
        batch_op.drop_column("stale_reason")
        batch_op.drop_column("is_stale")

    with op.batch_alter_table("rulesets", schema=None) as batch_op:
        batch_op.drop_constraint("fk_rulesets_active_start_pack_template_id", type_="foreignkey")
        batch_op.drop_column("active_start_pack_template_id")
        batch_op.drop_column("model_settings_json")

    op.drop_table("start_pack_template_items")
    op.drop_table("start_pack_templates")

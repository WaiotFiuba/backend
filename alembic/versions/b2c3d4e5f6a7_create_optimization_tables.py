"""create optimization tables

Revision ID: b2c3d4e5f6a7
Revises: f8b2c4d6e8a1
Create Date: 2026-09-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "f8b2c4d6e8a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create redistribution_plans table
    op.create_table(
        "redistribution_plans",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("moves", sa.JSON(), nullable=False),
        sa.Column("metrics_snapshot", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("simulation_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id"],
            ["simulation_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. Create optimization_whatif_levels table
    op.create_table(
        "optimization_whatif_levels",
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("container_id", sa.BigInteger(), nullable=False),
        sa.Column("original_site_id", sa.BigInteger(), nullable=True),
        sa.Column("optimized_site_id", sa.BigInteger(), nullable=True),
        sa.Column("virtual_level", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_reading", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["redistribution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["container_id"],
            ["containers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("plan_id", "container_id"),
    )
    op.create_index(
        "ix_opt_whatif_plan_id",
        "optimization_whatif_levels",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        "ix_opt_whatif_opt_site",
        "optimization_whatif_levels",
        ["optimized_site_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_opt_whatif_opt_site", table_name="optimization_whatif_levels")
    op.drop_index("ix_opt_whatif_plan_id", table_name="optimization_whatif_levels")
    op.drop_table("optimization_whatif_levels")
    op.drop_table("redistribution_plans")

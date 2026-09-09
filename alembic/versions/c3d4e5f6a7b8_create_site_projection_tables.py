"""create site projection tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-09 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_projection_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("model_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="completed", nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("horizon_hours", sa.Integer(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("critical_level", sa.Integer(), nullable=False),
        sa.Column("level_aggregation", sa.String(), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=True),
        sa.Column("stop_at_full", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("site_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_site_projection_runs_model_key",
        "site_projection_runs",
        ["model_key"],
        unique=False,
    )

    op.create_table(
        "site_projection_points",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("site_id", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("predicted_level", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column(
            "reaches_critical", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("reaches_full", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("confidence", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["site_projection_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "site_id",
            "timestamp",
            name="uq_site_projection_points_run_site_timestamp",
        ),
    )
    op.create_index(
        "ix_site_projection_points_run_id",
        "site_projection_points",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_site_projection_points_site_id",
        "site_projection_points",
        ["site_id"],
        unique=False,
    )
    op.create_index(
        "ix_site_projection_points_timestamp",
        "site_projection_points",
        ["timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_site_projection_points_timestamp", table_name="site_projection_points"
    )
    op.drop_index("ix_site_projection_points_site_id", table_name="site_projection_points")
    op.drop_index("ix_site_projection_points_run_id", table_name="site_projection_points")
    op.drop_table("site_projection_points")
    op.drop_index("ix_site_projection_runs_model_key", table_name="site_projection_runs")
    op.drop_table("site_projection_runs")

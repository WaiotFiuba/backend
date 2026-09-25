"""create site model evaluation tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_model_evaluations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("model_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="completed", nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("horizon_hours", sa.Integer(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("critical_level", sa.Integer(), nullable=False),
        sa.Column("level_aggregation", sa.String(), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=True),
        sa.Column("site_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cutoff_start_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cutoff_end_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        "ix_site_model_evaluations_model_key",
        "site_model_evaluations",
        ["model_key"],
        unique=False,
    )

    op.create_table(
        "site_model_evaluation_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_id", sa.BigInteger(), nullable=False),
        sa.Column("metric_scope", sa.String(), nullable=False),
        sa.Column("metric_key", sa.String(), nullable=False),
        sa.Column("metric_value", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("site_id", sa.BigInteger(), nullable=True),
        sa.Column("segment_key", sa.String(), nullable=True),
        sa.Column("segment_value", sa.String(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["site_model_evaluations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_site_model_evaluation_metrics_evaluation_id",
        "site_model_evaluation_metrics",
        ["evaluation_id"],
        unique=False,
    )
    op.create_index(
        "ix_site_model_evaluation_metrics_metric_scope",
        "site_model_evaluation_metrics",
        ["metric_scope"],
        unique=False,
    )
    op.create_index(
        "ix_site_model_evaluation_metrics_metric_key",
        "site_model_evaluation_metrics",
        ["metric_key"],
        unique=False,
    )
    op.create_index(
        "ix_site_model_evaluation_metrics_site_id",
        "site_model_evaluation_metrics",
        ["site_id"],
        unique=False,
    )
    op.create_index(
        "ix_site_model_evaluation_metrics_segment_key",
        "site_model_evaluation_metrics",
        ["segment_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_site_model_evaluation_metrics_segment_key",
        table_name="site_model_evaluation_metrics",
    )
    op.drop_index(
        "ix_site_model_evaluation_metrics_site_id",
        table_name="site_model_evaluation_metrics",
    )
    op.drop_index(
        "ix_site_model_evaluation_metrics_metric_key",
        table_name="site_model_evaluation_metrics",
    )
    op.drop_index(
        "ix_site_model_evaluation_metrics_metric_scope",
        table_name="site_model_evaluation_metrics",
    )
    op.drop_index(
        "ix_site_model_evaluation_metrics_evaluation_id",
        table_name="site_model_evaluation_metrics",
    )
    op.drop_table("site_model_evaluation_metrics")
    op.drop_index(
        "ix_site_model_evaluations_model_key",
        table_name="site_model_evaluations",
    )
    op.drop_table("site_model_evaluations")

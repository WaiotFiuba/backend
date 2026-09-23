"""create site features table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-09 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_features",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.BigInteger(), nullable=False),
        sa.Column("feature_key", sa.String(), nullable=False),
        sa.Column("numeric_value", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("category_value", sa.String(), nullable=True),
        sa.Column("source", sa.String(), server_default="unknown", nullable=False),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "site_id",
            "feature_key",
            "source",
            name="uq_site_features_site_key_source",
        ),
    )
    op.create_index(
        "ix_site_features_feature_key",
        "site_features",
        ["feature_key"],
        unique=False,
    )
    op.create_index(
        "ix_site_features_site_id",
        "site_features",
        ["site_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_site_features_site_id", table_name="site_features")
    op.drop_index("ix_site_features_feature_key", table_name="site_features")
    op.drop_table("site_features")

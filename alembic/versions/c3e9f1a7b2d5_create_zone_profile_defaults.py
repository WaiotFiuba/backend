"""create zone_profile_defaults table

Revision ID: c3e9f1a7b2d5
Revises: d2f7a9c4e1b8
Create Date: 2026-10-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3e9f1a7b2d5"
down_revision: Union[str, Sequence[str], None] = "d2f7a9c4e1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zone_profile_defaults",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profiles", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("zone_profile_defaults")

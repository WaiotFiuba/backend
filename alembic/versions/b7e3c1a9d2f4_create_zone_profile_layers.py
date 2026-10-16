"""create zone_profile_layers table

Revision ID: b7e3c1a9d2f4
Revises: 1a2b3c4d5e6f
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7e3c1a9d2f4"
down_revision: Union[str, Sequence[str], None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zone_profile_layers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("simulation_id", sa.BigInteger(), nullable=True),
        sa.Column("geojson", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("zone_profile_layers")

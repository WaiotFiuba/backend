"""add container change cursor

Revision ID: e7a1b2c3d4e5
Revises: d4f07c8e6a12
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e7a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "d4f07c8e6a12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE container_change_version_seq")
    op.add_column(
        "containers",
        sa.Column(
            "change_version",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_containers_change_version",
        "containers",
        ["change_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_containers_change_version",
        table_name="containers",
    )
    op.drop_column("containers", "change_version")
    op.execute("DROP SEQUENCE container_change_version_seq")

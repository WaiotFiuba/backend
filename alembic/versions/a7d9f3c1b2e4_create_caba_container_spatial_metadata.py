"""create caba container spatial metadata

Revision ID: a7d9f3c1b2e4
Revises: 9c1f2f1a7b8d
Create Date: 2026-06-27 00:00:00.000000

"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "a7d9f3c1b2e4"
down_revision: Union[str, Sequence[str], None] = "9c1f2f1a7b8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: legacy CABA container spatial metadata table is deprecated and decoupled.
    pass


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trigger_container_geo_update ON containers;")
    op.execute("DROP FUNCTION IF EXISTS auto_assign_container_geo_info();")
    op.execute("DROP TABLE IF EXISTS caba_container_spatial_metadata CASCADE;")

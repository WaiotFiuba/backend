"""optimize containers for hot updates

Drop the ix_containers_change_version index and set fillfactor=70
on the containers table. This enables PostgreSQL HOT (Heap-Only Tuple)
updates, dramatically reducing WAL generation and I/O for the high-frequency
container level updates during simulation.

Revision ID: f6a7b8c9d0e1
Revises: f1a2b3c4d5e6
Create Date: 2026-09-24 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop the change_version index — it prevents HOT updates
    op.drop_index("ix_containers_change_version", table_name="containers")

    # 2. Set fillfactor=70 to leave 30% free space per page for HOT updates.
    #    Without this, pages are packed 100% full and HOT can't reuse space.
    #    The new fillfactor applies to future page writes. For existing pages,
    #    autovacuum will gradually reorganize them, or run VACUUM FULL manually:
    #      docker compose exec postgis psql -U waiot -d waiot_map -c "VACUUM FULL containers"
    op.execute("ALTER TABLE containers SET (fillfactor = 70)")


def downgrade() -> None:
    op.execute("ALTER TABLE containers RESET (fillfactor)")
    op.create_index(
        "ix_containers_change_version", "containers", ["change_version"]
    )

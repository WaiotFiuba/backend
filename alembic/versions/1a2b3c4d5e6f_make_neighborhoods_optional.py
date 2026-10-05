"""make neighborhoods optional and decouple from caba

Revision ID: 1a2b3c4d5e6f
Revises: f6a7b8c9d0e1
Create Date: 2026-09-28 00:00:00.000000

"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop trigger and function on containers
    op.execute("DROP TRIGGER IF EXISTS trigger_container_geo_update ON containers;")
    op.execute("DROP FUNCTION IF EXISTS auto_assign_container_geo_info();")

    # 2. Create generic neighborhoods table
    op.create_table(
        "neighborhoods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_neighborhoods_geom",
        "neighborhoods",
        ["geom"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_neighborhoods_name",
        "neighborhoods",
        ["name"],
        unique=False,
    )

    # 3. Add neighborhood_id to containers table (nullable)
    op.add_column(
        "containers",
        sa.Column("neighborhood_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_containers_neighborhood_id_neighborhoods",
        "containers",
        "neighborhoods",
        ["neighborhood_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_containers_neighborhood_id",
        "containers",
        ["neighborhood_id"],
        unique=False,
    )

    # 4. Migrate data if legacy CABA tables exist
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'barrios') THEN
                INSERT INTO neighborhoods (name, geom)
                SELECT nombre, geom FROM barrios
                ON CONFLICT DO NOTHING;

                IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'caba_container_spatial_metadata') THEN
                    UPDATE containers c
                    SET neighborhood_id = n.id
                    FROM caba_container_spatial_metadata m
                    JOIN barrios b ON b.id = m.barrio_id
                    JOIN neighborhoods n ON n.name = b.nombre
                    WHERE c.id = m.container_id;
                END IF;
            END IF;
        END $$;
        """
    )

    # 5. Drop legacy mirror table if exists
    op.execute("DROP TABLE IF EXISTS caba_container_spatial_metadata CASCADE;")


def downgrade() -> None:
    # Drop neighborhood foreign key, index, column
    op.drop_constraint(
        "fk_containers_neighborhood_id_neighborhoods",
        "containers",
        type_="foreignkey",
    )
    op.drop_index("ix_containers_neighborhood_id", table_name="containers")
    op.drop_column("containers", "neighborhood_id")

    # Drop neighborhoods table
    op.drop_index("ix_neighborhoods_name", table_name="neighborhoods")
    op.drop_index("idx_neighborhoods_geom", table_name="neighborhoods")
    op.drop_table("neighborhoods")

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
    op.create_table(
        "caba_container_spatial_metadata",
        sa.Column("container_id", sa.BigInteger(), nullable=False),
        sa.Column("comuna_id", sa.Integer(), nullable=True),
        sa.Column("barrio_id", sa.Integer(), nullable=True),
        sa.Column("manzana_id", sa.Integer(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="POINT",
                srid=4326,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["container_id"], ["containers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["comuna_id"], ["comunas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["barrio_id"], ["barrios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["manzana_id"], ["manzanas.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("container_id"),
    )
    op.create_index(
        "idx_caba_container_spatial_metadata_geom",
        "caba_container_spatial_metadata",
        ["geom"],
        unique=False,
        postgresql_using="gist",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION auto_assign_container_geo_info()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO caba_container_spatial_metadata (
                container_id,
                comuna_id,
                barrio_id,
                manzana_id,
                geom
            )
            VALUES (
                NEW.id,
                (
                    SELECT id
                    FROM comunas
                    WHERE ST_Within(NEW.geom, comunas.geom)
                    LIMIT 1
                ),
                (
                    SELECT id
                    FROM barrios
                    WHERE ST_Within(NEW.geom, barrios.geom)
                    LIMIT 1
                ),
                COALESCE(
                    (
                        SELECT id
                        FROM manzanas
                        WHERE ST_Within(NEW.geom, manzanas.geom)
                        ORDER BY manzanas.geom <-> NEW.geom
                        LIMIT 1
                    ),
                    (
                        SELECT id
                        FROM manzanas
                        ORDER BY manzanas.geom <-> NEW.geom
                        LIMIT 1
                    )
                ),
                NEW.geom
            )
            ON CONFLICT (container_id) DO UPDATE SET
                comuna_id = EXCLUDED.comuna_id,
                barrio_id = EXCLUDED.barrio_id,
                manzana_id = EXCLUDED.manzana_id,
                geom = EXCLUDED.geom;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trigger_container_geo_update ON containers;")
    op.execute(
        """
        CREATE TRIGGER trigger_container_geo_update
        AFTER INSERT OR UPDATE OF geom ON containers
        FOR EACH ROW
        EXECUTE FUNCTION auto_assign_container_geo_info();
        """
    )
    op.execute(
        """
        INSERT INTO caba_container_spatial_metadata (
            container_id,
            comuna_id,
            barrio_id,
            manzana_id,
            geom
        )
        SELECT
            c.id,
            (
                SELECT id
                FROM comunas
                WHERE ST_Within(c.geom, comunas.geom)
                LIMIT 1
            ),
            (
                SELECT id
                FROM barrios
                WHERE ST_Within(c.geom, barrios.geom)
                LIMIT 1
            ),
            COALESCE(
                (
                    SELECT id
                    FROM manzanas
                    WHERE ST_Within(c.geom, manzanas.geom)
                    LIMIT 1
                ),
                (
                    SELECT id
                    FROM manzanas
                    ORDER BY manzanas.geom <-> c.geom
                    LIMIT 1
                )
            ),
            c.geom
        FROM containers c
        ON CONFLICT (container_id) DO UPDATE SET
            comuna_id = EXCLUDED.comuna_id,
            barrio_id = EXCLUDED.barrio_id,
            manzana_id = EXCLUDED.manzana_id,
            geom = EXCLUDED.geom;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trigger_container_geo_update ON containers;")
    op.execute("DROP FUNCTION IF EXISTS auto_assign_container_geo_info();")
    op.drop_index(
        "idx_caba_container_spatial_metadata_geom",
        table_name="caba_container_spatial_metadata",
        postgresql_using="gist",
    )
    op.drop_table("caba_container_spatial_metadata")
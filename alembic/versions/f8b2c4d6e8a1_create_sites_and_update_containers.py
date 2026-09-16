"""create sites and update containers

Revision ID: f8b2c4d6e8a1
Revises: e7a1b2c3d4e5
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2


revision: str = "f8b2c4d6e8a1"
down_revision: Union[str, Sequence[str], None] = "e7a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create sites table
    op.create_table(
        "sites",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("latitude", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("longitude", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                nullable=False,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column("load_side_category", sa.String(), nullable=True),
        sa.Column("waste_type_id", sa.BigInteger(), nullable=True),
        sa.Column("client_id", sa.BigInteger(), nullable=True),
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
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["waste_type_id"], ["waste_types.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sites_geom", "sites", ["geom"], unique=False, postgresql_using="gist"
    )

    # 2. Alter containers.site_id: remove unique, drop NOT NULL first, change to BigInteger FK
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE containers DROP CONSTRAINT IF EXISTS containers_site_id_key"
        )
        op.execute("ALTER TABLE containers ALTER COLUMN site_id DROP NOT NULL")
        op.execute(
            "ALTER TABLE containers ALTER COLUMN site_id TYPE BIGINT USING NULL::bigint"
        )
        op.create_foreign_key(
            "fk_containers_site_id_sites",
            "containers",
            "sites",
            ["site_id"],
            ["id"],
        )
        op.create_index("ix_containers_site_id", "containers", ["site_id"])
    else:
        with op.batch_alter_table("containers") as batch_op:
            try:
                batch_op.drop_constraint("containers_site_id_key", type_="unique")
            except Exception:
                pass
            batch_op.alter_column(
                "site_id",
                existing_type=sa.String(),
                type_=sa.BigInteger(),
                existing_nullable=True,
                nullable=True,
            )
            batch_op.create_foreign_key(
                "fk_containers_site_id_sites",
                "sites",
                ["site_id"],
                ["id"],
            )
            batch_op.create_index("ix_containers_site_id", ["site_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("ix_containers_site_id", table_name="containers")
        op.drop_constraint(
            "fk_containers_site_id_sites", "containers", type_="foreignkey"
        )
        op.execute(
            "ALTER TABLE containers ALTER COLUMN site_id TYPE VARCHAR USING NULL::varchar"
        )
    else:
        with op.batch_alter_table("containers") as batch_op:
            batch_op.drop_index("ix_containers_site_id")
            batch_op.drop_constraint("fk_containers_site_id_sites", type_="foreignkey")
            batch_op.alter_column(
                "site_id",
                existing_type=sa.BigInteger(),
                type_=sa.String(),
                existing_nullable=True,
                nullable=True,
            )

    op.drop_index("idx_sites_geom", table_name="sites", postgresql_using="gist")
    op.drop_table("sites")

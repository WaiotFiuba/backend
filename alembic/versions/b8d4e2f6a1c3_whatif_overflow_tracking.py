"""track overflow in the what-if and allow a single active plan

Revision ID: b8d4e2f6a1c3
Revises: a1b2c3d4e5f6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8d4e2f6a1c3"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = "status = 'active_whatif'"


def upgrade() -> None:
    # El reparto de cada lectura entre los contenedores virtuales da fracciones:
    # redondear en cada lectura acumularía error y rompería la conservación.
    op.alter_column(
        "optimization_whatif_levels",
        "virtual_level",
        type_=sa.Float(),
        existing_type=sa.Integer(),
        existing_nullable=False,
        postgresql_using="virtual_level::double precision",
    )
    # Velocidad reciente de llenado del contenedor real (puntos por hora), para
    # estimar lo que siguió llegando mientras el sensor marca 100%.
    op.add_column(
        "optimization_whatif_levels",
        sa.Column("rate_ewma", sa.Float(), nullable=True),
    )
    # Horas al 100% del contenedor virtual y del real, medidas con la misma regla.
    op.add_column(
        "optimization_whatif_levels",
        sa.Column("overflow_hours", sa.Float(), server_default="0", nullable=False),
    )
    op.add_column(
        "optimization_whatif_levels",
        sa.Column(
            "real_overflow_hours", sa.Float(), server_default="0", nullable=False
        ),
    )

    # Un solo plan activo: si hubiera varios, queda el más reciente.
    op.execute(
        f"""
        UPDATE redistribution_plans
        SET status = 'completed'
        WHERE {ACTIVE}
          AND id <> (SELECT max(id) FROM redistribution_plans WHERE {ACTIVE})
        """
    )
    op.create_index(
        "uq_redistribution_plans_single_active",
        "redistribution_plans",
        ["status"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
        sqlite_where=sa.text(ACTIVE),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_redistribution_plans_single_active", table_name="redistribution_plans"
    )
    op.drop_column("optimization_whatif_levels", "real_overflow_hours")
    op.drop_column("optimization_whatif_levels", "overflow_hours")
    op.drop_column("optimization_whatif_levels", "rate_ewma")
    op.alter_column(
        "optimization_whatif_levels",
        "virtual_level",
        type_=sa.Integer(),
        existing_type=sa.Float(),
        existing_nullable=False,
        postgresql_using="round(virtual_level)::integer",
    )

"""track overflow in the what-if and allow a single active plan

Revision ID: b8d4e2f6a1c3
Revises: c7032ce67097
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8d4e2f6a1c3"
down_revision: str | Sequence[str] | None = "c7032ce67097"
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
    # Última vez que cambió el nivel virtual. Es distinta de last_reading (la
    # última lectura del sensor real): un contenedor que llega a un sitio cambia
    # con las lecturas de los otros contenedores de ese sitio.
    op.add_column(
        "optimization_whatif_levels",
        sa.Column("virtual_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
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

    # Promedios de sitios críticos y contenedores al 100 %, real y con el plan.
    op.create_table(
        "optimization_whatif_totals",
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("observed_hours", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "real_critical_site_hours", sa.Float(), server_default="0", nullable=False
        ),
        sa.Column(
            "critical_site_hours", sa.Float(), server_default="0", nullable=False
        ),
        sa.Column(
            "real_full_container_hours",
            sa.Float(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "full_container_hours", sa.Float(), server_default="0", nullable=False
        ),
        sa.Column("last_observed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_real_critical_sites", sa.Integer(), nullable=True),
        sa.Column("last_critical_sites", sa.Integer(), nullable=True),
        sa.Column("last_real_full_containers", sa.Integer(), nullable=True),
        sa.Column("last_full_containers", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["redistribution_plans.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("plan_id"),
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
    op.drop_table("optimization_whatif_totals")
    op.drop_index(
        "uq_redistribution_plans_single_active", table_name="redistribution_plans"
    )
    op.drop_column("optimization_whatif_levels", "real_overflow_hours")
    op.drop_column("optimization_whatif_levels", "overflow_hours")
    op.drop_column("optimization_whatif_levels", "rate_ewma")
    op.drop_column("optimization_whatif_levels", "virtual_updated_at")
    op.alter_column(
        "optimization_whatif_levels",
        "virtual_level",
        type_=sa.Integer(),
        existing_type=sa.Float(),
        existing_nullable=False,
        postgresql_using="round(virtual_level)::integer",
    )

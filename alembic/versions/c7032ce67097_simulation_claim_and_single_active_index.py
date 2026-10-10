"""simulation claim and single active index

Agrega simulation_sessions.claimed_at (cuando un simulador tomo la sesion) y
corrige el indice parcial de d4f07c8e6a12, que tenia 'pendinNDemogg' en lugar
de 'pending' y por eso no impedia dos sesiones en pending a la vez.

Revision ID: c7032ce67097
Revises: b7e3c1a9d2f4
Create Date: 2026-10-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7032ce67097"
down_revision: str | Sequence[str] | None = "b7e3c1a9d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = "('pending', 'running', 'paused', 'stopping')"


def upgrade() -> None:
    op.add_column(
        "simulation_sessions",
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Si quedo mas de una sesion activa, solo sigue la ultima: el indice nuevo
    # no se podria crear.
    op.execute(
        f"""
        UPDATE simulation_sessions
        SET status = 'failed',
            finished_at = now(),
            error_message = 'Cerrada al corregir el indice de sesion activa unica.'
        WHERE status IN {ACTIVE}
          AND id <> (SELECT max(id) FROM simulation_sessions WHERE status IN {ACTIVE})
        """
    )
    op.drop_index(
        "uq_simulation_sessions_single_active",
        table_name="simulation_sessions",
    )
    op.create_index(
        "uq_simulation_sessions_single_active",
        "simulation_sessions",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text(f"status IN {ACTIVE}"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_simulation_sessions_single_active",
        table_name="simulation_sessions",
    )
    # Definicion original, con el error.
    op.create_index(
        "uq_simulation_sessions_single_active",
        "simulation_sessions",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pendinNDemogg', 'running', 'paused', 'stopping')"
        ),
    )
    op.drop_column("simulation_sessions", "claimed_at")

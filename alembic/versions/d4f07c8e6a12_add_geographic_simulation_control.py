"""add geographic simulation control

Revision ID: d4f07c8e6a12
Revises: a7d9f3c1b2e4
Create Date: 2026-06-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4f07c8e6a12"
down_revision: Union[str, Sequence[str], None] = "a7d9f3c1b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_barrios = inspector.has_table("barrios")

    fk_constraints = []
    if has_barrios:
        fk_constraints.append(
            sa.ForeignKeyConstraint(
                ["neighborhood_id"], ["barrios.id"], ondelete="CASCADE"
            )
        )

    op.create_table(
        "neighborhood_demographics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("neighborhood_id", sa.Integer(), nullable=False),
        sa.Column("population", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("area_km2", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("density_per_km2", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("density_factor", sa.DOUBLE_PRECISION(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        *fk_constraints,
        sa.UniqueConstraint("neighborhood_id"),
    )
    op.create_table(
        "simulation_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("scenario", sa.JSON(), nullable=False),
        sa.Column("speedup", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("global_demand_current", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("global_demand_start", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("global_demand_target", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("transition_minutes", sa.Integer(), nullable=False),
        sa.Column("transition_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("transition_ends_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("simulated_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("current_period", sa.Integer(), nullable=False),
        sa.Column("total_periods", sa.Integer(), nullable=False),
        sa.Column("measurements_sent", sa.Integer(), nullable=False),
        sa.Column("collections_generated", sa.Integer(), nullable=False),
        sa.Column("alarms_generated", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_simulation_sessions_status", "simulation_sessions", ["status"])
    op.create_index(
        "uq_simulation_sessions_single_active",
        "simulation_sessions",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pendinNDemogg', 'running', 'paused', 'stopping')"
        ),
    )
    op.create_table(
        "simulation_zone_overrides",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("simulation_id", sa.BigInteger(), nullable=False),
        sa.Column("neighborhood", sa.String(), nullable=False),
        sa.Column("multiplier_current", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("multiplier_start", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("multiplier_target", sa.DOUBLE_PRECISION(), nullable=False),
        sa.Column("transition_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("transition_ends_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["simulation_id"], ["simulation_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("simulation_id", "neighborhood", name="uq_simulation_zone"),
    )


def downgrade() -> None:
    op.drop_table("simulation_zone_overrides")
    op.drop_index(
        "uq_simulation_sessions_single_active",
        table_name="simulation_sessions",
    )
    op.drop_index("idx_simulation_sessions_status", table_name="simulation_sessions")
    op.drop_table("simulation_sessions")
    op.drop_table("neighborhood_demographics")

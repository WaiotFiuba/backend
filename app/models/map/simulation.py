from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DOUBLE_PRECISION,
    ForeignKey,
    Integer,
    JSON,
    String,
    TIMESTAMP,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.map_database import MapBase
from app.core.simulation_status import SimulationStatus


class SimulationSession(MapBase):
    __tablename__ = "simulation_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=SimulationStatus.PENDING
    )
    scenario: Mapped[dict] = mapped_column(JSON, nullable=False)
    speedup: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False, default=60)
    global_demand_current: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
        default=1.0,
    )
    global_demand_start: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
        default=1.0,
    )
    global_demand_target: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
        default=1.0,
    )
    transition_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    transition_started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    transition_ends_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    simulated_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    current_period: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_periods: Mapped[int] = mapped_column(Integer, nullable=False)
    measurements_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collections_generated: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    alarms_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SimulationZoneOverride(MapBase):
    __tablename__ = "simulation_zone_overrides"
    __table_args__ = (
        UniqueConstraint("simulation_id", "neighborhood", name="uq_simulation_zone"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    simulation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("simulation_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    neighborhood: Mapped[str] = mapped_column(String, nullable=False)
    multiplier_current: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
        default=1.0,
    )
    multiplier_start: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
        default=1.0,
    )
    multiplier_target: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
        default=1.0,
    )
    transition_started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    transition_ends_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )

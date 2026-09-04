from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Integer,
    JSON,
    String,
    TIMESTAMP,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.map_database import MapBase


class RedistributionPlan(MapBase):
    """Persiste planes de redistribución generados por el módulo de optimización."""

    __tablename__ = "redistribution_plans"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="draft"
    )  # draft | active_whatif | completed
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    moves: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metrics_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    simulation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("simulation_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class OptimizationWhatIfLevel(MapBase):
    """
    Almacena niveles virtuales de contenedores bajo la distribución optimizada
    durante una simulación what-if. Cada fila mapea un contenedor a su sitio
    optimizado y mantiene su nivel virtual actualizado en paralelo con el real.
    """

    __tablename__ = "optimization_whatif_levels"

    plan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("redistribution_plans.id", ondelete="CASCADE"),
        primary_key=True,
    )
    container_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("containers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    original_site_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    optimized_site_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    virtual_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reading: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    TIMESTAMP,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.map_database import MapBase


class RedistributionPlan(MapBase):
    """Persiste planes de redistribución generados por el módulo de optimización."""

    __tablename__ = "redistribution_plans"
    # Un solo plan activo a la vez: lo garantiza la base.
    __table_args__ = (
        Index(
            "uq_redistribution_plans_single_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active_whatif'"),
            sqlite_where=text("status = 'active_whatif'"),
        ),
    )

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
    # Con decimales: el reparto de cada lectura entre contenedores da fracciones.
    virtual_level: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    # Última lectura del sensor real del contenedor.
    last_reading: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # Última vez que cambió el nivel virtual (puede cambiar con lecturas de otros
    # contenedores del sitio).
    virtual_updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    # Velocidad reciente de llenado del contenedor real (puntos por hora).
    rate_ewma: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Horas al 100% del contenedor virtual y del real.
    overflow_hours: Mapped[float] = mapped_column(
        Float, default=0, server_default="0", nullable=False
    )
    real_overflow_hours: Mapped[float] = mapped_column(
        Float, default=0, server_default="0", nullable=False
    )


class OptimizationWhatIfTotals(MapBase):
    """Acumulados de la red, real y con el plan, desde que se activó el plan.

    En cada conteo (como mucho cada pocos minutos de lecturas) se suman los
    sitios críticos y los contenedores al 100 % del conteo anterior por el tiempo
    transcurrido. Divididos por observed_hours dan el promedio del período.
    """

    __tablename__ = "optimization_whatif_totals"

    plan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("redistribution_plans.id", ondelete="CASCADE"),
        primary_key=True,
    )
    observed_hours: Mapped[float] = mapped_column(
        Float, default=0, server_default="0", nullable=False
    )
    real_critical_site_hours: Mapped[float] = mapped_column(
        Float, default=0, server_default="0", nullable=False
    )
    critical_site_hours: Mapped[float] = mapped_column(
        Float, default=0, server_default="0", nullable=False
    )
    real_full_container_hours: Mapped[float] = mapped_column(
        Float, default=0, server_default="0", nullable=False
    )
    full_container_hours: Mapped[float] = mapped_column(
        Float, default=0, server_default="0", nullable=False
    )
    # Último conteo: rige hasta el siguiente.
    last_observed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_real_critical_sites: Mapped[int | None] = mapped_column(Integer)
    last_critical_sites: Mapped[int | None] = mapped_column(Integer)
    last_real_full_containers: Mapped[int | None] = mapped_column(Integer)
    last_full_containers: Mapped[int | None] = mapped_column(Integer)

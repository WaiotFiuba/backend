from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DOUBLE_PRECISION,
    JSON,
    TIMESTAMP,
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.map_database import MapBase

if TYPE_CHECKING:
    from app.models.map.site import Site


class SiteProjectionRun(MapBase):
    """Corrida persistida de una proyeccion de niveles de sitios."""

    __tablename__ = "site_projection_runs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    model_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="completed", server_default="completed"
    )
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    horizon_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    critical_level: Mapped[int] = mapped_column(Integer, nullable=False)
    level_aggregation: Mapped[str] = mapped_column(String, nullable=False)
    lookback_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_at_full: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    site_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    points: Mapped[list[SiteProjectionPoint]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class SiteProjectionPoint(MapBase):
    """Punto futuro proyectado para un sitio dentro de una corrida."""

    __tablename__ = "site_projection_points"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "site_id",
            "timestamp",
            name="uq_site_projection_points_run_site_timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("site_projection_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    site_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, index=True
    )
    predicted_level: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    reaches_critical: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    reaches_full: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    confidence: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[SiteProjectionRun] = relationship(back_populates="points")
    site: Mapped[Site] = relationship()

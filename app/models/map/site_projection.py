from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DOUBLE_PRECISION,
    JSON,
    TIMESTAMP,
    BigInteger,
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


class SiteFeature(MapBase):
    """Feature generica asociada a un sitio para modelos de proyeccion."""

    __tablename__ = "site_features"
    __table_args__ = (
        UniqueConstraint(
            "site_id",
            "feature_key",
            "source",
            name="uq_site_features_site_key_source",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    site_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    numeric_value: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    category_value: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(
        String, nullable=False, default="unknown", server_default="unknown"
    )
    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    site: Mapped[Site] = relationship()


class SiteModelEvaluation(MapBase):
    """Corrida persistida de evaluacion/backtesting de modelos de proyeccion."""

    __tablename__ = "site_model_evaluations"

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
    site_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cutoff_start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    cutoff_end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
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

    metrics: Mapped[list[SiteModelEvaluationMetric]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )


class SiteModelEvaluationMetric(MapBase):
    """Metrica comparable de una evaluacion, global o segmentada."""

    __tablename__ = "site_model_evaluation_metrics"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    evaluation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("site_model_evaluations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric_scope: Mapped[str] = mapped_column(String, nullable=False, index=True)
    metric_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    metric_value: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    site_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    segment_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    segment_value: Mapped[str | None] = mapped_column(String, nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    evaluation: Mapped[SiteModelEvaluation] = relationship(back_populates="metrics")
    site: Mapped[Site | None] = relationship()

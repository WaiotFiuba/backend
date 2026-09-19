from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SiteProjectionModel(BaseModel):
    key: str = Field(description="Identificador estable usado como model_key.")
    name: str = Field(description="Nombre legible del modelo.")
    description: str = Field(description="Resumen corto de como proyecta niveles.")
    status: str = Field(description="Estado de disponibilidad del modelo.")
    requires_training: bool = Field(
        description="Indica si el modelo necesita entrenamiento previo."
    )
    supports_site_features: bool = Field(
        description="Indica si puede consumir features genericas del sitio."
    )

    model_config = ConfigDict(from_attributes=True)


class SiteProjectionModelsResponse(BaseModel):
    default_model_key: str
    models: list[SiteProjectionModel]


class SiteProjectionRunRequest(BaseModel):
    site_ids: list[int] | None = Field(
        default=None,
        description="Sitios a proyectar. En v1 es requerido para evitar corridas masivas accidentales.",
    )
    model_key: str = "baseline_operational"
    horizon_hours: int = Field(default=24, ge=1, le=168)
    interval_minutes: int = Field(default=60, ge=15, le=1440)
    critical_level: int = Field(default=80, ge=1, le=100)
    level_aggregation: Literal["avg", "max"] = "avg"
    lookback_days: int = Field(default=14, ge=1, le=365)
    stop_at_full: bool = True


class SiteProjectionPoint(BaseModel):
    timestamp: datetime
    predicted_level: float
    lower_bound: float | None = None
    upper_bound: float | None = None
    reaches_critical: bool = False
    reaches_full: bool = False
    confidence: float
    reason: str | None = None


class SiteProjectionResponse(BaseModel):
    site_id: int
    model_key: str
    generated_at: datetime
    current_level: float
    horizon_hours: int
    interval_minutes: int
    critical_level: int
    level_aggregation: Literal["avg", "max"]
    lookback_days: int
    stop_at_full: bool
    confidence: float
    reason: str | None = None
    critical_at: datetime | None = None
    time_to_critical_hours: float | None = None
    full_at: datetime | None = None
    time_to_full_hours: float | None = None
    points: list[SiteProjectionPoint] = Field(default_factory=list)


class SiteProjectionRunResponse(BaseModel):
    id: int
    model_key: str
    status: str
    generated_at: datetime
    completed_at: datetime | None = None
    horizon_hours: int
    interval_minutes: int
    critical_level: int
    level_aggregation: Literal["avg", "max"]
    lookback_days: int
    stop_at_full: bool
    site_count: int
    summary: dict | None = None
    sites: list[SiteProjectionResponse] = Field(default_factory=list)


class SiteProjectionEvaluationRequest(BaseModel):
    site_ids: list[int] | None = Field(
        default=None, description="Sitios a evaluar contra historico observado."
    )
    site_sample_size: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description=(
            "Cantidad de sitios random a evaluar cuando no se envian site_ids. "
            "La muestra se toma entre sitios con mediciones en la ventana evaluable."
        ),
    )
    cutoff: datetime = Field(
        description="Momento historico donde se corta la serie para iniciar el backtesting."
    )
    model_key: str = "baseline_operational"
    horizon_hours: int = Field(default=24, ge=1, le=168)
    interval_minutes: int = Field(default=60, ge=15, le=1440)
    critical_level: int = Field(default=80, ge=1, le=100)
    level_aggregation: Literal["avg", "max"] = "avg"
    lookback_days: int = Field(default=14, ge=1, le=365)
    stop_at_full: bool = False


class SiteProjectionEvaluationMetricResponse(BaseModel):
    id: int
    metric_scope: str
    metric_key: str
    metric_value: float | None = None
    site_id: int | None = None
    segment_key: str | None = None
    segment_value: str | None = None
    sample_count: int | None = None
    metadata_json: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class SiteProjectionEvaluationResponse(BaseModel):
    id: int
    model_key: str
    status: str
    cutoff: datetime
    completed_at: datetime | None = None
    horizon_hours: int
    interval_minutes: int
    critical_level: int
    level_aggregation: Literal["avg", "max"]
    lookback_days: int
    site_count: int
    summary: dict | None = None
    metrics: list[SiteProjectionEvaluationMetricResponse] = Field(default_factory=list)


class SiteProjectionEvaluationSiteReport(BaseModel):
    site_id: int
    sample_count: int | None = None
    mae: float | None = None
    rmse: float | None = None
    critical_time_error_hours: float | None = None
    threshold_precision: float | None = None
    threshold_recall: float | None = None


class SiteProjectionEvaluationReportResponse(BaseModel):
    id: int
    model_key: str
    status: str
    completed_at: datetime | None = None
    summary: dict | None = None
    global_metrics: dict[str, float | None] = Field(default_factory=dict)
    site_metrics: list[SiteProjectionEvaluationSiteReport] = Field(default_factory=list)
    best_predicted_sites: list[SiteProjectionEvaluationSiteReport] = Field(
        default_factory=list
    )
    worst_predicted_sites: list[SiteProjectionEvaluationSiteReport] = Field(
        default_factory=list
    )

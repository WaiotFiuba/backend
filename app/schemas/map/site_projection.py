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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ForecastPoint:
    timestamp: datetime
    predicted_level: float
    confidence: float
    reason: str | None = None


@dataclass(frozen=True)
class ForecastRequest:
    site_id: int
    horizon_hours: int = 24
    interval_minutes: int = 60
    critical_level: int = 80
    level_aggregation: str = "avg"
    lookback_days: int = 14
    stop_at_full: bool = True


@dataclass(frozen=True)
class ForecastResult:
    site_id: int
    model_key: str
    generated_at: datetime
    points: list[ForecastPoint]


@dataclass(frozen=True)
class ForecastModelInfo:
    key: str
    name: str
    description: str
    status: str
    requires_training: bool
    supports_site_features: bool


class SiteLevelForecaster(Protocol):
    model_info: ForecastModelInfo

    async def forecast(self, request: ForecastRequest) -> ForecastResult:
        """Project future site levels.

        Concrete implementations will receive richer historical context in the
        next tasks. The protocol exists now so routers and services can depend
        on a stable contract instead of concrete model classes.
        """


BASELINE_OPERATIONAL_MODEL = ForecastModelInfo(
    key="baseline_operational",
    name="Baseline operativo",
    description=(
        "Modelo interpretable basado en incrementos historicos por intervalo, "
        "patrones horarios/semanales, ciclos de recoleccion y features genericas."
    ),
    status="planned",
    requires_training=False,
    supports_site_features=True,
)

DEFAULT_MODEL_KEY = BASELINE_OPERATIONAL_MODEL.key

_MODEL_REGISTRY: dict[str, ForecastModelInfo] = {
    BASELINE_OPERATIONAL_MODEL.key: BASELINE_OPERATIONAL_MODEL,
}


def list_projection_models() -> list[ForecastModelInfo]:
    return list(_MODEL_REGISTRY.values())


def get_projection_model_info(model_key: str) -> ForecastModelInfo | None:
    return _MODEL_REGISTRY.get(model_key)

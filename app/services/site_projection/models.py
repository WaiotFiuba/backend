from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.services.site_projection.history import SiteLevelBucket


@dataclass(frozen=True)
class ForecastPoint:
    timestamp: datetime
    predicted_level: float
    confidence: float
    lower_bound: float | None = None
    upper_bound: float | None = None
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
class ForecastContext:
    """Datos ya preparados para que el modelo no dependa de la base ni de CABA."""

    current_level: float
    history: list[SiteLevelBucket]
    site_features: dict[str, float | str]
    generated_at: datetime | None = None


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

    async def forecast(
        self,
        request: ForecastRequest,
        context: ForecastContext,
    ) -> ForecastResult:
        """Project future site levels.

        Implementations receive historical buckets and generic site features
        through ForecastContext, keeping the API independent from a city schema.
        """

    async def forecast_many(
        self,
        inputs: list[tuple[ForecastRequest, ForecastContext]],
    ) -> dict[int, ForecastResult]: ...


DEFAULT_MODEL_KEY = "baseline_operational"

_MODEL_REGISTRY: dict[str, SiteLevelForecaster] = {}


def _get_registry() -> dict[str, SiteLevelForecaster]:
    if DEFAULT_MODEL_KEY not in _MODEL_REGISTRY:
        from app.services.site_projection.baseline import BaselineOperationalForecaster

        _MODEL_REGISTRY[DEFAULT_MODEL_KEY] = BaselineOperationalForecaster()
    if "chronos_2_small" not in _MODEL_REGISTRY:
        try:
            from app.services.site_projection.chronos import (
                CHRONOS_2_SMALL_MODEL,
                Chronos2SmallForecaster,
            )

            _MODEL_REGISTRY[CHRONOS_2_SMALL_MODEL.key] = Chronos2SmallForecaster()
        except Exception:  # noqa: BLE001, S110
            pass
    return _MODEL_REGISTRY


def list_projection_models() -> list[ForecastModelInfo]:
    return [forecaster.model_info for forecaster in _get_registry().values()]


def get_projection_model_info(model_key: str) -> ForecastModelInfo | None:
    forecaster = _get_registry().get(model_key)
    if forecaster is None:
        return None
    return forecaster.model_info


def get_forecaster(model_key: str) -> SiteLevelForecaster | None:
    return _get_registry().get(model_key)

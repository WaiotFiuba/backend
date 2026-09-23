from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Protocol

from app.services.map.site_collection_cycle_service import detect_collection_event
from app.services.map.site_projection_history_service import SiteLevelBucket


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


BASELINE_OPERATIONAL_MODEL = ForecastModelInfo(
    key="baseline_operational",
    name="Baseline operativo",
    description=(
        "Modelo interpretable basado en incrementos historicos por intervalo, "
        "patrones horarios/semanales, ciclos de recoleccion y features genericas."
    ),
    status="available",
    requires_training=False,
    supports_site_features=True,
)

DEFAULT_MODEL_KEY = BASELINE_OPERATIONAL_MODEL.key


class BaselineOperationalForecaster:
    """Baseline interpretable: proyecta sumando incrementos historicos esperados."""

    model_info = BASELINE_OPERATIONAL_MODEL

    async def forecast(
        self,
        request: ForecastRequest,
        context: ForecastContext,
    ) -> ForecastResult:
        generated_at = _as_utc(context.generated_at or datetime.now(UTC))
        history = sorted(context.history, key=lambda bucket: bucket.timestamp)

        # Primero convierto la serie historica en "cuanto suele subir" el sitio.
        # Las bajadas por recoleccion no son demanda, por eso se filtran aparte.
        increment_profile = _build_increment_profile(history)
        confidence, reason = _confidence(history, increment_profile.global_increments)

        # Si tengo historico, parto del ultimo nivel observado. Si no, uso el
        # nivel actual que venga del estado operativo del sitio.
        current_level = _clamp_level(context.current_level)
        if history:
            current_level = _clamp_level(history[-1].level)

        # Las features territoriales solo corrigen el fallback. Si el sitio ya
        # tiene suficiente historico propio, prefiero creerle a su serie real.
        density_factor = _numeric_feature(
            context.site_features, "demand.density_factor"
        )
        feature_adjustment = _feature_adjustment(
            density_factor=density_factor,
            has_enough_site_history=len(increment_profile.global_increments) >= 6,
        )

        points: list[ForecastPoint] = []
        steps = max(1, int(request.horizon_hours * 60 / request.interval_minutes))
        next_timestamp = _ceil_to_next_interval(generated_at, request.interval_minutes)

        for step in range(steps):
            timestamp = next_timestamp + timedelta(
                minutes=request.interval_minutes * step
            )
            # Para cada punto futuro busco el patron mas especifico disponible:
            # mismo dia+hora, misma hora, mediana global o fallback conservador.
            increment = increment_profile.increment_for(timestamp)
            current_level = _clamp_level(current_level + increment * feature_adjustment)
            margin = _uncertainty_margin(confidence)
            points.append(
                ForecastPoint(
                    timestamp=timestamp,
                    predicted_level=round(current_level, 2),
                    confidence=confidence,
                    lower_bound=round(_clamp_level(current_level - margin), 2),
                    upper_bound=round(_clamp_level(current_level + margin), 2),
                    reason=reason,
                )
            )
            if request.stop_at_full and current_level >= 100:
                break

        return ForecastResult(
            site_id=request.site_id,
            model_key=self.model_info.key,
            generated_at=generated_at,
            points=points,
        )


@dataclass(frozen=True)
class _IncrementProfile:
    """Resumen de incrementos historicos en distintos niveles de especificidad."""

    global_increments: list[float]
    hourly_increments: dict[int, list[float]]
    weekly_hourly_increments: dict[tuple[int, int], list[float]]
    fallback_increment: float

    def increment_for(self, timestamp: datetime) -> float:
        # La mediana es intencional: reduce el peso de picos raros de sensores o
        # cargas excepcionales sin tener que entrenar un modelo pesado.
        weekday_hour = (timestamp.weekday(), timestamp.hour)
        candidates = self.weekly_hourly_increments.get(weekday_hour, [])
        if len(candidates) >= 2:
            return statistics.median(candidates)

        candidates = self.hourly_increments.get(timestamp.hour, [])
        if len(candidates) >= 2:
            return statistics.median(candidates)

        if self.global_increments:
            return statistics.median(self.global_increments)

        return self.fallback_increment


def _build_increment_profile(history: list[SiteLevelBucket]) -> _IncrementProfile:
    """Extrae demanda historica como incrementos positivos entre buckets."""

    global_increments: list[float] = []
    hourly_increments: dict[int, list[float]] = {}
    weekly_hourly_increments: dict[tuple[int, int], list[float]] = {}

    ordered = sorted(history, key=lambda bucket: bucket.timestamp)
    for previous, current in pairwise(ordered):
        # Una recoleccion baja el nivel, pero no significa menor generacion de
        # residuos. La saco del perfil para no enseñarle una pendiente falsa.
        if detect_collection_event(current, previous) is not None:
            continue

        increment = round(current.level - previous.level, 4)
        if increment <= 0:
            continue

        global_increments.append(increment)
        hourly_increments.setdefault(current.timestamp.hour, []).append(increment)
        weekly_hourly_increments.setdefault(
            (current.timestamp.weekday(), current.timestamp.hour), []
        ).append(increment)

    return _IncrementProfile(
        global_increments=global_increments,
        hourly_increments=hourly_increments,
        weekly_hourly_increments=weekly_hourly_increments,
        fallback_increment=1.0,
    )


def _confidence(
    history: list[SiteLevelBucket],
    increments: list[float],
) -> tuple[float, str | None]:
    # Esta confianza es operativa, no estadistica estricta: mide cuanta evidencia
    # local hubo para construir el perfil que estamos usando.
    if len(history) >= 48 and len(increments) >= 12:
        return 0.85, None
    if len(history) >= 12 and len(increments) >= 4:
        return 0.65, "limited_history"
    return 0.35, "fallback_due_to_low_history"


def _feature_adjustment(
    density_factor: float | None,
    has_enough_site_history: bool,
) -> float:
    # La densidad ayuda cuando arrancamos con poco historico. La acoto para que
    # un dato territorial ruidoso no domine completamente la proyeccion.
    if density_factor is None or has_enough_site_history:
        return 1.0
    return min(1.5, max(0.75, density_factor))


def _numeric_feature(
    features: dict[str, float | str],
    key: str,
) -> float | None:
    value = features.get(key)
    if isinstance(value, int | float):
        return float(value)
    return None


def _ceil_to_next_interval(timestamp: datetime, interval_minutes: int) -> datetime:
    # Las predicciones salen alineadas a buckets fijos: 08:00, 09:00, etc.
    timestamp = _as_utc(timestamp).replace(second=0, microsecond=0)
    minute_of_day = timestamp.hour * 60 + timestamp.minute
    remainder = minute_of_day % interval_minutes
    if remainder == 0:
        return timestamp + timedelta(minutes=interval_minutes)
    return timestamp + timedelta(minutes=interval_minutes - remainder)


def _uncertainty_margin(confidence: float) -> float:
    # Banda simple para graficar incertidumbre; se puede reemplazar por intervalos
    # calibrados cuando tengamos evaluaciones persistidas.
    if confidence >= 0.8:
        return 8.0
    if confidence >= 0.6:
        return 15.0
    return 25.0


def _clamp_level(value: float) -> float:
    return min(100.0, max(0.0, value))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


_MODEL_REGISTRY: dict[str, SiteLevelForecaster] = {
    BASELINE_OPERATIONAL_MODEL.key: BaselineOperationalForecaster(),
}


def list_projection_models() -> list[ForecastModelInfo]:
    return [forecaster.model_info for forecaster in _MODEL_REGISTRY.values()]


def get_projection_model_info(model_key: str) -> ForecastModelInfo | None:
    forecaster = _MODEL_REGISTRY.get(model_key)
    if forecaster is None:
        return None
    return forecaster.model_info


def get_forecaster(model_key: str) -> SiteLevelForecaster | None:
    return _MODEL_REGISTRY.get(model_key)

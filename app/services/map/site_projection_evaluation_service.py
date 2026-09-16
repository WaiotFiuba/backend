from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.services.map.site_projection_history_service import SiteLevelBucket
from app.services.map.site_projection_models import (
    DEFAULT_MODEL_KEY,
    ForecastContext,
    ForecastRequest,
    get_forecaster,
)


@dataclass(frozen=True)
class SiteBacktestRequest:
    site_id: int
    cutoff: datetime
    horizon_hours: int = 24
    interval_minutes: int = 60
    critical_level: int = 80
    level_aggregation: str = "avg"
    lookback_days: int = 14
    stop_at_full: bool = False
    model_key: str = DEFAULT_MODEL_KEY


@dataclass(frozen=True)
class SiteBacktestPoint:
    timestamp: datetime
    predicted_level: float
    observed_level: float
    absolute_error: float
    squared_error: float
    predicted_reaches_critical: bool
    observed_reaches_critical: bool


@dataclass(frozen=True)
class ThresholdMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float | None
    recall: float | None


@dataclass(frozen=True)
class SiteBacktestResult:
    site_id: int
    model_key: str
    cutoff: datetime
    horizon_hours: int
    interval_minutes: int
    compared_points: int
    mae: float | None
    rmse: float | None
    predicted_critical_at: datetime | None
    observed_critical_at: datetime | None
    critical_time_error_hours: float | None
    threshold_metrics: ThresholdMetrics
    points: list[SiteBacktestPoint]


async def evaluate_site_forecast_backtest(
    history: list[SiteLevelBucket],
    request: SiteBacktestRequest,
    site_features: dict[str, float | str] | None = None,
) -> SiteBacktestResult:
    """Evalua un modelo cortando la serie en un punto pasado.

    La funcion no sabe nada de la base de datos: recibe buckets agregados,
    entrena/proyecta con lo que existia hasta cutoff y compara contra los
    buckets reales posteriores. Eso la hace reutilizable para endpoints, jobs
    batch y tests unitarios.
    """

    forecaster = get_forecaster(request.model_key)
    if forecaster is None:
        raise ValueError(f"Modelo de proyeccion desconocido: {request.model_key}")

    ordered_history = sorted(history, key=lambda bucket: bucket.timestamp)
    training_history = [
        bucket for bucket in ordered_history if bucket.timestamp <= request.cutoff
    ]
    observed_history = [
        bucket
        for bucket in ordered_history
        if request.cutoff < bucket.timestamp <= _horizon_end(request)
    ]

    current_level = training_history[-1].level if training_history else 0.0
    forecast = await forecaster.forecast(
        ForecastRequest(
            site_id=request.site_id,
            horizon_hours=request.horizon_hours,
            interval_minutes=request.interval_minutes,
            critical_level=request.critical_level,
            level_aggregation=request.level_aggregation,
            lookback_days=request.lookback_days,
            stop_at_full=request.stop_at_full,
        ),
        ForecastContext(
            current_level=current_level,
            history=training_history,
            site_features=site_features or {},
            generated_at=request.cutoff,
        ),
    )

    observed_by_timestamp = {bucket.timestamp: bucket for bucket in observed_history}
    compared_points = [
        _compare_point(
            timestamp=point.timestamp,
            predicted_level=point.predicted_level,
            observed_level=observed_by_timestamp[point.timestamp].level,
            critical_level=request.critical_level,
        )
        for point in forecast.points
        if point.timestamp in observed_by_timestamp
    ]

    predicted_critical_at = _first_predicted_critical_at(
        forecast.points,
        request.critical_level,
    )
    observed_critical_at = _first_observed_critical_at(
        observed_history,
        request.critical_level,
    )

    return SiteBacktestResult(
        site_id=request.site_id,
        model_key=forecast.model_key,
        cutoff=request.cutoff,
        horizon_hours=request.horizon_hours,
        interval_minutes=request.interval_minutes,
        compared_points=len(compared_points),
        mae=_mae(compared_points),
        rmse=_rmse(compared_points),
        predicted_critical_at=predicted_critical_at,
        observed_critical_at=observed_critical_at,
        critical_time_error_hours=_critical_time_error_hours(
            predicted_critical_at,
            observed_critical_at,
        ),
        threshold_metrics=_threshold_metrics(compared_points),
        points=compared_points,
    )


def _horizon_end(request: SiteBacktestRequest) -> datetime:
    from datetime import timedelta

    return request.cutoff + timedelta(hours=request.horizon_hours)


def _compare_point(
    timestamp: datetime,
    predicted_level: float,
    observed_level: float,
    critical_level: int,
) -> SiteBacktestPoint:
    absolute_error = abs(predicted_level - observed_level)
    return SiteBacktestPoint(
        timestamp=timestamp,
        predicted_level=predicted_level,
        observed_level=observed_level,
        absolute_error=round(absolute_error, 4),
        squared_error=round(absolute_error**2, 4),
        predicted_reaches_critical=predicted_level >= critical_level,
        observed_reaches_critical=observed_level >= critical_level,
    )


def _mae(points: list[SiteBacktestPoint]) -> float | None:
    if not points:
        return None
    return round(sum(point.absolute_error for point in points) / len(points), 4)


def _rmse(points: list[SiteBacktestPoint]) -> float | None:
    if not points:
        return None
    mean_squared_error = sum(point.squared_error for point in points) / len(points)
    return round(math.sqrt(mean_squared_error), 4)


def _first_predicted_critical_at(points, critical_level: int) -> datetime | None:
    for point in points:
        if point.predicted_level >= critical_level:
            return point.timestamp
    return None


def _first_observed_critical_at(
    buckets: list[SiteLevelBucket],
    critical_level: int,
) -> datetime | None:
    for bucket in buckets:
        if bucket.level >= critical_level:
            return bucket.timestamp
    return None


def _critical_time_error_hours(
    predicted_at: datetime | None,
    observed_at: datetime | None,
) -> float | None:
    if predicted_at is None or observed_at is None:
        return None
    return round((predicted_at - observed_at).total_seconds() / 3600, 4)


def _threshold_metrics(points: list[SiteBacktestPoint]) -> ThresholdMetrics:
    true_positives = sum(
        1
        for point in points
        if point.predicted_reaches_critical and point.observed_reaches_critical
    )
    false_positives = sum(
        1
        for point in points
        if point.predicted_reaches_critical and not point.observed_reaches_critical
    )
    false_negatives = sum(
        1
        for point in points
        if not point.predicted_reaches_critical and point.observed_reaches_critical
    )
    true_negatives = sum(
        1
        for point in points
        if not point.predicted_reaches_critical and not point.observed_reaches_critical
    )
    return ThresholdMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        true_negatives=true_negatives,
        precision=_safe_divide(true_positives, true_positives + false_positives),
        recall=_safe_divide(true_positives, true_positives + false_negatives),
    )


def _safe_divide(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)

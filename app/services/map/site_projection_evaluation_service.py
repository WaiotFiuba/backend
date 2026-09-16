from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.site_projection import (
    SiteFeature,
    SiteModelEvaluation,
    SiteModelEvaluationMetric,
)
from app.schemas.map.site_projection import (
    SiteProjectionEvaluationMetricResponse,
    SiteProjectionEvaluationRequest,
    SiteProjectionEvaluationResponse,
)
from app.services.map.site_projection_history_service import (
    SiteLevelBucket,
    get_site_level_series,
)
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
    # Backtesting clasico: el modelo solo ve lo que existia hasta el cutoff.
    # Todo lo posterior queda reservado como "verdad observada" para medir error.
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

    # Comparo por timestamp exacto porque las proyecciones salen en buckets fijos
    # y el historico ya fue agregado con el mismo interval_minutes.
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


async def create_site_projection_evaluation(
    db: AsyncSession,
    request: SiteProjectionEvaluationRequest,
) -> SiteProjectionEvaluationResponse:
    if not request.site_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="site_ids es requerido para ejecutar una evaluacion.",
        )

    unique_site_ids = sorted(set(request.site_ids))
    cutoff = _as_utc(request.cutoff)
    # Necesito traer historico antes del cutoff para construir el perfil del
    # modelo, y tambien despues del cutoff para tener observaciones contra las
    # cuales comparar. Por eso la ventana suma lookback + horizonte.
    history_by_site = await get_site_level_series(
        db=db,
        site_ids=unique_site_ids,
        interval_minutes=request.interval_minutes,
        level_aggregation=request.level_aggregation,
        lookback_days=_evaluation_window_days(request),
        end_time=cutoff + timedelta(hours=request.horizon_hours),
    )
    features_by_site = await _get_features_by_site(db, unique_site_ids)

    # La v1 evalua sitios uno por uno. Mantener este loop simple nos deja
    # agregar segmentacion o ejecucion paralela despues sin cambiar el contrato.
    results = [
        await evaluate_site_forecast_backtest(
            history=history_by_site.get(site_id, []),
            request=SiteBacktestRequest(
                site_id=site_id,
                cutoff=cutoff,
                horizon_hours=request.horizon_hours,
                interval_minutes=request.interval_minutes,
                critical_level=request.critical_level,
                level_aggregation=request.level_aggregation,
                lookback_days=request.lookback_days,
                stop_at_full=request.stop_at_full,
                model_key=request.model_key,
            ),
            site_features=features_by_site.get(site_id, {}),
        )
        for site_id in unique_site_ids
    ]

    completed_at = datetime.now(UTC)
    evaluation = SiteModelEvaluation(
        model_key=request.model_key,
        status="completed",
        config=request.model_dump(mode="json"),
        horizon_hours=request.horizon_hours,
        interval_minutes=request.interval_minutes,
        critical_level=request.critical_level,
        level_aggregation=request.level_aggregation,
        lookback_days=request.lookback_days,
        site_count=len(unique_site_ids),
        cutoff_start_at=cutoff,
        cutoff_end_at=cutoff,
        summary=_build_evaluation_summary(results),
        completed_at=completed_at,
    )
    db.add(evaluation)
    await db.flush()

    # Separo cabecera y metricas: la cabecera describe la corrida; las metricas
    # quedan en filas comparables para poder consultar global/site/segmento.
    metric_models = _metrics_from_results(evaluation.id, results)
    db.add_all(metric_models)
    await db.commit()
    await db.refresh(evaluation)

    return _evaluation_response(evaluation, metric_models, cutoff)


async def get_site_projection_evaluation(
    db: AsyncSession,
    evaluation_id: int,
) -> SiteProjectionEvaluationResponse:
    evaluation = await db.get(SiteModelEvaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluacion de modelo no encontrada.",
        )

    result = await db.execute(
        select(SiteModelEvaluationMetric)
        .where(SiteModelEvaluationMetric.evaluation_id == evaluation_id)
        .order_by(
            SiteModelEvaluationMetric.metric_scope,
            SiteModelEvaluationMetric.site_id,
            SiteModelEvaluationMetric.metric_key,
        )
    )
    metrics = list(result.scalars().all())
    cutoff = _parse_cutoff_from_config(evaluation.config) or _as_utc(
        evaluation.cutoff_start_at or evaluation.created_at
    )
    return _evaluation_response(evaluation, metrics, cutoff)


def _evaluation_window_days(request: SiteProjectionEvaluationRequest) -> int:
    # get_site_level_series recibe dias enteros. Redondeo el horizonte hacia
    # arriba para no perder observaciones cuando el horizonte no cae justo en 24h.
    horizon_days = max(1, math.ceil(request.horizon_hours / 24))
    return request.lookback_days + horizon_days


async def _get_features_by_site(
    db: AsyncSession,
    site_ids: list[int],
) -> dict[int, dict[str, float | str]]:
    """Carga features genericas sin acoplar el evaluador a CABA u otra ciudad."""

    result = await db.execute(
        select(
            SiteFeature.site_id,
            SiteFeature.feature_key,
            SiteFeature.numeric_value,
            SiteFeature.category_value,
        ).where(SiteFeature.site_id.in_(site_ids))
    )
    features_by_site: dict[int, dict[str, float | str]] = {
        site_id: {} for site_id in site_ids
    }
    for row in result.all():
        value = (
            float(row.numeric_value)
            if row.numeric_value is not None
            else row.category_value
        )
        if value is not None:
            features_by_site.setdefault(int(row.site_id), {})[row.feature_key] = value
    return features_by_site


def _build_evaluation_summary(results: list[SiteBacktestResult]) -> dict:
    """Resumen compacto para mostrar una evaluacion sin leer todas sus metricas."""

    compared_points = sum(result.compared_points for result in results)
    return {
        "site_count": len(results),
        "compared_points": compared_points,
        "mae": _weighted_average(
            [(result.mae, result.compared_points) for result in results]
        ),
        "rmse": _weighted_average(
            [(result.rmse, result.compared_points) for result in results]
        ),
        "sites_with_observed_critical": sum(
            1 for result in results if result.observed_critical_at is not None
        ),
        "sites_with_predicted_critical": sum(
            1 for result in results if result.predicted_critical_at is not None
        ),
    }


def _metrics_from_results(
    evaluation_id: int,
    results: list[SiteBacktestResult],
) -> list[SiteModelEvaluationMetric]:
    """Convierte resultados en filas metricas persistibles.

    Guardo metricas globales y por sitio con el mismo formato para que despues
    podamos sumar segmentos por residuo, barrio o densidad sin crear tablas nuevas.
    """

    metrics: list[SiteModelEvaluationMetric] = []
    summary = _build_evaluation_summary(results)
    for metric_key in ("mae", "rmse"):
        metrics.append(
            SiteModelEvaluationMetric(
                evaluation_id=evaluation_id,
                metric_scope="global",
                metric_key=metric_key,
                metric_value=summary.get(metric_key),
                sample_count=summary["compared_points"],
            )
        )

    aggregate_threshold = _aggregate_threshold_metrics(results)
    for metric_key, metric_value in aggregate_threshold.items():
        metrics.append(
            SiteModelEvaluationMetric(
                evaluation_id=evaluation_id,
                metric_scope="global",
                metric_key=metric_key,
                metric_value=metric_value,
                sample_count=summary["compared_points"],
            )
        )

    for result in results:
        site_metrics = {
            "mae": result.mae,
            "rmse": result.rmse,
            "critical_time_error_hours": result.critical_time_error_hours,
            "threshold_precision": result.threshold_metrics.precision,
            "threshold_recall": result.threshold_metrics.recall,
        }
        for metric_key, metric_value in site_metrics.items():
            metrics.append(
                SiteModelEvaluationMetric(
                    evaluation_id=evaluation_id,
                    metric_scope="site",
                    metric_key=metric_key,
                    metric_value=metric_value,
                    site_id=result.site_id,
                    sample_count=result.compared_points,
                    metadata_json={
                        "cutoff": result.cutoff.isoformat(),
                        "predicted_critical_at": (
                            result.predicted_critical_at.isoformat()
                            if result.predicted_critical_at
                            else None
                        ),
                        "observed_critical_at": (
                            result.observed_critical_at.isoformat()
                            if result.observed_critical_at
                            else None
                        ),
                    },
                )
            )
    return metrics


def _aggregate_threshold_metrics(
    results: list[SiteBacktestResult],
) -> dict[str, float | None]:
    # Para precision/recall global no promediamos porcentajes por sitio:
    # sumamos la matriz de confusion completa y recien ahi calculamos ratios.
    true_positives = sum(result.threshold_metrics.true_positives for result in results)
    false_positives = sum(
        result.threshold_metrics.false_positives for result in results
    )
    false_negatives = sum(
        result.threshold_metrics.false_negatives for result in results
    )
    true_negatives = sum(result.threshold_metrics.true_negatives for result in results)
    return {
        "threshold_true_positives": float(true_positives),
        "threshold_false_positives": float(false_positives),
        "threshold_false_negatives": float(false_negatives),
        "threshold_true_negatives": float(true_negatives),
        "threshold_precision": _safe_divide(
            true_positives, true_positives + false_positives
        ),
        "threshold_recall": _safe_divide(
            true_positives, true_positives + false_negatives
        ),
    }


def _weighted_average(values: list[tuple[float | None, int]]) -> float | None:
    # MAE/RMSE global se ponderan por puntos comparados. Un sitio con 2 puntos no
    # deberia pesar igual que uno con 100 observaciones validas.
    weighted_values = [
        (value, weight) for value, weight in values if value is not None and weight > 0
    ]
    total_weight = sum(weight for _, weight in weighted_values)
    if total_weight == 0:
        return None
    return round(
        sum(value * weight for value, weight in weighted_values) / total_weight,
        4,
    )


def _evaluation_response(
    evaluation: SiteModelEvaluation,
    metrics: list[SiteModelEvaluationMetric],
    cutoff: datetime,
) -> SiteProjectionEvaluationResponse:
    # Mantengo la traduccion ORM -> schema en un solo lugar para que POST y GET
    # devuelvan exactamente la misma forma.
    return SiteProjectionEvaluationResponse(
        id=evaluation.id,
        model_key=evaluation.model_key,
        status=evaluation.status,
        cutoff=cutoff,
        completed_at=evaluation.completed_at,
        horizon_hours=evaluation.horizon_hours,
        interval_minutes=evaluation.interval_minutes,
        critical_level=evaluation.critical_level,
        level_aggregation=evaluation.level_aggregation,
        lookback_days=evaluation.lookback_days or 0,
        site_count=evaluation.site_count,
        summary=evaluation.summary,
        metrics=[
            SiteProjectionEvaluationMetricResponse.model_validate(metric)
            for metric in metrics
        ],
    )


def _parse_cutoff_from_config(config: dict | None) -> datetime | None:
    if not config:
        return None
    cutoff = config.get("cutoff")
    if not isinstance(cutoff, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(cutoff))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

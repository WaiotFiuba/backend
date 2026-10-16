from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import MapSessionLocal
from app.core.thresholds import get_level_thresholds
from app.models.map.container import Container
from app.models.map.data_level import DataLevel
from app.models.map.site import Site
from app.models.map.site_projection import (
    SiteFeature,
    SiteModelEvaluation,
    SiteModelEvaluationMetric,
)
from app.schemas.map.site_projection import (
    SiteProjectionEvaluationMetricResponse,
    SiteProjectionEvaluationReportResponse,
    SiteProjectionEvaluationRequest,
    SiteProjectionEvaluationResponse,
    SiteProjectionEvaluationSiteReport,
)
from app.services.site_projection.generation import (
    first_crossing,
    uncollected_levels,
)
from app.services.site_projection.history import (
    SiteLevelBucket,
    aggregate_site_measurements,
    fetch_site_measurements,
    floor_to_interval,
)
from app.services.site_projection.models import (
    DEFAULT_MODEL_KEY,
    ForecastContext,
    ForecastRequest,
    ForecastResult,
    SiteLevelForecaster,
    get_forecaster,
)

EVALUATION_INTERVAL_MINUTES = 60
EVALUATION_BATCH_SIZE = 100
# Origen fijo desde el que se mide el resto por separacion: mismos datos, mismos cortes.
CUTOFF_ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)
logger = logging.getLogger(__name__)
_evaluation_lock = asyncio.Lock()


@dataclass(frozen=True)
class SiteBacktestRequest:
    site_id: int
    cutoff: datetime
    horizon_hours: int = 24
    interval_minutes: int = 60
    critical_level: int = get_level_thresholds().critical
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
    # Cuando hubiera llegado al umbral si no lo vaciaban: la verdad de los eventos.
    uncollected_critical_at: datetime | None = None
    uncollected_time_error_hours: float | None = None


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

    forecast_request, context, observed_history = _prepare_backtest(
        history, request, site_features
    )
    forecast = await forecaster.forecast(forecast_request, context)
    return _score_backtest(request, forecast, observed_history, context.history)


def _prepare_backtest(
    history: list[SiteLevelBucket],
    request: SiteBacktestRequest,
    site_features: dict[str, float | str] | None = None,
) -> tuple[ForecastRequest, ForecastContext, list[SiteLevelBucket]]:
    ordered_history = sorted(history, key=lambda bucket: bucket.timestamp)
    # Backtesting clasico: el modelo solo ve lo que existia hasta el cutoff.
    # Todo lo posterior queda reservado como "verdad observada" para medir error.
    history_start = request.cutoff - timedelta(days=request.lookback_days)
    training_history = [
        bucket
        for bucket in ordered_history
        if history_start < bucket.timestamp <= request.cutoff
    ]
    observed_history = [
        bucket
        for bucket in ordered_history
        if request.cutoff < bucket.timestamp <= _horizon_end(request)
    ]

    current_level = training_history[-1].level if training_history else 0.0
    # Uso el mismo contrato que usa produccion para proyectar. Asi evaluamos el
    # modelo real y no una version especial preparada solo para tests/reportes.
    return (
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
        observed_history,
    )


def _score_backtest(
    request: SiteBacktestRequest,
    forecast: ForecastResult,
    observed_history: list[SiteLevelBucket],
    training_history: list[SiteLevelBucket] | None = None,
) -> SiteBacktestResult:
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
    uncollected_critical_at = first_crossing(
        uncollected_levels(training_history or [], observed_history),
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
        uncollected_critical_at=uncollected_critical_at,
        uncollected_time_error_hours=_critical_time_error_hours(
            predicted_critical_at,
            uncollected_critical_at,
        ),
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
    if not request.site_ids and request.site_sample_size is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe enviarse site_ids o site_sample_size.",
        )
    if get_forecaster(request.model_key) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Modelo de proyeccion desconocido: {request.model_key}",
        )
    evaluation = SiteModelEvaluation(
        model_key=request.model_key,
        status="pending",
        config=request.model_dump(mode="json"),
        horizon_hours=request.horizon_hours,
        interval_minutes=EVALUATION_INTERVAL_MINUTES,
        critical_level=request.critical_level,
        level_aggregation=request.level_aggregation,
        lookback_days=request.lookback_days,
        site_count=0,
    )
    db.add(evaluation)
    await db.commit()
    await db.refresh(evaluation)
    return _evaluation_response(evaluation, [], None)


async def run_site_projection_evaluation(
    evaluation_id: int,
    request: SiteProjectionEvaluationRequest,
) -> None:
    async with _evaluation_lock:
        try:
            async with MapSessionLocal() as db:
                evaluation = await db.get(SiteModelEvaluation, evaluation_id)
                if evaluation is None:
                    return
                evaluation.status = "running"
                await db.commit()

                cutoffs, short_history = await _resolve_cutoffs(db, request)
                site_ids = await _resolve_evaluation_site_ids(db, request, cutoffs)
                evaluation.site_count = len(site_ids)
                evaluation.cutoff_start_at = cutoffs[0]
                evaluation.cutoff_end_at = cutoffs[-1]
                evaluation.summary = {"processed_sites": 0}
                await db.commit()

            results: list[SiteBacktestResult] = []
            forecaster = get_forecaster(request.model_key)
            if forecaster is None:
                raise ValueError(
                    f"Modelo de proyeccion desconocido: {request.model_key}"
                )
            for start in range(0, len(site_ids), EVALUATION_BATCH_SIZE):
                batch_ids = site_ids[start : start + EVALUATION_BATCH_SIZE]
                async with MapSessionLocal() as db:
                    samples = await fetch_site_measurements(
                        db,
                        batch_ids,
                        lookback_days=_evaluation_window_days(request, cutoffs),
                        end_time=cutoffs[-1] + timedelta(hours=request.horizon_hours),
                    )
                    features_by_site = await _get_features_by_site(db, batch_ids)

                history_by_site = await asyncio.to_thread(
                    aggregate_site_measurements,
                    samples,
                    interval_minutes=EVALUATION_INTERVAL_MINUTES,
                    level_aggregation=request.level_aggregation,
                )
                # Una lectura por tanda de sitios; cada corte se pronostica en lote.
                for cutoff in cutoffs:
                    results.extend(
                        await _evaluate_backtest_batch(
                            batch_ids,
                            history_by_site,
                            features_by_site,
                            cutoff,
                            request,
                            forecaster,
                        )
                    )
                async with MapSessionLocal() as db:
                    evaluation = await db.get(SiteModelEvaluation, evaluation_id)
                    if evaluation is None:
                        return
                    evaluation.summary = {"processed_sites": start + len(batch_ids)}
                    await db.commit()

            async with MapSessionLocal() as db:
                evaluation = await db.get(SiteModelEvaluation, evaluation_id)
                if evaluation is None:
                    return
                config = dict(evaluation.config)
                config.update(
                    cutoff=cutoffs[-1].isoformat(),
                    cutoffs=[cutoff.isoformat() for cutoff in cutoffs],
                    interval_minutes=EVALUATION_INTERVAL_MINUTES,
                    cutoff_strategy="anchored_rolling",
                    selected_site_ids=site_ids,
                )
                evaluation.config = config
                evaluation.status = "completed"
                evaluation.site_count = len(site_ids)
                evaluation.cutoff_start_at = cutoffs[0]
                evaluation.cutoff_end_at = cutoffs[-1]
                evaluation.summary = {
                    **_build_evaluation_summary(
                        results,
                        request.critical_early_hours,
                        request.critical_late_hours,
                    ),
                    "short_history": short_history,
                }
                evaluation.completed_at = datetime.now(UTC)
                db.add_all(
                    _metrics_from_results(
                        evaluation_id,
                        results,
                        request.critical_early_hours,
                        request.critical_late_hours,
                    )
                )
                await db.commit()
        except Exception as exc:
            logger.exception("Fallo la evaluacion de proyecciones %s", evaluation_id)
            async with MapSessionLocal() as db:
                evaluation = await db.get(SiteModelEvaluation, evaluation_id)
                if evaluation is not None:
                    evaluation.status = "failed"
                    error = (
                        str(exc.detail)
                        if isinstance(exc, HTTPException)
                        else "No se pudo completar la evaluacion. Revisa los logs del servidor."
                    )
                    evaluation.summary = {"error": error}
                    evaluation.completed_at = datetime.now(UTC)
                    await db.commit()


async def _evaluate_backtest_batch(
    site_ids: list[int],
    history_by_site: dict[int, list[SiteLevelBucket]],
    features_by_site: dict[int, dict[str, float | str]],
    cutoff: datetime,
    request: SiteProjectionEvaluationRequest,
    forecaster: SiteLevelForecaster,
) -> list[SiteBacktestResult]:
    prepared = []
    for site_id in site_ids:
        backtest = SiteBacktestRequest(
            site_id=site_id,
            cutoff=cutoff,
            horizon_hours=request.horizon_hours,
            interval_minutes=EVALUATION_INTERVAL_MINUTES,
            critical_level=request.critical_level,
            level_aggregation=request.level_aggregation,
            lookback_days=request.lookback_days,
            stop_at_full=request.stop_at_full,
            model_key=request.model_key,
        )
        forecast_request, context, observed = _prepare_backtest(
            history_by_site.get(site_id, []),
            backtest,
            features_by_site.get(site_id, {}),
        )
        prepared.append((backtest, forecast_request, context, observed))
    forecasts = await forecaster.forecast_many(
        [(forecast_request, context) for _, forecast_request, context, _ in prepared]
    )
    return [
        replace(
            _score_backtest(
                backtest, forecasts[backtest.site_id], observed, context.history
            ),
            points=[],
        )
        for backtest, _, context, observed in prepared
    ]


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
    cutoff = _parse_cutoff_from_config(evaluation.config)
    if cutoff is None and evaluation.cutoff_start_at is not None:
        cutoff = _as_utc(evaluation.cutoff_start_at)
    return _evaluation_response(evaluation, metrics, cutoff)


async def get_site_projection_evaluation_status(
    db: AsyncSession,
    evaluation_id: int,
) -> SiteProjectionEvaluationResponse:
    evaluation = await db.get(SiteModelEvaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluacion de modelo no encontrada.",
        )
    cutoff = _parse_cutoff_from_config(evaluation.config)
    if cutoff is None and evaluation.cutoff_start_at is not None:
        cutoff = _as_utc(evaluation.cutoff_start_at)
    return _evaluation_response(evaluation, [], cutoff)


async def get_site_projection_evaluation_report(
    db: AsyncSession,
    evaluation_id: int,
) -> SiteProjectionEvaluationReportResponse:
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
    return _evaluation_report(evaluation, list(result.scalars().all()))


async def _reading_range(db: AsyncSession) -> tuple[datetime, datetime]:
    result = await db.execute(
        select(
            func.min(DataLevel.reading_date),
            func.max(DataLevel.reading_date),
        ).where(DataLevel.container_current_level.is_not(None))
    )
    earliest_reading, latest_reading = result.one()
    if earliest_reading is None or latest_reading is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No hay mediciones disponibles para ejecutar la evaluacion.",
        )
    return _as_utc(earliest_reading), _as_utc(latest_reading)


async def _resolve_cutoffs(
    db: AsyncSession,
    request: SiteProjectionEvaluationRequest,
) -> tuple[list[datetime], bool]:
    """Los cortes a evaluar y si la historia es corta. Si ningun corte llega a
    min_history_days, se evalua solo el ultimo, como antes."""
    earliest_reading, latest_reading = await _reading_range(db)
    latest_cutoff = _latest_cutoff(earliest_reading, latest_reading, request)
    cutoffs = rolling_cutoffs(latest_cutoff, earliest_reading, request)
    if not cutoffs:
        return [latest_cutoff], True
    return cutoffs, False


def rolling_cutoffs(
    latest_cutoff: datetime,
    earliest_reading: datetime,
    request: SiteProjectionEvaluationRequest,
) -> list[datetime]:
    """Cortes cada cutoff_spacing_hours hacia atras desde el ultimo punto de la
    grilla que no pasa a latest_cutoff, del mas viejo al mas nuevo, sin los que
    tienen menos de min_history_days de historia."""
    spacing = timedelta(hours=request.cutoff_spacing_hours)
    anchored = CUTOFF_ANCHOR + (latest_cutoff - CUTOFF_ANCHOR) // spacing * spacing
    min_history = timedelta(days=request.min_history_days)
    cutoffs = [anchored - spacing * index for index in range(request.cutoff_count)]
    return sorted(c for c in cutoffs if c - earliest_reading >= min_history)


async def _resolve_automatic_cutoff(
    db: AsyncSession,
    request: SiteProjectionEvaluationRequest,
) -> datetime:
    return _latest_cutoff(*await _reading_range(db), request)


def _latest_cutoff(
    earliest_reading: datetime,
    latest_reading: datetime,
    request: SiteProjectionEvaluationRequest,
) -> datetime:
    # El usuario no deberia tener que conocer el rango exacto del historico.
    # Tomo la ultima medicion disponible y retrocedo el horizonte solicitado:
    # asi queda una ventana futura real para comparar contra la prediccion.
    cutoff = floor_to_interval(
        latest_reading - timedelta(hours=request.horizon_hours),
        EVALUATION_INTERVAL_MINUTES,
    )
    if cutoff < earliest_reading or cutoff >= latest_reading:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No hay suficientes mediciones para el horizonte solicitado. "
                "Ejecuta una simulacion mas larga o reduce horizon_hours."
            ),
        )
    return cutoff


def _evaluation_window_days(
    request: SiteProjectionEvaluationRequest, cutoffs: list[datetime]
) -> int:
    # fetch_site_measurements recibe dias enteros: redondeo hacia arriba para
    # cubrir la historia del primer corte y el horizonte del ultimo.
    span = cutoffs[-1] - cutoffs[0] + timedelta(hours=request.horizon_hours)
    return request.lookback_days + math.ceil(span / timedelta(days=1))


async def _resolve_evaluation_site_ids(
    db: AsyncSession,
    request: SiteProjectionEvaluationRequest,
    cutoffs: list[datetime],
) -> list[int]:
    if request.site_ids:
        return sorted(set(request.site_ids))
    if request.site_sample_size is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe enviarse site_ids o site_sample_size.",
        )

    # Para que la evaluacion tenga valor, la muestra random sale de sitios que
    # realmente tienen mediciones dentro de la ventana usada por el backtesting.
    start_time = cutoffs[0] - timedelta(days=request.lookback_days)
    end_time = cutoffs[-1] + timedelta(hours=request.horizon_hours)
    before_cutoff_count = func.count(DataLevel.id).filter(
        DataLevel.reading_date <= cutoffs[0]
    )
    after_cutoff_count = func.count(DataLevel.id).filter(
        DataLevel.reading_date > cutoffs[-1]
    )
    stmt = (
        select(Site.id)
        .join(Container, Container.site_id == Site.id)
        .join(DataLevel, DataLevel.container_id == Container.id)
        .where(
            Site.deleted_at.is_(None),
            Container.deleted_at.is_(None),
            DataLevel.container_current_level.is_not(None),
            DataLevel.reading_date >= start_time,
            DataLevel.reading_date <= end_time,
        )
        .group_by(Site.id)
        .having(before_cutoff_count > 0)
        .having(after_cutoff_count > 0)
        .order_by(func.random())
        .limit(request.site_sample_size)
    )
    result = await db.execute(stmt)
    site_ids = [int(site_id) for site_id in result.scalars().all()]
    if not site_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No hay sitios con mediciones en la ventana de evaluacion.",
        )
    return sorted(site_ids)


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


def _build_evaluation_summary(
    results: list[SiteBacktestResult], early_hours: int = 3, late_hours: int = 1
) -> dict:
    """Resumen compacto para mostrar una evaluacion sin leer todas sus metricas."""

    compared_points = sum(result.compared_points for result in results)
    events = event_metrics(results, early_hours, late_hours)
    return {
        "site_count": len({result.site_id for result in results}),
        "cutoff_count": len({result.cutoff for result in results}),
        "case_count": len(results),
        "events_observed": events["event_observed"],
        "event_precision": events["event_precision"],
        "event_recall": events["event_recall"],
        "event_f1": events["event_f1"],
        "compared_points": compared_points,
        "mae": _weighted_average(
            [(result.mae, result.compared_points) for result in results]
        ),
        "rmse": _weighted_average(
            [(result.rmse, result.compared_points) for result in results]
        ),
        "sites_with_observed_critical": len(
            {r.site_id for r in results if r.observed_critical_at is not None}
        ),
        "sites_with_predicted_critical": len(
            {r.site_id for r in results if r.predicted_critical_at is not None}
        ),
    }


def event_metrics(
    results: list[SiteBacktestResult], early_hours: int = 3, late_hours: int = 1
) -> dict[str, float | None]:
    """Un caso por sitio y corte: si llega al umbral en el horizonte si no lo
    vacian. Acierta si avisa entre early_hours antes y late_hours despues del
    cruce sin recoleccion; antes es una visita de mas (falsa alarma), despues un
    desborde no anticipado (cruce perdido)."""
    tp = fp = fn = tn = early = late = 0
    for r in results:
        predicted = r.predicted_critical_at is not None
        observed = r.uncollected_critical_at is not None
        if predicted and observed:
            if r.uncollected_time_error_hours < -early_hours:
                early += 1
            elif r.uncollected_time_error_hours > late_hours:
                late += 1
            else:
                tp += 1
        elif predicted:
            fp += 1
        elif observed:
            fn += 1
        else:
            tn += 1
    errors = [
        r.uncollected_time_error_hours
        for r in results
        if r.uncollected_time_error_hours is not None
    ]
    detected = tp + early + late
    precision = _safe_divide(tp, tp + fp + early)
    recall = _safe_divide(tp, tp + fn + late)
    return {
        "event_true_positives": float(tp),
        "event_false_positives": float(fp + early),
        "event_false_negatives": float(fn + late),
        "event_true_negatives": float(tn),
        "event_too_early": float(early),
        "event_too_late": float(late),
        "event_observed": float(detected + fn),
        "event_prevalence": _safe_divide(detected + fn, len(results)),
        "event_precision": precision,
        "event_recall": recall,
        "event_f1": _f1(precision, recall),
        # Sin mirar la hora: solo si avisa y si cruza.
        "event_horizon_f1": _f1(
            _safe_divide(detected, detected + fp),
            _safe_divide(detected, detected + fn),
        ),
        "critical_time_mae_hours": (
            round(sum(abs(e) for e in errors) / len(errors), 4) if errors else None
        ),
        "critical_time_bias_hours": (
            round(sum(errors) / len(errors), 4) if errors else None
        ),
    }


def _f1(precision: float | None, recall: float | None) -> float | None:
    if not precision or not recall:
        return None
    return round(2 * precision * recall / (precision + recall), 4)


def _metrics_from_results(
    evaluation_id: int,
    results: list[SiteBacktestResult],
    early_hours: int = 3,
    late_hours: int = 1,
) -> list[SiteModelEvaluationMetric]:
    """Convierte resultados en filas metricas persistibles.

    Guardo metricas globales y por sitio con el mismo formato para que despues
    podamos sumar segmentos por residuo, barrio o densidad sin crear tablas nuevas.
    """

    metrics: list[SiteModelEvaluationMetric] = []
    summary = _build_evaluation_summary(results, early_hours, late_hours)
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
    for metric_key, metric_value in event_metrics(
        results, early_hours, late_hours
    ).items():
        metrics.append(
            SiteModelEvaluationMetric(
                evaluation_id=evaluation_id,
                metric_scope="global",
                metric_key=metric_key,
                metric_value=metric_value,
                sample_count=len(results),
            )
        )

    for result in _merge_site_results(results):
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


def _merge_site_results(results: list[SiteBacktestResult]) -> list[SiteBacktestResult]:
    """Una fila por sitio aunque se haya evaluado en varios cortes."""
    by_site: dict[int, list[SiteBacktestResult]] = {}
    for result in results:
        by_site.setdefault(result.site_id, []).append(result)
    return [_merge_site(site_results) for site_results in by_site.values()]


def _merge_site(results: list[SiteBacktestResult]) -> SiteBacktestResult:
    if len(results) == 1:
        return results[0]
    weighted = [(r.mae, r.compared_points) for r in results]
    squared = [(r.rmse**2, r.compared_points) for r in results if r.rmse is not None]
    mean_squared = _weighted_average(squared)
    errors = [
        abs(r.critical_time_error_hours)
        for r in results
        if r.critical_time_error_hours is not None
    ]
    thresholds = [r.threshold_metrics for r in results]
    true_positives = sum(t.true_positives for t in thresholds)
    false_positives = sum(t.false_positives for t in thresholds)
    false_negatives = sum(t.false_negatives for t in thresholds)
    last = max(results, key=lambda result: result.cutoff)
    return replace(
        last,
        compared_points=sum(r.compared_points for r in results),
        mae=_weighted_average(weighted),
        rmse=round(math.sqrt(mean_squared), 4) if mean_squared is not None else None,
        predicted_critical_at=None,
        observed_critical_at=None,
        uncollected_critical_at=None,
        uncollected_time_error_hours=None,
        critical_time_error_hours=(
            round(sum(errors) / len(errors), 4) if errors else None
        ),
        threshold_metrics=ThresholdMetrics(
            true_positives=true_positives,
            false_positives=false_positives,
            false_negatives=false_negatives,
            true_negatives=sum(t.true_negatives for t in thresholds),
            precision=_safe_divide(true_positives, true_positives + false_positives),
            recall=_safe_divide(true_positives, true_positives + false_negatives),
        ),
        points=[],
    )


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
    cutoff: datetime | None,
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


def _evaluation_report(
    evaluation: SiteModelEvaluation,
    metrics: list[SiteModelEvaluationMetric],
) -> SiteProjectionEvaluationReportResponse:
    # Este reporte es una vista de lectura: no recalcula nada, solo acomoda las
    # metricas persistidas para comparar rapidamente modelos y detectar outliers.
    global_metrics = {
        metric.metric_key: metric.metric_value
        for metric in metrics
        if metric.metric_scope == "global"
    }
    site_metrics = _site_report_metrics(metrics)
    ranked_sites = [site for site in site_metrics if site.mae is not None]
    ranked_sites.sort(key=lambda site: site.mae or 0)

    return SiteProjectionEvaluationReportResponse(
        id=evaluation.id,
        model_key=evaluation.model_key,
        status=evaluation.status,
        completed_at=evaluation.completed_at,
        summary=evaluation.summary,
        global_metrics=global_metrics,
        site_metrics=site_metrics,
        best_predicted_sites=ranked_sites[:10],
        worst_predicted_sites=list(reversed(ranked_sites[-10:])),
    )


def _site_report_metrics(
    metrics: list[SiteModelEvaluationMetric],
) -> list[SiteProjectionEvaluationSiteReport]:
    grouped: dict[int, dict[str, float | int | None]] = {}
    for metric in metrics:
        if metric.metric_scope != "site" or metric.site_id is None:
            continue
        site_data = grouped.setdefault(
            metric.site_id,
            {"site_id": metric.site_id, "sample_count": metric.sample_count},
        )
        site_data[metric.metric_key] = metric.metric_value
        if site_data.get("sample_count") is None:
            site_data["sample_count"] = metric.sample_count

    return [
        SiteProjectionEvaluationSiteReport(
            site_id=int(site_data["site_id"]),
            sample_count=(
                int(site_data["sample_count"])
                if site_data.get("sample_count") is not None
                else None
            ),
            mae=_optional_float(site_data.get("mae")),
            rmse=_optional_float(site_data.get("rmse")),
            critical_time_error_hours=_optional_float(
                site_data.get("critical_time_error_hours")
            ),
            threshold_precision=_optional_float(site_data.get("threshold_precision")),
            threshold_recall=_optional_float(site_data.get("threshold_recall")),
        )
        for site_data in grouped.values()
    ]


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


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

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.site import Site
from app.models.map.site_projection import (
    SiteFeature,
    SiteProjectionRun,
)
from app.models.map.site_projection import (
    SiteProjectionPoint as SiteProjectionPointModel,
)
from app.schemas.map.site_projection import (
    SiteProjectionPoint,
    SiteProjectionResponse,
    SiteProjectionRunRequest,
    SiteProjectionRunResponse,
)
from app.services.map.site_projection_history_service import get_site_level_series
from app.services.map.site_projection_models import (
    DEFAULT_MODEL_KEY,
    ForecastContext,
    ForecastRequest,
    get_forecaster,
)
from app.services.simulation_control_service import get_active_simulation_session

LevelAggregation = Literal["avg", "max"]


async def project_site_level(
    db: AsyncSession,
    site_id: int | str,
    model_key: str = DEFAULT_MODEL_KEY,
    horizon_hours: int = 24,
    interval_minutes: int = 60,
    critical_level: int = 80,
    level_aggregation: LevelAggregation = "avg",
    lookback_days: int = 14,
    stop_at_full: bool = True,
) -> SiteProjectionResponse:
    numeric_site_id = _parse_site_id(site_id)
    current_level = await _get_current_site_level(
        db,
        site_id=numeric_site_id,
        level_aggregation=level_aggregation,
    )
    generated_at = await _projection_clock(db)
    history_by_site = await get_site_level_series(
        db=db,
        site_ids=[numeric_site_id],
        interval_minutes=interval_minutes,
        level_aggregation=level_aggregation,
        lookback_days=lookback_days,
        end_time=generated_at,
    )
    features = await _get_site_features(db, numeric_site_id)
    forecaster = get_forecaster(model_key)
    if forecaster is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Modelo de proyeccion desconocido: {model_key}",
        )

    request = ForecastRequest(
        site_id=numeric_site_id,
        horizon_hours=horizon_hours,
        interval_minutes=interval_minutes,
        critical_level=critical_level,
        level_aggregation=level_aggregation,
        lookback_days=lookback_days,
        stop_at_full=stop_at_full,
    )
    result = await forecaster.forecast(
        request,
        ForecastContext(
            current_level=current_level,
            history=history_by_site.get(numeric_site_id, []),
            site_features=features,
            generated_at=generated_at,
        ),
    )

    points = [
        SiteProjectionPoint(
            timestamp=point.timestamp,
            predicted_level=point.predicted_level,
            lower_bound=point.lower_bound,
            upper_bound=point.upper_bound,
            reaches_critical=point.predicted_level >= critical_level,
            reaches_full=point.predicted_level >= 100,
            confidence=point.confidence,
            reason=point.reason,
        )
        for point in result.points
    ]
    critical_at = _first_timestamp_at_or_above(points, critical_level)
    full_at = _first_timestamp_at_or_above(points, 100)

    return SiteProjectionResponse(
        site_id=numeric_site_id,
        model_key=result.model_key,
        generated_at=result.generated_at,
        current_level=current_level,
        horizon_hours=horizon_hours,
        interval_minutes=interval_minutes,
        critical_level=critical_level,
        level_aggregation=level_aggregation,
        lookback_days=lookback_days,
        stop_at_full=stop_at_full,
        confidence=points[0].confidence if points else 0.0,
        reason=points[0].reason if points else "no_projection_points",
        critical_at=critical_at,
        time_to_critical_hours=_hours_between(result.generated_at, critical_at),
        full_at=full_at,
        time_to_full_hours=_hours_between(result.generated_at, full_at),
        points=points,
    )


async def create_site_projection_run(
    db: AsyncSession,
    request: SiteProjectionRunRequest,
) -> SiteProjectionRunResponse:
    if not request.site_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="site_ids es requerido en v1 para evitar corridas masivas accidentales.",
        )

    unique_site_ids = sorted(set(request.site_ids))
    projections: list[SiteProjectionResponse] = []
    for site_id in unique_site_ids:
        projections.append(
            await project_site_level(
                db=db,
                site_id=site_id,
                model_key=request.model_key,
                horizon_hours=request.horizon_hours,
                interval_minutes=request.interval_minutes,
                critical_level=request.critical_level,
                level_aggregation=request.level_aggregation,
                lookback_days=request.lookback_days,
                stop_at_full=request.stop_at_full,
            )
        )

    generated_at = projections[0].generated_at if projections else datetime.now(UTC)
    completed_at = datetime.now(UTC)
    run = SiteProjectionRun(
        model_key=request.model_key,
        status="completed",
        config=request.model_dump(),
        horizon_hours=request.horizon_hours,
        interval_minutes=request.interval_minutes,
        critical_level=request.critical_level,
        level_aggregation=request.level_aggregation,
        lookback_days=request.lookback_days,
        stop_at_full=request.stop_at_full,
        site_count=len(projections),
        summary=_build_run_summary(projections),
        completed_at=completed_at,
    )
    db.add(run)
    await db.flush()

    db.add_all(
        SiteProjectionPointModel(
            run_id=run.id,
            site_id=projection.site_id,
            timestamp=point.timestamp,
            predicted_level=point.predicted_level,
            reaches_critical=point.reaches_critical,
            reaches_full=point.reaches_full,
            confidence=point.confidence,
            metadata_json={
                "lower_bound": point.lower_bound,
                "upper_bound": point.upper_bound,
                "reason": point.reason,
                "generated_at": projection.generated_at.isoformat(),
                "current_level": projection.current_level,
            },
        )
        for projection in projections
        for point in projection.points
    )
    await db.commit()
    await db.refresh(run)

    return SiteProjectionRunResponse(
        id=run.id,
        model_key=run.model_key,
        status=run.status,
        generated_at=generated_at,
        completed_at=run.completed_at,
        horizon_hours=run.horizon_hours,
        interval_minutes=run.interval_minutes,
        critical_level=run.critical_level,
        level_aggregation=run.level_aggregation,
        lookback_days=run.lookback_days or request.lookback_days,
        stop_at_full=run.stop_at_full,
        site_count=run.site_count,
        summary=run.summary,
        sites=projections,
    )


async def get_site_projection_run(
    db: AsyncSession,
    run_id: int,
) -> SiteProjectionRunResponse:
    run = await db.get(SiteProjectionRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Corrida de proyeccion no encontrada.",
        )

    result = await db.execute(
        select(SiteProjectionPointModel)
        .where(SiteProjectionPointModel.run_id == run_id)
        .order_by(SiteProjectionPointModel.site_id, SiteProjectionPointModel.timestamp)
    )
    points_by_site: dict[int, list[SiteProjectionPointModel]] = {}
    for point in result.scalars().all():
        points_by_site.setdefault(point.site_id, []).append(point)

    sites = [
        _projection_from_persisted_points(
            site_id=site_id,
            run=run,
            points=points,
        )
        for site_id, points in points_by_site.items()
    ]
    generated_at = _generated_at_from_run(run, sites)

    return SiteProjectionRunResponse(
        id=run.id,
        model_key=run.model_key,
        status=run.status,
        generated_at=generated_at,
        completed_at=run.completed_at,
        horizon_hours=run.horizon_hours,
        interval_minutes=run.interval_minutes,
        critical_level=run.critical_level,
        level_aggregation=run.level_aggregation,
        lookback_days=run.lookback_days or 0,
        stop_at_full=run.stop_at_full,
        site_count=run.site_count,
        summary=run.summary,
        sites=sites,
    )


def _parse_site_id(site_id: int | str) -> int:
    raw_id = str(site_id).split("|")[-1].strip()
    try:
        return int(raw_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El site_id debe ser numerico.",
        ) from exc


async def _get_current_site_level(
    db: AsyncSession,
    site_id: int,
    level_aggregation: LevelAggregation,
) -> float:
    level_expr = (
        func.coalesce(func.max(Container.current_level), 0)
        if level_aggregation == "max"
        else func.coalesce(
            func.round(cast(func.avg(Container.current_level), Numeric), 2), 0
        )
    )
    stmt = (
        select(level_expr.label("current_level"))
        .select_from(Site)
        .outerjoin(Container, Container.site_id == Site.id)
        .where(Site.id == site_id, Site.deleted_at.is_(None))
        .group_by(Site.id)
    )
    value = await db.scalar(stmt)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sitio no encontrado.",
        )
    return float(value)


def _build_run_summary(projections: list[SiteProjectionResponse]) -> dict:
    sites_reaching_critical = sum(
        1 for projection in projections if projection.critical_at is not None
    )
    sites_reaching_full = sum(
        1 for projection in projections if projection.full_at is not None
    )
    return {
        "site_count": len(projections),
        "sites_reaching_critical": sites_reaching_critical,
        "sites_reaching_full": sites_reaching_full,
        "avg_confidence": round(
            sum(projection.confidence for projection in projections) / len(projections),
            4,
        )
        if projections
        else 0,
    }


def _projection_from_persisted_points(
    site_id: int,
    run: SiteProjectionRun,
    points: list[SiteProjectionPointModel],
) -> SiteProjectionResponse:
    response_points = [
        SiteProjectionPoint(
            timestamp=point.timestamp,
            predicted_level=point.predicted_level,
            lower_bound=(point.metadata_json or {}).get("lower_bound"),
            upper_bound=(point.metadata_json or {}).get("upper_bound"),
            reaches_critical=point.reaches_critical,
            reaches_full=point.reaches_full,
            confidence=point.confidence or 0.0,
            reason=(point.metadata_json or {}).get("reason"),
        )
        for point in points
    ]
    first_metadata = points[0].metadata_json or {} if points else {}
    generated_at = _parse_datetime_metadata(first_metadata.get("generated_at"))
    generated_at = generated_at or run.created_at
    critical_at = _first_timestamp_at_or_above(response_points, run.critical_level)
    full_at = _first_timestamp_at_or_above(response_points, 100)

    return SiteProjectionResponse(
        site_id=site_id,
        model_key=run.model_key,
        generated_at=generated_at,
        current_level=float(first_metadata.get("current_level", 0)),
        horizon_hours=run.horizon_hours,
        interval_minutes=run.interval_minutes,
        critical_level=run.critical_level,
        level_aggregation=run.level_aggregation,
        lookback_days=run.lookback_days or 0,
        stop_at_full=run.stop_at_full,
        confidence=response_points[0].confidence if response_points else 0,
        reason=response_points[0].reason if response_points else None,
        critical_at=critical_at,
        time_to_critical_hours=_hours_between(generated_at, critical_at),
        full_at=full_at,
        time_to_full_hours=_hours_between(generated_at, full_at),
        points=response_points,
    )


def _generated_at_from_run(
    run: SiteProjectionRun,
    sites: list[SiteProjectionResponse],
) -> datetime:
    if sites:
        return sites[0].generated_at
    return _as_utc(run.created_at)


def _parse_datetime_metadata(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


async def _get_site_features(
    db: AsyncSession,
    site_id: int,
) -> dict[str, float | str]:
    result = await db.execute(
        select(
            SiteFeature.feature_key,
            SiteFeature.numeric_value,
            SiteFeature.category_value,
        ).where(SiteFeature.site_id == site_id)
    )
    features: dict[str, float | str] = {}
    for row in result.all():
        features[row.feature_key] = (
            float(row.numeric_value)
            if row.numeric_value is not None
            else row.category_value
        )
    return {key: value for key, value in features.items() if value is not None}


async def _projection_clock(db: AsyncSession) -> datetime:
    active_session = await get_active_simulation_session(db)
    if active_session and active_session.simulated_time:
        return _as_utc(active_session.simulated_time)
    return datetime.now(UTC)


def _first_timestamp_at_or_above(
    points: list[SiteProjectionPoint],
    level: float,
) -> datetime | None:
    for point in points:
        if point.predicted_level >= level:
            return point.timestamp
    return None


def _hours_between(start: datetime, end: datetime | None) -> float | None:
    if end is None:
        return None
    return round((_as_utc(end) - _as_utc(start)).total_seconds() / 3600, 2)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

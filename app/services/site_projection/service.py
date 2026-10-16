from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import Numeric, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.thresholds import get_level_thresholds
from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.data_level import DataLevel
from app.models.map.site import Site
from app.models.map.site_projection import SiteFeature
from app.schemas.map.site_projection import (
    SiteProjectionBatchRequest,
    SiteProjectionBatchResponse,
    SiteProjectionPoint,
    SiteProjectionResponse,
)
from app.services.map.reference_time_service import get_reference_time
from app.services.simulation_session_service import get_active_simulation_session
from app.services.site_projection.history import get_site_level_series
from app.services.site_projection.models import (
    DEFAULT_MODEL_KEY,
    ForecastContext,
    ForecastRequest,
    ForecastResult,
    get_forecaster,
)

LevelAggregation = Literal["avg", "max"]


@dataclass(frozen=True)
class SiteVolumeSnapshot:
    current_volume_m3: float | None = None
    capacity_m3: float | None = None


@dataclass(frozen=True)
class SiteStartingSnapshot:
    level: float
    volume: SiteVolumeSnapshot


async def project_site_level(
    db: AsyncSession,
    site_id: int | str,
    model_key: str = DEFAULT_MODEL_KEY,
    horizon_hours: int = 24,
    interval_minutes: int = 60,
    critical_level: int = get_level_thresholds().critical,
    level_aggregation: LevelAggregation = "avg",
    lookback_days: int = 14,
    stop_at_full: bool = True,
    as_of: datetime | None = None,
) -> SiteProjectionResponse:
    numeric_site_id = _parse_site_id(site_id)
    if as_of is not None:
        snapshot = (
            await _get_site_snapshots_at(
                db, [numeric_site_id], _as_utc(as_of), level_aggregation
            )
        )[numeric_site_id]
        current_level = snapshot.level
        volume_snapshot = snapshot.volume
    else:
        current_level = await _get_current_site_level(
            db,
            site_id=numeric_site_id,
            level_aggregation=level_aggregation,
        )
        volume_snapshot = (await _get_current_site_volumes(db, [numeric_site_id]))[
            numeric_site_id
        ]
    generated_at = _as_utc(as_of) if as_of is not None else await _projection_clock(db)
    history_by_site = await get_site_level_series(
        db=db,
        site_ids=[numeric_site_id],
        interval_minutes=interval_minutes,
        level_aggregation=level_aggregation,
        lookback_days=lookback_days,
        end_time=generated_at,
    )
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
        stop_at_full=False if as_of is not None else stop_at_full,
    )
    context = ForecastContext(
        current_level=current_level,
        history=history_by_site.get(numeric_site_id, []),
        site_features=await _get_site_features(db, numeric_site_id),
        generated_at=generated_at,
        snapshot_at=generated_at,
    )
    forecast_result = await forecaster.forecast(request, context)
    return build_site_projection_response(
        request,
        context,
        volume_snapshot,
        forecast_result,
        stop_at_full=stop_at_full,
    )


def build_site_projection_response(
    request: ForecastRequest,
    context: ForecastContext,
    volume_snapshot: SiteVolumeSnapshot,
    forecast_result: ForecastResult,
    *,
    stop_at_full: bool,
) -> SiteProjectionResponse:
    points = [
        SiteProjectionPoint(
            timestamp=point.timestamp,
            predicted_level=point.predicted_level,
            predicted_volume_m3=_estimate_site_volume(
                volume_snapshot,
                context.current_level,
                point.predicted_level,
            ),
            lower_bound=point.lower_bound,
            upper_bound=point.upper_bound,
            reaches_critical=point.predicted_level >= request.critical_level,
            reaches_full=point.predicted_level >= 100,
            confidence=point.confidence,
            reason=point.reason,
        )
        for point in forecast_result.points
    ]
    critical_at = _first_timestamp_at_or_above(points, request.critical_level)
    full_at = _first_timestamp_at_or_above(points, 100)

    return SiteProjectionResponse(
        site_id=request.site_id,
        model_key=forecast_result.model_key,
        generated_at=forecast_result.generated_at,
        current_level=context.current_level,
        current_volume_m3=volume_snapshot.current_volume_m3,
        capacity_m3=volume_snapshot.capacity_m3,
        horizon_hours=request.horizon_hours,
        interval_minutes=request.interval_minutes,
        critical_level=request.critical_level,
        level_aggregation=request.level_aggregation,
        lookback_days=request.lookback_days,
        stop_at_full=stop_at_full,
        confidence=points[0].confidence if points else 0.0,
        reason=points[0].reason if points else "no_projection_points",
        critical_at=critical_at,
        time_to_critical_hours=_hours_between(
            forecast_result.generated_at, critical_at
        ),
        full_at=full_at,
        time_to_full_hours=_hours_between(forecast_result.generated_at, full_at),
        points=points,
    )


async def project_sites(
    db: AsyncSession,
    request: SiteProjectionBatchRequest,
) -> SiteProjectionBatchResponse:
    if request.target_at is not None and request.as_of is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="as_of es requerido cuando se envia target_at.",
        )

    as_of = request.as_of.astimezone(UTC) if request.as_of else None
    target_at = request.target_at.astimezone(UTC) if request.target_at else None
    horizon_hours = request.horizon_hours
    if target_at is not None and as_of is not None:
        if target_at <= as_of or target_at > as_of + timedelta(hours=168):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="target_at debe estar entre 0 y 168 horas despues de as_of.",
            )
        horizon_hours = ceil((target_at - as_of).total_seconds() / 3600) + 1

    unique_site_ids = sorted(set(request.site_ids))
    generated_at = as_of if as_of is not None else await _projection_clock(db)
    skipped_site_ids: list[int] = []
    if as_of is not None:
        snapshots = await _get_site_snapshots_at(
            db, unique_site_ids, as_of, request.level_aggregation, skip_incomplete=True
        )
        skipped_site_ids = sorted(set(unique_site_ids) - snapshots.keys())
        current_levels = {
            site_id: snapshot.level for site_id, snapshot in snapshots.items()
        }
        current_volumes = {
            site_id: snapshot.volume for site_id, snapshot in snapshots.items()
        }
    else:
        current_levels = await _get_current_site_levels(
            db, unique_site_ids, request.level_aggregation
        )
        for site_id in unique_site_ids:
            if site_id not in current_levels:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Sitio no encontrado.",
                )
        current_volumes = await _get_current_site_volumes(db, unique_site_ids)
    eligible_site_ids = [
        site_id for site_id in unique_site_ids if site_id not in skipped_site_ids
    ]
    history_by_site = await get_site_level_series(
        db=db,
        site_ids=eligible_site_ids,
        interval_minutes=request.interval_minutes,
        level_aggregation=request.level_aggregation,
        lookback_days=request.lookback_days,
        end_time=generated_at,
    )
    features_by_site = await _get_sites_features(db, eligible_site_ids)
    forecaster = get_forecaster(request.model_key)
    if forecaster is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Modelo de proyeccion desconocido: {request.model_key}",
        )
    forecast_inputs = [
        (
            ForecastRequest(
                site_id=site_id,
                horizon_hours=horizon_hours,
                interval_minutes=request.interval_minutes,
                critical_level=request.critical_level,
                level_aggregation=request.level_aggregation,
                lookback_days=request.lookback_days,
                stop_at_full=False if target_at or as_of else request.stop_at_full,
            ),
            ForecastContext(
                current_level=current_levels[site_id],
                history=history_by_site.get(site_id, []),
                site_features=features_by_site.get(site_id, {}),
                generated_at=generated_at,
                snapshot_at=generated_at,
            ),
        )
        for site_id in eligible_site_ids
    ]
    batch_results = (
        await forecaster.forecast_many(forecast_inputs) if forecast_inputs else {}
    )
    projections = [
        build_site_projection_response(
            forecast_request,
            context,
            current_volumes[forecast_request.site_id],
            batch_results[forecast_request.site_id],
            stop_at_full=False if target_at else request.stop_at_full,
        )
        for forecast_request, context in forecast_inputs
    ]

    if target_at is not None:
        for projection in projections:
            projection.target_point = next(
                (
                    point
                    for point in projection.points
                    if _as_utc(point.timestamp) >= target_at
                ),
                None,
            )
            if projection.target_point is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"No hay proyeccion para el sitio {projection.site_id} en target_at.",
                )

    return SiteProjectionBatchResponse(
        model_key=request.model_key,
        generated_at=generated_at,
        horizon_hours=horizon_hours,
        interval_minutes=request.interval_minutes,
        critical_level=request.critical_level,
        level_aggregation=request.level_aggregation,
        lookback_days=request.lookback_days,
        stop_at_full=False if target_at else request.stop_at_full,
        site_count=len(projections),
        summary=_build_run_summary(projections),
        sites=projections,
        skipped_site_ids=skipped_site_ids,
        target_at=target_at,
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
    levels = await _get_current_site_levels(db, [site_id], level_aggregation)
    if site_id not in levels:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sitio no encontrado.",
        )
    return levels[site_id]


async def _get_current_site_levels(
    db: AsyncSession,
    site_ids: list[int],
    level_aggregation: LevelAggregation,
) -> dict[int, float]:
    level_expr = (
        func.coalesce(func.max(Container.current_level), 0)
        if level_aggregation == "max"
        else func.coalesce(
            func.round(cast(func.avg(Container.current_level), Numeric), 2), 0
        )
    )
    stmt = (
        select(Site.id, level_expr.label("current_level"))
        .select_from(Site)
        .outerjoin(Container, Container.site_id == Site.id)
        .where(Site.id.in_(site_ids), Site.deleted_at.is_(None))
        .group_by(Site.id)
    )
    result = await db.execute(stmt)
    return {int(row.id): float(row.current_level) for row in result.all()}


async def _get_current_site_volumes(
    db: AsyncSession, site_ids: list[int]
) -> dict[int, SiteVolumeSnapshot]:
    result = await db.execute(
        select(Container.site_id, Container.current_level, ContainerType.volume_m3)
        .outerjoin(ContainerType, Container.container_type_id == ContainerType.id)
        .where(Container.site_id.in_(site_ids), Container.deleted_at.is_(None))
    )
    volumes: dict[int, float] = {}
    capacities: dict[int, float] = {}
    invalid_sites: set[int] = set()
    for row in result.all():
        site_id = int(row.site_id)
        capacity = row.volume_m3
        if capacity is None or capacity <= 0:
            invalid_sites.add(site_id)
            continue
        if site_id in invalid_sites:
            continue
        capacities[site_id] = capacities.get(site_id, 0.0) + float(capacity)
        volumes[site_id] = volumes.get(site_id, 0.0) + (
            min(max(float(row.current_level), 0.0), 100.0) * float(capacity) / 100
        )
    return {
        site_id: SiteVolumeSnapshot(
            current_volume_m3=round(volumes[site_id], 3),
            capacity_m3=round(capacities[site_id], 3),
        )
        if site_id in volumes and site_id not in invalid_sites
        else SiteVolumeSnapshot()
        for site_id in site_ids
    }


async def _get_site_snapshots_at(
    db: AsyncSession,
    site_ids: list[int],
    as_of: datetime,
    level_aggregation: LevelAggregation,
    skip_incomplete: bool = False,
) -> dict[int, SiteStartingSnapshot]:
    latest_level = (
        select(DataLevel.container_current_level)
        .where(
            DataLevel.container_id == Container.id,
            DataLevel.reading_date <= as_of,
            DataLevel.container_current_level.is_not(None),
        )
        .order_by(DataLevel.reading_date.desc(), DataLevel.id.desc())
        .limit(1)
        .correlate(Container)
        .scalar_subquery()
    )
    result = await db.execute(
        select(
            Site.id.label("site_id"),
            Container.id.label("container_id"),
            latest_level.label("level"),
            ContainerType.volume_m3,
        )
        .select_from(Site)
        .outerjoin(
            Container,
            and_(Container.site_id == Site.id, Container.deleted_at.is_(None)),
        )
        .outerjoin(ContainerType, Container.container_type_id == ContainerType.id)
        .where(Site.id.in_(site_ids), Site.deleted_at.is_(None))
    )
    grouped: dict[int, list] = {}
    for row in result.all():
        grouped.setdefault(int(row.site_id), []).append(row)

    missing_sites = set(site_ids) - grouped.keys()
    if missing_sites:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sitios no encontrados: {sorted(missing_sites)}",
        )

    snapshots: dict[int, SiteStartingSnapshot] = {}
    incomplete_sites: list[int] = []
    for site_id, rows in grouped.items():
        if any(row.container_id is None or row.level is None for row in rows):
            incomplete_sites.append(site_id)
            continue
        levels = [float(row.level) for row in rows]
        level = (
            max(levels)
            if level_aggregation == "max"
            else round(sum(levels) / len(levels), 2)
        )
        if any(row.volume_m3 is None or row.volume_m3 <= 0 for row in rows):
            volume = SiteVolumeSnapshot()
        else:
            capacity = sum(float(row.volume_m3) for row in rows)
            current_volume = sum(
                min(max(float(row.level), 0.0), 100.0) * float(row.volume_m3) / 100
                for row in rows
            )
            volume = SiteVolumeSnapshot(
                current_volume_m3=round(current_volume, 3),
                capacity_m3=round(capacity, 3),
            )
        snapshots[site_id] = SiteStartingSnapshot(level=level, volume=volume)
    if incomplete_sites and not skip_incomplete:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Sitios sin lecturas completas anteriores a as_of: {sorted(incomplete_sites)}",
        )
    return snapshots


def _estimate_site_volume(
    snapshot: SiteVolumeSnapshot,
    starting_level: float,
    predicted_level: float,
) -> float | None:
    if snapshot.current_volume_m3 is None or snapshot.capacity_m3 is None:
        return None
    volume = (
        snapshot.current_volume_m3
        + (predicted_level - starting_level) * snapshot.capacity_m3 / 100
    )
    return round(min(max(volume, 0.0), snapshot.capacity_m3), 3)


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


async def _get_site_features(
    db: AsyncSession,
    site_id: int,
) -> dict[str, float | str]:
    features_by_site = await _get_sites_features(db, [site_id])
    return features_by_site.get(site_id, {})


async def _get_sites_features(
    db: AsyncSession,
    site_ids: list[int],
) -> dict[int, dict[str, float | str]]:
    result = await db.execute(
        select(
            SiteFeature.site_id,
            SiteFeature.feature_key,
            SiteFeature.numeric_value,
            SiteFeature.category_value,
        ).where(SiteFeature.site_id.in_(site_ids))
    )
    features_by_site: dict[int, dict[str, float | str]] = {}
    for row in result.all():
        value = (
            float(row.numeric_value)
            if row.numeric_value is not None
            else row.category_value
        )
        if value is not None:
            features_by_site.setdefault(int(row.site_id), {})[row.feature_key] = value
    return features_by_site


async def _projection_clock(db: AsyncSession) -> datetime:
    active_session = await get_active_simulation_session(db)
    if active_session and active_session.simulated_time:
        return _as_utc(active_session.simulated_time)
    return await get_reference_time(db)


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

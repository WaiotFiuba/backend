from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.core.thresholds import get_level_thresholds
from app.schemas.map.site import (
    SiteChanges,
    SiteCluster,
    SiteLevelHistory,
    SiteMapOutputSchema,
)
from app.schemas.map.site_projection import SiteProjectionResponse
from app.services.map.site_projection_models import DEFAULT_MODEL_KEY
from app.services.map.site_projection_service import project_site_level
from app.services.map.site_service import (
    get_site_by_id,
    get_site_changes,
    get_site_level_history,
    get_sites_clustered,
)

router = APIRouter(prefix="/sites", tags=["sites"])

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]


@router.get(
    "/bbox",
    response_model=list[SiteCluster] | list[SiteMapOutputSchema],
    summary="Obtener sitios en un bounding box o clusters según zoom",
)
async def get_sites_by_bbox(
    db: MapDbDep,
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lng_min: float = Query(...),
    lng_max: float = Query(...),
    zoom: int = Query(..., ge=0, le=22),
    level_aggregation: Literal["avg", "max"] = Query("avg"),
    limit: int | None = Query(None, ge=1),
    offset: int | None = Query(None, ge=0),
    distribution: Literal["real", "whatif"] = Query("real"),
    waste_filter: Literal["all", "humedo", "reciclable"] = Query("all"),
) -> list[SiteCluster] | list[SiteMapOutputSchema]:
    return await get_sites_clustered(
        db=db,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        zoom=zoom,
        level_aggregation=level_aggregation,
        limit=limit,
        offset=offset,
        distribution=distribution,
        waste_filter=waste_filter,
    )


@router.get(
    "/changes",
    response_model=SiteChanges,
    summary="Obtener cambios incrementales en sitios a partir de un cursor",
)
async def get_sites_change_feed(
    db: MapDbDep,
    after: int = Query(0, ge=0),
    level_aggregation: Literal["avg", "max"] = Query("avg"),
    limit: int = Query(5000, ge=1, le=50000),
) -> SiteChanges:
    return await get_site_changes(
        db=db,
        after=after,
        level_aggregation=level_aggregation,
        limit=limit,
    )


@router.get(
    "/{site_id}/history",
    response_model=SiteLevelHistory,
    status_code=status.HTTP_200_OK,
    summary="Obtener histórico agregado de niveles de un sitio",
)
async def get_single_site_history(
    site_id: str,
    db: MapDbDep,
    limit: int = Query(168, ge=1, le=1000),
) -> SiteLevelHistory:
    numeric_id = int(site_id.split("|")[-1]) if site_id.split("|")[-1].isdigit() else 1
    return await get_site_level_history(db=db, site_id=numeric_id, limit=limit)


@router.get(
    "/{site_id}/projection",
    response_model=SiteProjectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Proyectar nivel de llenado de un sitio",
    description=(
        "Calcula una proyeccion on-demand para un sitio usando el modelo indicado. "
        "La v1 usa baseline_operational sobre historico agregado por sitio y "
        "features genericas si existen."
    ),
)
async def get_single_site_projection(
    site_id: str,
    db: MapDbDep,
    model_key: Annotated[str, Query(description="Modelo de proyeccion a usar.")] = (
        DEFAULT_MODEL_KEY
    ),
    horizon_hours: Annotated[int, Query(ge=1, le=168)] = 24,
    interval_minutes: Annotated[int, Query(ge=15, le=1440)] = 60,
    critical_level: Annotated[int, Query(ge=1, le=100)] = (
        get_level_thresholds().critical
    ),
    level_aggregation: Annotated[Literal["avg", "max"], Query()] = "avg",
    lookback_days: Annotated[int, Query(ge=1, le=365)] = 14,
    stop_at_full: Annotated[bool, Query()] = True,
) -> SiteProjectionResponse:
    return await project_site_level(
        db=db,
        site_id=site_id,
        model_key=model_key,
        horizon_hours=horizon_hours,
        interval_minutes=interval_minutes,
        critical_level=critical_level,
        level_aggregation=level_aggregation,
        lookback_days=lookback_days,
        stop_at_full=stop_at_full,
    )


@router.get(
    "/{site_id}",
    response_model=SiteMapOutputSchema,
    status_code=status.HTTP_200_OK,
    summary="Obtener detalle de un sitio por ID con sus contenedores",
)
async def get_single_site(
    site_id: str,
    db: MapDbDep,
    level_aggregation: Literal["avg", "max"] = Query("avg"),
) -> SiteMapOutputSchema:
    return await get_site_by_id(
        db=db,
        site_id=site_id,
        level_aggregation=level_aggregation,
    )

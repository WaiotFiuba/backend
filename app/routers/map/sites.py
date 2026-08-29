from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.schemas.map.site import (
    SiteChanges,
    SiteCluster,
    SiteMapOutputSchema,
    SiteMapSnapshot,
)
from app.services.map.site_service import (
    get_site_by_id,
    get_site_changes,
    get_site_map_snapshot,
    get_sites_clustered,
)

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get(
    "/bbox",
    response_model=list[SiteCluster] | list[SiteMapOutputSchema],
    summary="Obtener sitios en un bounding box o clusters según zoom",
)
async def get_sites_by_bbox(
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lng_min: float = Query(...),
    lng_max: float = Query(...),
    zoom: int = Query(..., ge=0, le=22),
    level_aggregation: Literal["avg", "max"] = Query("avg"),
    limit: int | None = Query(None, ge=1),
    offset: int | None = Query(None, ge=0),
    db: AsyncSession = Depends(get_map_db),
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
    )


@router.get(
    "/bbox/snapshot",
    response_model=SiteMapSnapshot,
    summary="Obtener snapshot inicial completo de sitios y cursor",
)
async def get_all_sites_snapshot(
    level_aggregation: Literal["avg", "max"] = Query("avg"),
    db: AsyncSession = Depends(get_map_db),
) -> SiteMapSnapshot:
    return await get_site_map_snapshot(db=db, level_aggregation=level_aggregation)


@router.get(
    "/changes",
    response_model=SiteChanges,
    summary="Obtener cambios incrementales en sitios a partir de un cursor",
)
async def get_sites_change_feed(
    after: int = Query(0, ge=0),
    level_aggregation: Literal["avg", "max"] = Query("avg"),
    limit: int = Query(5000, ge=1, le=50000),
    db: AsyncSession = Depends(get_map_db),
) -> SiteChanges:
    return await get_site_changes(
        db=db,
        after=after,
        level_aggregation=level_aggregation,
        limit=limit,
    )


@router.get(
    "/{site_id}",
    response_model=SiteMapOutputSchema,
    status_code=status.HTTP_200_OK,
    summary="Obtener detalle de un sitio por ID con sus contenedores",
)
async def get_single_site(
    site_id: int,
    level_aggregation: Literal["avg", "max"] = Query("avg"),
    db: AsyncSession = Depends(get_map_db),
) -> SiteMapOutputSchema:
    return await get_site_by_id(
        db=db,
        site_id=site_id,
        level_aggregation=level_aggregation,
    )

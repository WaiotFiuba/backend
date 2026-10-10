from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.core.thresholds import get_level_thresholds
from app.models.map.container import Container
from app.models.map.site import Site
from app.schemas.map.config import (
    LevelThresholdsResponse,
    MapCenter,
    MapConfigResponse,
)

router = APIRouter(prefix="/config", tags=["config"])
MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]


@router.get(
    "",
    response_model=MapConfigResponse,
    summary="Obtener configuración dinámica del mapa, centroide y límites para cualquier ciudad",
)
async def get_map_config(db: MapDbDep) -> MapConfigResponse:
    city_name = os.getenv("CITY_NAME", "Ciudad")

    res_site = (
        await db.execute(
            select(
                func.min(Site.latitude),
                func.max(Site.latitude),
                func.min(Site.longitude),
                func.max(Site.longitude),
                func.avg(Site.latitude),
                func.avg(Site.longitude),
                func.count(Site.id),
            )
        )
    ).one()

    lat_min, lat_max, lng_min, lng_max, avg_lat, avg_lng, site_count = res_site

    if not site_count or site_count == 0 or avg_lat is None:
        res_cont = (
            await db.execute(
                select(
                    func.min(Container.latitude),
                    func.max(Container.latitude),
                    func.min(Container.longitude),
                    func.max(Container.longitude),
                    func.avg(Container.latitude),
                    func.avg(Container.longitude),
                    func.count(Container.id),
                )
            )
        ).one()
        lat_min, lat_max, lng_min, lng_max, avg_lat, avg_lng, cont_count = res_cont
        has_entities = bool(cont_count and cont_count > 0 and avg_lat is not None)
    else:
        has_entities = True

    if has_entities and lat_min is not None:
        center = MapCenter(lat=float(avg_lat), lng=float(avg_lng))
        bounds = [
            [float(lat_min), float(lng_min)],
            [float(lat_max), float(lng_max)],
        ]
        zoom = 13
    else:
        center = MapCenter(lat=-34.6037, lng=-58.3816)
        bounds = None
        zoom = 13

    thresholds = get_level_thresholds()

    return MapConfigResponse(
        city_name=city_name,
        center=center,
        default_zoom=zoom,
        bounds=bounds,
        thresholds=LevelThresholdsResponse(
            normal=thresholds.normal,
            high=thresholds.high,
            critical=thresholds.critical,
            full=thresholds.full,
        ),
    )

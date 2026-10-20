from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.services.map.zone_profile_service import (
    get_zone_profile_defaults,
    get_zone_profiles_geojson,
)

router = APIRouter(prefix="/zone-profiles", tags=["zone-profiles"])

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]


@router.get(
    "/radios",
    summary="Polígonos de radios censales con su perfil de zona (GeoJSON)",
)
async def read_zone_profiles_radios(db: MapDbDep) -> dict:
    return await get_zone_profiles_geojson(db)


@router.get(
    "/defaults",
    summary="Perfiles de zona por defecto, por tipo de uso del suelo",
)
async def read_zone_profile_defaults(db: MapDbDep) -> dict[str, dict]:
    """De donde parte el editor de perfiles: volumen (demand_multiplier), curva
    por hora (hour_weights) y factores por dia (weekday_factors) de cada tipo de
    uso, tal como los publico el simulador. Vacio si todavia no los publico."""
    return await get_zone_profile_defaults(db)

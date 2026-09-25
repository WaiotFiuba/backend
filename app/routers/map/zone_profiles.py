from __future__ import annotations

from fastapi import APIRouter

from app.services.map.zone_profile_service import get_zone_profiles_geojson

router = APIRouter(prefix="/zone-profiles", tags=["zone-profiles"])


@router.get(
    "/radios",
    summary="Polígonos de radios censales con su perfil de zona (GeoJSON)",
)
async def read_zone_profiles_radios() -> dict:
    return get_zone_profiles_geojson()

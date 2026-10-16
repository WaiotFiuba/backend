from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.simulation import SimulationSession
from app.models.map.zone_profile_layer import LATEST_LAYER_ID, ZoneProfileLayer

EMPTY_LAYER: dict = {"type": "FeatureCollection", "features": []}


async def get_zone_profiles_geojson(db: AsyncSession) -> dict:
    """Ultima capa de perfiles de zona publicada por el simulador: poligono de
    cada radio censal con su perfil de zona (zone_type, demand_multiplier,
    weekend_factor, curva horaria/semanal). Vacia si todavia no corrio ninguna
    simulacion.
    """
    layer = await db.get(ZoneProfileLayer, LATEST_LAYER_ID)
    return layer.geojson if layer is not None else EMPTY_LAYER


async def save_zone_profiles_geojson(
    db: AsyncSession, simulation_id: int, geojson: dict
) -> int:
    """Guarda la capa que publica el simulador al iniciar una sesion,
    reemplazando la anterior. Devuelve la cantidad de features."""
    if await db.get(SimulationSession, simulation_id) is None:
        raise HTTPException(status_code=404, detail="Simulacion no encontrada.")
    layer = await db.get(ZoneProfileLayer, LATEST_LAYER_ID)
    if layer is None:
        db.add(
            ZoneProfileLayer(
                id=LATEST_LAYER_ID, simulation_id=simulation_id, geojson=geojson
            )
        )
    else:
        layer.simulation_id = simulation_id
        layer.geojson = geojson
    await db.commit()
    return len(geojson.get("features", []))

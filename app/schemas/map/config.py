from __future__ import annotations

from pydantic import BaseModel


class MapCenter(BaseModel):
    lat: float
    lng: float


class LevelThresholdsResponse(BaseModel):
    """Umbrales de nivel de llenado (%): límite inferior inclusive de cada categoría."""

    normal: int
    high: int
    critical: int
    full: int


class MapConfigResponse(BaseModel):
    city_name: str
    center: MapCenter
    default_zoom: int
    bounds: list[list[float]] | None = None
    thresholds: LevelThresholdsResponse

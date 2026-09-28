from __future__ import annotations

from pydantic import BaseModel


class MapCenter(BaseModel):
    lat: float
    lng: float


class MapConfigResponse(BaseModel):
    city_name: str
    center: MapCenter
    default_zoom: int
    bounds: list[list[float]] | None = None

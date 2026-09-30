from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SiteContainerSummary(BaseModel):
    id: int
    serie_id: str | None = None
    current_level: int
    device_imei: str | None = None
    available: bool = True
    container_type: str | None = None
    height_cm: float | None = None
    volume_m3: float | None = None
    last_reading: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SiteMapOutputSchema(BaseModel):
    id: int
    name: str
    address: str | None = None
    latitude: float
    longitude: float
    current_level: int = 0
    available: bool = True
    load_side_category: str | None = None
    waste_type_id: int | None = None
    waste_type_name: str | None = None
    waste_type_color: str | None = None
    container_count: int = 0
    last_reading: datetime | None = None
    last_pickup: datetime | None = None
    updated_at: datetime
    containers: list[SiteContainerSummary] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class SiteCluster(BaseModel):
    cluster_id: str
    latitude: float
    longitude: float
    count: int
    avg_level: float
    max_level: float
    available_count: int


class SiteChanges(BaseModel):
    sites: list[SiteMapOutputSchema]
    latest_cursor: int
    has_more: bool = False


class SiteLevelHistoryPoint(BaseModel):
    timestamp: datetime
    avg_level: float
    max_level: int
    min_level: int
    measurement_count: int


class SiteLevelHistory(BaseModel):
    site_id: int
    simulation_id: int | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    points: list[SiteLevelHistoryPoint] = Field(default_factory=list)

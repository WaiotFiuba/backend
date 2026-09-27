from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict


class SimulationZoneState(BaseModel):
    model_config = ConfigDict(frozen=True)

    neighborhood: str
    multiplier_current: float
    multiplier_target: float


class SimulationSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    status: Literal["pending", "running", "paused", "stopping", "completed", "failed"]
    scenario: dict[str, object]
    speedup: float
    global_demand_current: float
    global_demand_target: float
    transition_minutes: int
    simulated_time: datetime | None
    current_period: int
    total_periods: int
    measurements_sent: int
    collections_generated: int
    alarms_generated: int
    error_message: str | None
    created_by: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    zone_overrides: list[
        SimulationZoneState
    ]  # Parses nested dictionaries automatically


class ZoneDemand(BaseModel):
    model_config = ConfigDict(frozen=True)

    neighborhood: str
    commune: str | None
    population: int
    year: int
    source: str
    area_km2: float
    density_per_km2: float
    density_factor: float
    multiplier_effective: float


class Site(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    zone: str
    latitude: float
    longitude: float
    demand_base: float
    address: str | None = None

    def to_record(self) -> dict[str, object]:
        return self.model_dump()


class Container(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    site_id: str
    name: str
    waste_type: str
    height_cm: float
    volume_m3: float | None = None
    serie_id: str | None = None

    def to_record(self) -> dict[str, object]:
        return self.model_dump()


class Device(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    container_id: str

    def to_record(self) -> dict[str, object]:
        return self.model_dump()


class Measurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    site_id: str | None
    container_id: str
    device_id: str
    fill_level_pct: float
    ultrasonic_distance_cm: float
    battery_pct: float
    signal_rssi_dbm: float
    temperature_c: float
    acceleration_g: float
    is_collection_detected: bool
    anomaly: str | None = None

    def to_record(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class CollectionEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    container_id: str
    kind: str
    level_before_pct: float
    level_after_pct: float
    detected_by_sensor: bool

    def to_record(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class Alarm(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    container_id: str
    alarm_type: str
    severity: str

    def to_record(self) -> dict[str, object]:
        return self.model_dump(mode="json")

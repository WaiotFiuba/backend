from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.simulation_status import SimulationStatus


class TelemetryValues(BaseModel):
    fill_level_pct: float = Field(ge=0, le=100)
    ultrasonic_distance_cm: float = Field(ge=0)
    battery_pct: float = Field(ge=0, le=100)
    signal_rssi_dbm: float
    temperature_c: float
    acceleration_g: float = Field(ge=0)


class TelemetryFlags(BaseModel):
    is_collection_detected: bool = False
    anomaly: str | None = None


class TelemetryIngestPayload(BaseModel):
    device_id: str
    container_id: str
    timestamp: datetime
    telemetry: TelemetryValues
    flags: TelemetryFlags = Field(default_factory=TelemetryFlags)


class TelemetryBatchIngestPayload(BaseModel):
    measurements: list[TelemetryIngestPayload] = Field(min_length=1)


class TelemetryIngestResult(BaseModel):
    accepted: int
    updated: int
    not_found: int


class ZoneDemandOverride(BaseModel):
    neighborhood: str
    multiplier: float = Field(gt=0, le=1100)


class SimulationCreate(BaseModel):
    scenario: dict[str, object] = Field(default_factory=dict)
    speedup: float = Field(default=60, gt=0)
    global_demand_multiplier: float = Field(default=1.0, gt=0, le=1100)
    transition_minutes: int = Field(default=60, ge=0, le=10080)
    zone_overrides: list[ZoneDemandOverride] = Field(default_factory=list)
    start_time: datetime | None = Field(default=None)


class SimulationControlsUpdate(BaseModel):
    speedup: float | None = Field(default=None, gt=0)
    global_demand_multiplier: float | None = Field(default=None, gt=0, le=1100)
    transition_minutes: int | None = Field(default=None, ge=0, le=10080)
    zone_overrides: list[ZoneDemandOverride] | None = None


class SimulationProgressUpdate(BaseModel):
    simulated_time: datetime | None = None
    current_period: int | None = None
    global_demand_current: float | None = None
    measurements_sent: int = 0
    collections_generated: int = 0
    alarms_generated: int = 0
    status: SimulationStatus | None = None
    trucks: list[dict] | None = None


class SimulationFinish(BaseModel):
    status: Literal[SimulationStatus.COMPLETED, SimulationStatus.FAILED]
    error_message: str | None = None


class SimulationZoneState(BaseModel):
    neighborhood: str
    multiplier_current: float
    multiplier_target: float


class SimulationRead(BaseModel):
    id: int
    status: SimulationStatus
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
    zone_overrides: list[SimulationZoneState] = Field(default_factory=list)


class ZoneDemandRead(BaseModel):
    neighborhood: str
    commune: str | None
    population: int
    year: int
    source: str
    area_km2: float
    density_per_km2: float
    density_factor: float
    multiplier_effective: float


class SavedConfigurationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    config: dict[str, object] = Field(default_factory=dict)


class SavedConfigurationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    config: dict[str, object] | None = None


class SavedConfigurationRead(BaseModel):
    id: int
    user_id: int
    name: str
    description: str | None
    config: dict[str, object]
    created_at: datetime
    updated_at: datetime

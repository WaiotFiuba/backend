from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class ScenarioInput(BaseModel):
    """Escenario que manda el front al crear una simulacion.

    El backend solo valida los tipos de los campos que entiende; no completa
    defaults. Los defaults los aplica el simulador, que al arrancar la sesion
    reporta el escenario efectivo (ver SimulationProgressUpdate.scenario). Los
    campos que el backend no conoce se guardan tal cual.
    """

    model_config = ConfigDict(extra="allow")

    start: datetime | None = None
    end: datetime | None = None
    periods: int | None = Field(default=None, ge=0)
    frequency_minutes: int | None = Field(default=None, gt=0)
    collection_hours: list[int] | None = None
    collection_days: list[int | str] | None = None
    no_collection_days: list[int | str] | None = None

    @field_validator("start", "end", "periods", mode="before")
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("collection_hours")
    @classmethod
    def _valid_hours(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(not 0 <= h <= 23 for h in value):
            raise ValueError("collection_hours debe tener horas entre 0 y 23.")
        return value

    @model_validator(mode="after")
    def _end_after_start(self) -> ScenarioInput:
        if self.start and self.end and self.end <= self.start:
            raise ValueError("El campo 'end' debe ser posterior a 'start'.")
        return self


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
    # Escenario efectivo (con los defaults del simulador) y cantidad real de
    # periodos: el worker los reporta al marcar la sesion como running.
    scenario: dict[str, object] | None = None
    total_periods: int | None = Field(default=None, ge=0)


class SimulationFinish(BaseModel):
    status: Literal[SimulationStatus.COMPLETED, SimulationStatus.FAILED]
    error_message: str | None = None


class ZoneProfileLayerPayload(BaseModel):
    """Capa GeoJSON de perfiles de zona que publica el simulador."""

    type: Literal["FeatureCollection"]
    features: list[dict]


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

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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

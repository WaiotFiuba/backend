from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    zone: str
    latitude: float
    longitude: float
    demand_base: float

    def to_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Container:
    id: str
    site_id: str
    name: str
    waste_type: str
    height_cm: float
    volume_m3: float | None = None

    def to_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Device:
    id: str
    container_id: str

    def to_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Measurement:
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
        record = asdict(self)
        record["timestamp"] = self.timestamp.isoformat()
        return record


@dataclass(frozen=True)
class CollectionEvent:
    timestamp: datetime
    container_id: str
    kind: str
    level_before_pct: float
    level_after_pct: float
    detected_by_sensor: bool

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["timestamp"] = self.timestamp.isoformat()
        return record


@dataclass(frozen=True)
class Alarm:
    timestamp: datetime
    container_id: str
    alarm_type: str
    severity: str

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["timestamp"] = self.timestamp.isoformat()
        return record

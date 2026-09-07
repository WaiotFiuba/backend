from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any

import yaml


def get_default_start_time() -> datetime:
    return datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class ScenarioConfig:
    name: str = "semana_normal"
    seed: int = 42
    start: datetime = field(default_factory=get_default_start_time)
    end: datetime | None = None
    periods: int = 7 * 24
    frequency_minutes: int = 60
    synthetic_site_count: int = 3
    synthetic_containers_per_site: int = 2
    container_limit: int | None = None
    reading_jitter_minutes: int = 10
    collection_hours: tuple[int, ...] = (21, 22, 23, 0, 1, 2, 3, 4, 5, 6)
    collection_probability: float = 0.85
    partial_collection_probability: float = 0.12
    omitted_collection_probability: float = 0.03
    high_demand_multiplier: float = 1.0
    overflow_stress_multiplier: float = 1.0
    noisy_sensor_probability: float = 0.0
    stuck_sensor_probability: float = 0.0
    fire_probability: float = 0.0
    signal_loss_probability: float = 0.0
    low_battery_probability: float = 0.0
    api_payloads: bool = False
    waste_type_factors: dict[str, float] = field(
        default_factory=lambda: {
            "reciclables": 0.75,
            "residuos_humedos": 1.0,
            "vidrio": 0.55,
        }
    )

    @property
    def total_hours(self) -> float:
        return self.periods * self.frequency_minutes / 60


def load_scenario(path: str | Path | None = None) -> ScenarioConfig:
    if path is None:
        return ScenarioConfig()

    source = Path(path)
    raw = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        data = yaml.safe_load(raw) or {}

    return scenario_from_mapping(data)


def scenario_from_mapping(data: dict[str, Any]) -> ScenarioConfig:
    normalized = dict(data)
    if "site_count" in normalized:
        normalized["synthetic_site_count"] = normalized.pop("site_count")
    if "containers_per_site" in normalized:
        normalized["synthetic_containers_per_site"] = normalized.pop(
            "containers_per_site"
        )
    normalized.pop("sensor_fault_probability", None)
    if "start" in normalized and isinstance(normalized["start"], str):
        normalized["start"] = datetime.fromisoformat(normalized["start"])
    if "end" in normalized and isinstance(normalized["end"], str):
        normalized["end"] = datetime.fromisoformat(normalized["end"])
    if normalized.get("end") is not None:
        normalized["periods"] = _periods_between(
            start=normalized.get("start", get_default_start_time()),
            end=normalized["end"],
            frequency_minutes=normalized.get("frequency_minutes", 60),
        )
    if "collection_hours" in normalized:
        normalized["collection_hours"] = tuple(normalized["collection_hours"])
    return ScenarioConfig(**normalized)


def _periods_between(start: datetime, end: datetime, frequency_minutes: int) -> int:
    if end <= start:
        raise ValueError("El campo 'end' debe ser posterior a 'start'.")
    duration_minutes = (end - start).total_seconds() / 60
    return ceil(duration_minutes / frequency_minutes)

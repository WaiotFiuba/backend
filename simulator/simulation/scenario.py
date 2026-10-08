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
    periods: int = 0
    frequency_minutes: int = 60
    synthetic_site_count: int = 3
    synthetic_containers_per_site: int = 2
    container_limit: int | None = None
    reading_jitter_minutes: int = 10
    collection_hours: tuple[int, ...] = (21, 22, 23, 0, 1, 2, 3, 4, 5, 6)
    collection_days: tuple[int, ...] = (0, 1, 2, 3, 4)
    no_collection_days: tuple[int, ...] = (5, 6)
    collection_probability: float = 0.98  # valores para la recoleccion secundaria
    partial_collection_probability: float = (
        0.03  # valores para la recoleccion secundaria
    )
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
            "reciclables": 0.55,
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
        normalized["synthetic_site_count"] = int(normalized.pop("site_count"))
    if "containers_per_site" in normalized:
        normalized["synthetic_containers_per_site"] = int(
            normalized.pop("containers_per_site")
        )
    normalized.pop("sensor_fault_probability", None)

    if "start" in normalized:
        if not normalized["start"]:
            normalized.pop("start")
        elif isinstance(normalized["start"], str):
            normalized["start"] = datetime.fromisoformat(normalized["start"])
        if (
            "start" in normalized
            and isinstance(normalized["start"], datetime)
            and normalized["start"].tzinfo is not None
        ):
            normalized["start"] = normalized["start"].replace(tzinfo=None)

    if "end" in normalized:
        if not normalized["end"]:
            normalized["end"] = None
        elif isinstance(normalized["end"], str):
            normalized["end"] = datetime.fromisoformat(normalized["end"])
        if (
            normalized["end"] is not None
            and isinstance(normalized["end"], datetime)
            and normalized["end"].tzinfo is not None
        ):
            normalized["end"] = normalized["end"].replace(tzinfo=None)

    if normalized.get("end") is not None:
        normalized["periods"] = _periods_between(
            start=normalized.get("start", get_default_start_time()),
            end=normalized["end"],
            frequency_minutes=int(normalized.get("frequency_minutes", 60)),
        )
    elif (
        "periods" in normalized
        and normalized["periods"] is not None
        and normalized["periods"] != ""
    ):
        normalized["periods"] = int(normalized["periods"])
    else:
        normalized["periods"] = 0

    if "collection_hours" in normalized and normalized["collection_hours"] is not None:
        normalized["collection_hours"] = tuple(
            int(h) for h in normalized["collection_hours"]
        )

    DAY_NAME_MAP = {
        "monday": 0,
        "lunes": 0,
        "mon": 0,
        "lun": 0,
        "tuesday": 1,
        "martes": 1,
        "tue": 1,
        "mar": 1,
        "wednesday": 2,
        "miercoles": 2,
        "miércoles": 2,
        "wed": 2,
        "mie": 2,
        "thursday": 3,
        "jueves": 3,
        "thu": 3,
        "jue": 3,
        "friday": 4,
        "viernes": 4,
        "fri": 4,
        "vie": 4,
        "saturday": 5,
        "sabado": 5,
        "sábado": 5,
        "sat": 5,
        "sab": 5,
        "sunday": 6,
        "domingo": 6,
        "sun": 6,
        "dom": 6,
    }

    def _parse_days(raw_list: Any) -> list[int]:
        res = []
        for d in raw_list:
            if isinstance(d, int) and 0 <= d <= 6:
                res.append(d)
            elif isinstance(d, str):
                d_clean = d.strip().casefold()
                if d_clean.isdigit() and 0 <= int(d_clean) <= 6:
                    res.append(int(d_clean))
                elif d_clean in DAY_NAME_MAP:
                    res.append(DAY_NAME_MAP[d_clean])
        return sorted(set(res))

    if "collection_days" in normalized and normalized["collection_days"] is not None:
        active_days = _parse_days(normalized["collection_days"])
        normalized["collection_days"] = tuple(active_days)
        normalized["no_collection_days"] = tuple(
            d for d in range(7) if d not in active_days
        )
    elif (
        "no_collection_days" in normalized
        and normalized["no_collection_days"] is not None
    ):
        blocked_days = _parse_days(normalized["no_collection_days"])
        normalized["no_collection_days"] = tuple(blocked_days)
        normalized["collection_days"] = tuple(
            d for d in range(7) if d not in blocked_days
        )
    else:
        normalized["collection_days"] = (0, 1, 2, 3, 4)
        normalized["no_collection_days"] = (5, 6)

    if (
        "frequency_minutes" in normalized
        and normalized["frequency_minutes"] is not None
        and normalized["frequency_minutes"] != ""
    ):
        normalized["frequency_minutes"] = int(normalized["frequency_minutes"])

    import dataclasses

    valid_fields = {f.name for f in dataclasses.fields(ScenarioConfig)}
    filtered = {k: v for k, v in normalized.items() if k in valid_fields}
    return ScenarioConfig(**filtered)


def scenario_to_record(config: ScenarioConfig) -> dict[str, object]:
    """Escenario efectivo (con los defaults del simulador) como dict guardable en
    JSON. El worker se lo reporta al backend al iniciar cada sesion."""
    return {
        key: value.isoformat()
        if isinstance(value, datetime)
        else list(value)
        if isinstance(value, tuple)
        else value
        for key, value in config.__dict__.items()
    }


def _periods_between(start: datetime, end: datetime, frequency_minutes: int) -> int:
    if end <= start:
        raise ValueError("El campo 'end' debe ser posterior a 'start'.")
    duration_minutes = (end - start).total_seconds() / 60
    return ceil(duration_minutes / frequency_minutes) + 1

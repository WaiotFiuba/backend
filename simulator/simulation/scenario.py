from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any

import yaml

from simulator.domain.entities import WasteType

# Valores por defecto de los parametros del escenario (ver el archivo).
DEFAULTS_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "scenario_defaults.yaml"
)

# Se resuelven juntos: collection_days tiene prioridad sobre no_collection_days,
# asi que si el escenario trae uno, el del archivo de defaults no se usa.
_COLLECTION_DAY_KEYS = ("collection_days", "no_collection_days")


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
    noisy_sensor_probability: float = 0.0
    stuck_sensor_probability: float = 0.0
    fire_probability: float = 0.0
    signal_loss_probability: float = 0.0
    low_battery_probability: float = 0.0
    api_payloads: bool = False
    waste_type_factors: dict[str, float] = field(
        default_factory=lambda: {
            WasteType.HUMEDO: 1.0,
            WasteType.RECICLABLE: 0.55,
        }
    )
    # Ajuste del usuario por tipo de residuo, encima de waste_type_factors.
    waste_type_multipliers: dict[str, float] = field(default_factory=dict)

    @property
    def total_hours(self) -> float:
        return self.periods * self.frequency_minutes / 60

    def periods_until(self, end: datetime | None) -> int:
        """Períodos desde el inicio hasta end (0 = sin fin); sin end, los del escenario."""
        if end is None:
            return max(self.periods, 0)
        return _periods_between(self.start, end, self.frequency_minutes)

    def with_collection_schedule(
        self, hours: tuple[int, ...] | None, days: tuple[int, ...] | None
    ) -> ScenarioConfig:
        """El mismo escenario con otras horas y días de recolección (None: sin
        cambio). Los días sin recolección se vuelven a calcular."""
        record = scenario_to_record(self)
        if hours is not None:
            record["collection_hours"] = list(hours)
        if days is not None:
            record["collection_days"] = list(days)
        return scenario_from_mapping(record)


def read_scenario_file(path: str | Path) -> dict[str, Any]:
    """Contenido de un escenario YAML o JSON, sin los defaults."""
    source = Path(path)
    raw = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        return json.loads(raw)
    return yaml.safe_load(raw) or {}


def load_scenario(path: str | Path | None = None) -> ScenarioConfig:
    """Escenario de la CLI: los defaults del archivo, pisados por el escenario
    de path (si hay)."""
    data = read_scenario_file(path) if path is not None else {}
    return scenario_from_mapping(with_defaults(data))


def load_scenario_defaults(path: str | Path = DEFAULTS_PATH) -> dict[str, Any]:
    """Lee scenario_defaults.yaml. Si no esta, no hay defaults de archivo y
    quedan los del dataclass."""
    source = Path(path)
    if not source.exists():
        return {}
    return yaml.safe_load(source.read_text(encoding="utf-8")) or {}


def with_defaults(
    scenario: dict[str, Any], defaults: dict[str, Any] | None = None
) -> dict[str, Any]:
    """El escenario completado con los defaults del archivo: lo que trae el
    escenario pisa al archivo."""
    base = load_scenario_defaults() if defaults is None else defaults
    return merge_scenario(base, scenario)


def merge_scenario(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """override pisa a base. Un null no pisa un valor que base tiene, y los
    diccionarios (waste_type_factors) se mezclan clave por clave."""
    merged = dict(base)
    if any(override.get(key) is not None for key in _COLLECTION_DAY_KEYS):
        for key in _COLLECTION_DAY_KEYS:
            merged.pop(key, None)
    for key, value in override.items():
        if value is None and key in base:
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            value = {**base[key], **value}
        merged[key] = value
    return merged


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

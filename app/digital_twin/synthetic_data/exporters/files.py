from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from app.digital_twin.synthetic_data.simulation.engine import SimulationResult

ExportFormat = str


def export_simulation(
    result: SimulationResult,
    output_dir: str | Path,
    api_payloads: bool = False,
    export_format: ExportFormat = "csv",
    include_topology: bool = True,
) -> dict[str, Path]:
    if export_format not in {"csv", "parquet"}:
        raise ValueError("export_format debe ser 'csv' o 'parquet'.")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _remove_previous_outputs(destination)

    paths = {
        "measurements": destination / f"measurements.{export_format}",
        "collections": destination / f"collections.{export_format}",
        "alarms": destination / f"alarms.{export_format}",
    }

    if include_topology:
        paths["sites"] = destination / "sites.csv"
        paths["containers"] = destination / "containers.csv"
        paths["devices"] = destination / "devices.csv"
        _write_csv(paths["sites"], (site.to_record() for site in result.sites))
        _write_csv(paths["containers"], (container.to_record() for container in result.containers))
        _write_csv(paths["devices"], (device.to_record() for device in result.devices))

    _write_tabular(paths["measurements"], _measurement_records(result), export_format)
    _write_tabular(paths["collections"], (item.to_record() for item in result.collections), export_format)
    _write_tabular(paths["alarms"], (item.to_record() for item in result.alarms), export_format)

    if api_payloads:
        payload_path = destination / "api_payloads.jsonl"
        _write_jsonl(payload_path, (_api_payload(item.to_record()) for item in result.measurements))
        paths["api_payloads"] = payload_path

    return paths


def _remove_previous_outputs(destination: Path) -> None:
    generated_names = (
        "sites.csv",
        "containers.csv",
        "devices.csv",
        "measurements.csv",
        "collections.csv",
        "alarms.csv",
        "measurements.parquet",
        "collections.parquet",
        "alarms.parquet",
        "api_payloads.jsonl",
    )
    for name in generated_names:
        path = destination / name
        if path.exists():
            path.unlink()


def _write_tabular(path: Path, rows: Iterable[dict[str, object]], export_format: ExportFormat) -> None:
    if export_format == "csv":
        _write_csv(path, rows)
        return
    _write_parquet(path, rows)


def _write_csv(path: Path, rows: Iterable[dict[str, object]]) -> None:
    materialized = list(rows)
    if not materialized:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(materialized[0]))
        writer.writeheader()
        writer.writerows(materialized)


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, default=str))
            file.write("\n")


def _write_parquet(path: Path, rows: Iterable[dict[str, object]]) -> None:
    materialized = list(rows)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Para exportar Parquet instalá la dependencia opcional `pyarrow` "
            "o ejecutá el motor y exportá solo CSV/JSONL en tests."
        ) from exc

    table = pa.Table.from_pylist(materialized)
    pq.write_table(table, path)


def _measurement_records(result: SimulationResult) -> Iterable[dict[str, object]]:
    container_by_id = {container.id: container for container in result.containers}
    site_by_id = {site.id: site for site in result.sites}
    collection_by_key = {
        (collection.timestamp, collection.container_id): collection
        for collection in result.collections
    }

    for measurement in result.measurements:
        container = container_by_id[measurement.container_id]
        site = site_by_id.get(container.site_id)
        collection = collection_by_key.get((measurement.timestamp, measurement.container_id))
        current_m3 = _current_volume_m3(container.volume_m3, measurement.fill_level_pct)
        yield {
            "imei": measurement.device_id,
            "m_id": "",
            "reading_date": _format_reading_date(measurement.timestamp),
            "reported_height": measurement.ultrasonic_distance_cm,
            "reported_temperature": measurement.temperature_c,
            "reported_collection_date": _format_reading_date(collection.timestamp) if collection else "",
            "garbage_collection_alarm": int(measurement.is_collection_detected),
            "fire_alarm": int(measurement.anomaly == "incendio"),
            "freeze_alarm": 0,
            "crash_alarm": int(measurement.acceleration_g >= 2.5),
            "rssi": measurement.signal_rssi_dbm,
            "container_name": container.name,
            "container_current_level": measurement.fill_level_pct,
            "container_current_m3": current_m3 if current_m3 is not None else "",
            "container_current_t": "",
            "site_name": site.name if site else "",
            "zone_name": site.zone if site else "",
            "waste_type_name": container.waste_type,
        }


def _format_reading_date(value) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def _current_volume_m3(volume_m3: float | None, fill_level_pct: float) -> float | None:
    if volume_m3 is None:
        return None
    return round(volume_m3 * fill_level_pct / 100, 4)


def _api_payload(row: dict[str, object]) -> dict[str, object]:
    return {
        "device_id": row["device_id"],
        "container_id": row["container_id"],
        "timestamp": row["timestamp"],
        "telemetry": {
            "fill_level_pct": row["fill_level_pct"],
            "ultrasonic_distance_cm": row["ultrasonic_distance_cm"],
            "battery_pct": row["battery_pct"],
            "signal_rssi_dbm": row["signal_rssi_dbm"],
            "temperature_c": row["temperature_c"],
            "acceleration_g": row["acceleration_g"],
        },
        "flags": {
            "is_collection_detected": row["is_collection_detected"],
            "anomaly": row["anomaly"],
        },
    }

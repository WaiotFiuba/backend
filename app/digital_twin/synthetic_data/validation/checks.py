from __future__ import annotations

from dataclasses import dataclass

from app.digital_twin.synthetic_data.simulation.engine import SimulationResult


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    metrics: dict[str, float]
    errors: list[str]


def validate_result(result: SimulationResult) -> ValidationReport:
    errors: list[str] = []
    levels = [measurement.fill_level_pct for measurement in result.measurements]
    distances = [measurement.ultrasonic_distance_cm for measurement in result.measurements]

    if any(level < 0 or level > 100 for level in levels):
        errors.append("Hay niveles de llenado fuera del rango 0..100.")
    if any(distance < 0 for distance in distances):
        errors.append("Hay distancias ultrasonicas negativas.")
    if not result.sites:
        errors.append("La simulacion no genero sitios.")
    if not result.containers:
        errors.append("La simulacion no genero contenedores.")
    if not result.devices:
        errors.append("La simulacion no genero dispositivos.")
    if not result.measurements:
        errors.append("La simulacion no genero mediciones.")

    metrics = {
        "sites": float(len(result.sites)),
        "containers": float(len(result.containers)),
        "devices": float(len(result.devices)),
        "measurements": float(len(result.measurements)),
        "collections": float(len(result.collections)),
        "alarms": float(len(result.alarms)),
        "avg_fill_level_pct": round(sum(levels) / len(levels), 4) if levels else 0.0,
        "max_fill_level_pct": max(levels) if levels else 0.0,
    }
    return ValidationReport(ok=not errors, metrics=metrics, errors=errors)

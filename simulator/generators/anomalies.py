"""Fallas que se sortean en cada tick y las alarmas que dispara cada medición."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from simulator.domain.entities import Alarm
from simulator.simulation.scenario import ScenarioConfig


@dataclass(frozen=True)
class AnomalyDraws:
    sensor: np.ndarray  # None, "sensor_trabado" o "sensor_ruidoso" por contenedor
    fire: np.ndarray
    signal_lost: np.ndarray
    low_battery: np.ndarray


def draw_anomalies(
    n: int, config: ScenarioConfig, rng: np.random.Generator
) -> AnomalyDraws:
    """Sorteo independiente por contenedor de cada falla, con las
    probabilidades del escenario."""
    rolls = rng.random(size=n)
    stuck_p = config.stuck_sensor_probability
    noisy_p = config.noisy_sensor_probability
    sensor = np.empty(n, dtype=object)
    sensor[:] = None
    sensor[rolls < stuck_p] = "sensor_trabado"
    sensor[(rolls >= stuck_p) & (rolls < stuck_p + noisy_p)] = "sensor_ruidoso"

    fire = rng.random(size=n) < config.fire_probability
    signal_lost = rng.random(size=n) < config.signal_loss_probability
    low_battery = rng.random(size=n) < config.low_battery_probability
    return AnomalyDraws(
        sensor=sensor, fire=fire, signal_lost=signal_lost, low_battery=low_battery
    )


def alarm_from_measurement(measurement) -> Alarm | None:
    if measurement.fill_level_pct >= 95:
        return Alarm(
            timestamp=measurement.timestamp,
            container_id=measurement.container_id,
            alarm_type="desborde",
            severity="critical",
        )
    if measurement.temperature_c >= 70:
        return Alarm(
            timestamp=measurement.timestamp,
            container_id=measurement.container_id,
            alarm_type="incendio",
            severity="critical",
        )
    if measurement.battery_pct <= 15:
        return Alarm(
            timestamp=measurement.timestamp,
            container_id=measurement.container_id,
            alarm_type="bateria_baja",
            severity="warning",
        )
    if measurement.signal_rssi_dbm <= -115:
        return Alarm(
            timestamp=measurement.timestamp,
            container_id=measurement.container_id,
            alarm_type="perdida_senal",
            severity="warning",
        )
    if measurement.anomaly in {"sensor_trabado", "sensor_ruidoso"}:
        return Alarm(
            timestamp=measurement.timestamp,
            container_id=measurement.container_id,
            alarm_type=measurement.anomaly,
            severity="warning",
        )
    return None

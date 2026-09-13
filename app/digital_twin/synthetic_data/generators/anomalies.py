from __future__ import annotations

import random

from app.digital_twin.synthetic_data.domain.entities import Alarm


def pick_sensor_anomaly(
    rng: random.Random, stuck_probability: float, noisy_probability: float
) -> str | None:
    roll = rng.random()
    if roll < stuck_probability:
        return "sensor_trabado"
    if roll < stuck_probability + noisy_probability:
        return "sensor_ruidoso"
    return None


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

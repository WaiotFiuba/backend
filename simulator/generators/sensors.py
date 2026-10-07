"""Lecturas del dispositivo de cada contenedor en cada tick: distancia
ultrasónica, batería, señal, temperatura y aceleración."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from simulator.domain.entities import Container
from simulator.generators.anomalies import AnomalyDraws


@dataclass(frozen=True)
class SensorReadings:
    distances: np.ndarray  # cm del sensor a la superficie de la basura
    batteries: np.ndarray  # %
    rssi: np.ndarray  # dBm
    temperatures: np.ndarray  # °C
    accelerations: np.ndarray  # g


def read_sensors(
    levels: np.ndarray,
    collected: np.ndarray,
    anomalies: AnomalyDraws,
    timestamp: datetime,
    containers: Sequence[Container],
    heights: np.ndarray,
    previous_batteries: np.ndarray,
    stuck_distances: dict[str, float],
    rng: np.random.Generator,
) -> SensorReadings:
    """stuck_distances es la distancia guardada de cada sensor trabado: se
    actualiza acá (se guarda al trabarse y se borra al destrabarse)."""
    n = len(containers)

    # Distancia: altura vacía menos lo lleno, con ruido (más si es ruidoso).
    sigmas = np.where(anomalies.sensor == "sensor_ruidoso", 6.5, 1.2)
    noises = rng.normal(0.0, sigmas)
    distances = np.round(np.maximum(2.0, heights * (1.0 - levels / 100.0) + noises), 2)

    # Sensor trabado: repite la distancia guardada mientras siga trabado.
    final_distances = distances.copy()
    for idx, container in enumerate(containers):
        if anomalies.sensor[idx] == "sensor_trabado":
            final_distances[idx] = stuck_distances.setdefault(
                container.id, float(distances[idx])
            )
        else:
            stuck_distances.pop(container.id, None)

    # Batería: descarga de cada tick, o cae a 3-14 % si salió batería baja.
    discharges = rng.uniform(0.002, 0.025, size=n)
    normal_batteries = np.maximum(0.0, previous_batteries - discharges)
    forced_low = rng.uniform(3.0, 14.0, size=n)
    batteries = np.round(
        np.where(anomalies.low_battery, forced_low, normal_batteries), 2
    )

    # Señal: normal alrededor de -76 dBm, o perdida (-125 a -116).
    lost_rssi = rng.uniform(-125.0, -116.0, size=n)
    normal_rssi = rng.normal(-76.0, 8.0, size=n)
    rssi = np.round(np.where(anomalies.signal_lost, lost_rssi, normal_rssi), 2)

    # Temperatura: curva del día, o 75-130 °C si hay incendio.
    base_temp = 19.0 + 7.0 * _daily_wave(timestamp.hour)
    fire_temps = rng.uniform(75.0, 130.0, size=n)
    normal_temps = base_temp + rng.normal(0.0, 1.8, size=n)
    temperatures = np.round(np.where(anomalies.fire, fire_temps, normal_temps), 2)

    # Aceleración: el golpe del camión al levantar el contenedor, o reposo.
    collection_acc = rng.uniform(1.45, 3.4, size=n)
    rest_acc = np.maximum(0.0, rng.normal(0.03, 0.025, size=n))
    accelerations = np.round(np.where(collected, collection_acc, rest_acc), 3)

    return SensorReadings(
        distances=final_distances,
        batteries=batteries,
        rssi=rssi,
        temperatures=temperatures,
        accelerations=accelerations,
    )


def _daily_wave(hour: int) -> float:
    return -1 if hour < 6 else min(1, (hour - 6) / 8)

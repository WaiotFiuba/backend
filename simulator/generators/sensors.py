from __future__ import annotations

import random
from datetime import datetime

from simulator.domain.entities import Container


def ultrasonic_distance_cm(
    container: Container,
    fill_level_pct: float,
    rng: random.Random,
    noisy: bool = False,
    stuck_value: float | None = None,
) -> float:
    if stuck_value is not None:
        return round(stuck_value, 2)
    empty_distance = container.height_cm
    distance = empty_distance * (1 - fill_level_pct / 100)
    noise_sigma = 6.5 if noisy else 1.2
    return round(max(2.0, distance + rng.gauss(0, noise_sigma)), 2)


def battery_pct(previous: float, rng: random.Random, force_low: bool = False) -> float:
    if force_low:
        return round(rng.uniform(3.0, 14.0), 2)
    discharge = rng.uniform(0.002, 0.025)
    return round(max(0.0, previous - discharge), 2)


def signal_rssi_dbm(rng: random.Random, lost: bool = False) -> float:
    if lost:
        return round(rng.uniform(-125, -116), 2)
    return round(rng.gauss(-76, 8), 2)


def temperature_c(timestamp: datetime, rng: random.Random, fire: bool = False) -> float:
    if fire:
        return round(rng.uniform(75, 130), 2)
    base = 19 + 7 * _daily_wave(timestamp.hour)
    return round(base + rng.gauss(0, 1.8), 2)


def acceleration_g(rng: random.Random, collection: bool = False) -> float:
    if collection:
        return round(rng.uniform(1.45, 3.4), 3)
    return round(max(0.0, rng.gauss(0.03, 0.025)), 3)


def _daily_wave(hour: int) -> float:
    return -1 if hour < 6 else min(1, (hour - 6) / 8)

from __future__ import annotations

import math
import random
from datetime import datetime

from app.digital_twin.synthetic_data.domain.entities import Container, Site
from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig


def filling_increment(
    timestamp: datetime,
    site: Site,
    container: Container,
    config: ScenarioConfig,
    rng: random.Random,
    global_demand_multiplier: float | None = None,
    zone_demand_multiplier: float = 1.0,
) -> float:
    hour_factor = _hour_factor(timestamp.hour)
    weekday_factor = _weekday_factor(timestamp.weekday())
    waste_factor = config.waste_type_factors.get(container.waste_type, 1.0)
    noise = max(0.2, rng.lognormvariate(0, 0.18))

    increment = (
        site.demand_base
        * hour_factor
        * weekday_factor
        * waste_factor
        * (
            config.high_demand_multiplier
            if global_demand_multiplier is None
            else global_demand_multiplier
        )
        * zone_demand_multiplier
        * config.overflow_stress_multiplier
        * noise
    )
    return round(increment, 4)


def _hour_factor(hour: int) -> float:
    lunch_peak = 0.8 * math.exp(-((hour - 13) ** 2) / 18)
    evening_peak = 1.1 * math.exp(-((hour - 21) ** 2) / 12)
    night_floor = 0.25 if 0 <= hour <= 5 else 0.65
    return night_floor + lunch_peak + evening_peak


def _weekday_factor(weekday: int) -> float:
    if weekday == 5:
        return 1.22
    if weekday == 6:
        return 1.08
    if weekday == 0:
        return 0.92
    return 1.0

from __future__ import annotations

import random
from datetime import datetime

from simulator.simulation.scenario import ScenarioConfig


def should_collect(
    timestamp: datetime, fill_level: float, config: ScenarioConfig, rng: random.Random
) -> bool:
    if timestamp.hour not in config.collection_hours:
        return False
    if rng.random() < config.omitted_collection_probability:
        return False
    threshold = 62 if timestamp.weekday() < 5 else 55
    return fill_level >= threshold and rng.random() < config.collection_probability


def level_after_collection(
    level_before: float, config: ScenarioConfig, rng: random.Random
) -> tuple[str, float]:
    if rng.random() < config.partial_collection_probability:
        reduction = rng.uniform(25, 55)
        return "partial", round(max(0, level_before - reduction), 2)
    return "total", round(rng.uniform(0, 8), 2)

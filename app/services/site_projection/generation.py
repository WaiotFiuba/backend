"""Generación de residuos de un sitio: lo que sube su nivel, sin las bajadas por
recolección. En la hora de una recolección no se ve cuánto se generó, así que se
usa lo típico de esa hora."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from itertools import pairwise
from statistics import median

from app.services.site_projection.collection_cycle import detect_collection_event
from app.services.site_projection.history import SiteLevelBucket

TypicalIncrements = tuple[dict[int, float], float]


def typical_increments(history: list[SiteLevelBucket]) -> TypicalIncrements:
    """Mediana de lo que sube el sitio en cada hora del día, y en general."""
    by_hour: dict[int, list[float]] = defaultdict(list)
    for previous, current in pairwise(history):
        if _is_collection(previous, current):
            continue
        by_hour[current.timestamp.hour].append(current.level - previous.level)
    all_increments = [value for values in by_hour.values() for value in values]
    return (
        {hour: median(values) for hour, values in by_hour.items()},
        median(all_increments) if all_increments else 0.0,
    )


def generation_increment(
    previous: SiteLevelBucket, current: SiteLevelBucket, typical: TypicalIncrements
) -> float:
    if _is_collection(previous, current):
        by_hour, overall = typical
        return by_hour.get(current.timestamp.hour, overall)
    return current.level - previous.level


def hourly_generation(history: list[SiteLevelBucket]) -> list[float]:
    """Lo generado en cada bucket; el primero va en 0."""
    ordered = sorted(history, key=lambda bucket: bucket.timestamp)
    if not ordered:
        return []
    typical = typical_increments(ordered)
    return [0.0] + [
        round(generation_increment(previous, current, typical), 4)
        for previous, current in pairwise(ordered)
    ]


def uncollected_levels(
    training: list[SiteLevelBucket], observed: list[SiteLevelBucket]
) -> list[tuple[datetime, float]]:
    """Nivel que hubiera tenido el sitio si no lo vaciaban después del corte:
    el nivel del corte (primer punto) más lo que se generó desde entonces."""
    training = sorted(training, key=lambda bucket: bucket.timestamp)
    observed = sorted(observed, key=lambda bucket: bucket.timestamp)
    if not training or not observed:
        return []
    typical = typical_increments(training)
    previous = training[-1]
    levels = [(previous.timestamp, previous.level)]
    for bucket in observed:
        level = levels[-1][1] + generation_increment(previous, bucket, typical)
        levels.append((bucket.timestamp, level))
        previous = bucket
    return levels


def first_crossing(
    levels: list[tuple[datetime, float]], level: float
) -> datetime | None:
    """Primera lectura después del corte en la que se está en level o más."""
    for (_, before), (timestamp, after) in pairwise(levels):
        if before >= level or after >= level:
            return timestamp
    return None


def _is_collection(previous: SiteLevelBucket, current: SiteLevelBucket) -> bool:
    return (
        current.level < previous.level
        or detect_collection_event(current, previous) is not None
    )

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np

from simulator.domain.entities import CollectionEvent, Container
from simulator.simulation.scenario import ScenarioConfig

if TYPE_CHECKING:
    from simulator.simulation.state import ContainerArrays
    from simulator.trucks.truck_engine import TruckFleetSimulator


@dataclass(frozen=True)
class CollectionOutcome:
    levels: np.ndarray  # niveles después de recolectar
    collected: np.ndarray  # True en los contenedores recolectados en el tick
    events: list[CollectionEvent]


def collect_with_trucks(
    fleet: TruckFleetSimulator,
    levels: np.ndarray,
    timestamp: datetime,
    containers: Sequence[Container],
    arrays: ContainerArrays,
    config: ScenarioConfig,
) -> CollectionOutcome:
    """Les pasa a los camiones los contenedores de cada sitio con su nivel
    actual; los que vacían quedan con el nivel que deja el camión."""
    items = [
        {
            "id": container.id,
            "index": i,
            "current_level": float(levels[i]),
            "waste_type": container.waste_type,
        }
        for i, container in enumerate(containers)
    ]
    containers_by_site = {
        str(site_id): [items[idx] for idx in indices]
        for site_id, indices in arrays.containers_by_site.items()
    }
    truck_events = fleet.step(
        simulated_time=timestamp,
        dt_seconds=config.frequency_minutes * 60.0,
        speedup=1.0,
        containers_by_site=containers_by_site,
    )

    levels = levels.copy()
    collected = np.zeros(len(containers), dtype=bool)
    events: list[CollectionEvent] = []
    for ev in truck_events:
        idx = ev["container_index"]
        levels[idx] = ev["level_after"]
        collected[idx] = True
        events.append(
            CollectionEvent(
                timestamp=_reading_time(timestamp, arrays, idx),
                container_id=containers[idx].id,
                kind="total",
                level_before_pct=float(np.round(ev["level_before"], 2)),
                level_after_pct=float(np.round(ev["level_after"], 2)),
                detected_by_sensor=True,
            )
        )
    return CollectionOutcome(levels=levels, collected=collected, events=events)


def collect_probabilistic(
    levels: np.ndarray,
    timestamp: datetime,
    containers: Sequence[Container],
    arrays: ContainerArrays,
    config: ScenarioConfig,
    rng: np.random.Generator,
) -> CollectionOutcome:
    """Modelo viejo, sin camiones: en las horas de recolección, cada contenedor
    por encima del umbral (62 % en días hábiles, 55 % el fin de semana) se
    recolecta con probabilidad collection_probability, salvo que se omita
    (omitted_collection_probability). La recolección es parcial con
    probabilidad partial_collection_probability (baja 25-55 puntos) y si no,
    total (queda entre 0 y 8 %)."""
    n = len(containers)
    is_collection_hour = (
        timestamp.hour in config.collection_hours
        and timestamp.weekday() not in config.no_collection_days
    )
    if is_collection_hour:
        not_omitted = rng.random(size=n) >= config.omitted_collection_probability
        threshold = 62.0 if timestamp.weekday() < 5 else 55.0
        above_threshold = levels >= threshold
        will_collect = rng.random(size=n) < config.collection_probability
        collected = not_omitted & above_threshold & will_collect
    else:
        collected = np.zeros(n, dtype=bool)

    is_partial = rng.random(size=n) < config.partial_collection_probability
    reduction = rng.uniform(25.0, 55.0, size=n)
    total_val = rng.uniform(0.0, 8.0, size=n)
    partial_level = np.maximum(0.0, levels - reduction)
    new_levels_if_collected = np.round(
        np.where(is_partial, partial_level, total_val), 2
    )
    new_levels = np.where(collected, new_levels_if_collected, levels)

    events = [
        CollectionEvent(
            timestamp=_reading_time(timestamp, arrays, idx),
            container_id=containers[idx].id,
            kind="partial" if is_partial[idx] else "total",
            level_before_pct=float(np.round(levels[idx], 2)),
            level_after_pct=float(new_levels[idx]),
            detected_by_sensor=bool((levels[idx] - new_levels[idx]) >= 20.0),
        )
        for idx in np.where(collected)[0]
    ]
    return CollectionOutcome(levels=new_levels, collected=collected, events=events)


def _reading_time(timestamp: datetime, arrays: ContainerArrays, idx: int) -> datetime:
    return timestamp + timedelta(minutes=int(arrays.reading_offsets[idx]))

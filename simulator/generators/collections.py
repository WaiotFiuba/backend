from __future__ import annotations

import zlib
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
    """Método secundario, sin camiones: lo usa el engine si la flota no se pudo
    armar. Imita al camión sin rutas:
    - Respeta las horas y los días de recolección del escenario (los del front).
    - Cada sitio tiene un turno, una de las horas de recolección (sale de su
      id), y en ese turno se recolectan todos sus contenedores con algo de
      basura, con probabilidad collection_probability. Así cada sitio se
      recolecta a lo sumo una vez por noche.
    - Cada contenedor queda entre 0 y 8 %, salvo que la recolección sea parcial
      (probabilidad partial_collection_probability): baja 25-55 puntos.
    """
    n = len(containers)
    hours = sorted(config.collection_hours, key=lambda h: (h - 18) % 24)
    collected = np.zeros(n, dtype=bool)
    if timestamp.hour in hours and timestamp.weekday() not in config.no_collection_days:
        turn = hours.index(timestamp.hour)
        for site_id, indices in arrays.containers_by_site.items():
            if _site_turn(site_id, len(hours)) != turn:
                continue
            if rng.random() < config.collection_probability:
                collected[indices] = True
        collected &= levels > 0.0

    is_partial = rng.random(size=n) < config.partial_collection_probability
    reduction = rng.uniform(25.0, 55.0, size=n)
    total_val = rng.uniform(0.0, 8.0, size=n)
    partial_level = np.maximum(0.0, levels - reduction)
    new_levels_if_collected = np.round(
        np.where(is_partial, partial_level, total_val), 2
    )
    new_levels = np.where(
        collected, np.minimum(levels, new_levels_if_collected), levels
    )

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


def _site_turn(site_id: str, n_turns: int) -> int:
    """Turno fijo de un sitio entre las horas de recolección. crc32 y no hash():
    el hash de los str de Python cambia en cada ejecución."""
    return zlib.crc32(str(site_id).encode("utf-8")) % n_turns


def _reading_time(timestamp: datetime, arrays: ContainerArrays, idx: int) -> datetime:
    return timestamp + timedelta(minutes=int(arrays.reading_offsets[idx]))

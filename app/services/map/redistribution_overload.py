"""Modelo de sobrecarga de sitios: "rehacer la historia" con otros contenedores.

Para decidir a dónde mover contenedores hay que estimar qué le habría pasado a
cada sitio con otra cantidad de contenedores. En lugar de resumir la historia
en promedios, se usa la serie real del sitio (con sus horarios reales de
recolección y su patrón real de llenado) y se reescala:

- Se reconstruye cuánto residuo se acumuló en el sitio desde la última
  recolección, hora por hora (en "% de un contenedor").
- Con c contenedores el nivel habría sido ese acumulado / c (la basura del
  sitio se reparte en partes iguales entre sus contenedores).
- Se cuentan las horas en zona crítica (entre el umbral crítico y el 100%) y
  desbordado (100% o más). El costo del sitio es crítico + peso * desbordado.

Como el acumulado se ordena una sola vez, el costo para cualquier cantidad de
contenedores es una búsqueda binaria.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from app.services.map.site_collection_cycle_service import detect_collection_event
from app.services.map.site_projection_history_service import SiteLevelBucket

FULL_LEVEL = 100.0
# Umbral crítico por defecto; coincide con el umbral centralizado del backend.
DEFAULT_CRITICAL_LEVEL = 80.0
# Una hora desbordado pesa como esta cantidad de horas en zona crítica.
DEFAULT_OVERFLOW_WEIGHT = 3.0
# Desde este nivel el sensor marca "lleno" y el nivel ya no sube aunque se siga
# generando residuo.
SATURATION_LEVEL = 99.5
# Mientras el sitio está lleno se supone que se siguió llenando al ritmo
# promedio de esta cantidad de buckets previos.
RECENT_RISES = 3


@dataclass(frozen=True)
class OverloadHours:
    critical: float
    overflow: float

    def cost(self, overflow_weight: float = DEFAULT_OVERFLOW_WEIGHT) -> float:
        return self.critical + overflow_weight * self.overflow


class OverloadProfile:
    """Historia de un sitio lista para evaluarla con otras cantidades de contenedores."""

    def __init__(self, accumulated: np.ndarray, hours_per_bucket: float):
        self._sorted = np.sort(accumulated)
        self._hours_per_bucket = hours_per_bucket

    def hours(
        self,
        containers: int,
        critical_level: float = DEFAULT_CRITICAL_LEVEL,
    ) -> OverloadHours:
        """Horas en zona crítica y desbordado si el sitio hubiera tenido `containers`."""
        if containers <= 0:
            raise ValueError("Un sitio no puede quedar con 0 contenedores.")
        total = len(self._sorted)
        over_critical = total - np.searchsorted(
            self._sorted, critical_level * containers
        )
        overflowing = total - np.searchsorted(self._sorted, FULL_LEVEL * containers)
        return OverloadHours(
            critical=float(over_critical - overflowing) * self._hours_per_bucket,
            overflow=float(overflowing) * self._hours_per_bucket,
        )

    def cost(
        self,
        containers: int,
        critical_level: float = DEFAULT_CRITICAL_LEVEL,
        overflow_weight: float = DEFAULT_OVERFLOW_WEIGHT,
    ) -> float:
        return self.hours(containers, critical_level).cost(overflow_weight)


def accumulated_since_collection(
    buckets: list[SiteLevelBucket],
    containers: int,
) -> np.ndarray:
    """Residuo acumulado desde la última recolección en cada bucket (% de un contenedor).

    En el bucket de una recolección el nivel ya es el de después de vaciar, así
    que el acumulado arranca de ese nivel. Mientras el sitio está lleno, se
    supone que siguió subiendo al ritmo promedio de los buckets previos.
    """
    ordered = sorted(buckets, key=lambda bucket: bucket.timestamp)
    if not ordered:
        return np.array([])
    accumulated = np.empty(len(ordered))
    total = ordered[0].level * containers
    accumulated[0] = total
    recent: list[float] = []
    for index, (previous, current) in enumerate(pairwise(ordered), start=1):
        if detect_collection_event(current, previous) is not None:
            total = current.level * containers
            recent.clear()
        else:
            saturated = (
                previous.level >= SATURATION_LEVEL and current.level >= SATURATION_LEVEL
            )
            if saturated and recent:
                rise = sum(recent[-RECENT_RISES:]) / len(recent[-RECENT_RISES:])
            else:
                rise = max(0.0, current.level - previous.level)
                recent.append(rise)
            total += rise * containers
        accumulated[index] = total
    return accumulated


def build_overload_profile(
    buckets: list[SiteLevelBucket],
    containers: int,
    interval_minutes: int = 60,
) -> OverloadProfile | None:
    """Perfil de sobrecarga del sitio, o None si no tiene historia."""
    if not buckets or containers <= 0:
        return None
    return OverloadProfile(
        accumulated_since_collection(buckets, containers),
        hours_per_bucket=interval_minutes / 60,
    )

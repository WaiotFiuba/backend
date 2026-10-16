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

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.core.thresholds import get_level_thresholds
from app.services.site_projection.collection_cycle import (
    CollectionCycleDetectionConfig,
)
from app.services.site_projection.history import SiteLevelBucket

FULL_LEVEL = float(get_level_thresholds().full)
# Umbral crítico centralizado del backend (app/core/thresholds.py).
DEFAULT_CRITICAL_LEVEL = float(get_level_thresholds().critical)
# Una hora desbordado pesa como esta cantidad de horas en zona crítica.
DEFAULT_OVERFLOW_WEIGHT = 3.0
# Desde este nivel el sensor marca "lleno" y el nivel ya no sube aunque se siga
# generando residuo.
SATURATION_LEVEL = 99.5
# Mientras el sitio está lleno se supone que se siguió llenando al ritmo
# promedio de esta cantidad de buckets previos.
RECENT_RISES = 3
# Caída de nivel que se considera recolección (la misma que usa detect_collection_event).
COLLECTION_DROP = CollectionCycleDetectionConfig().partial_drop_threshold


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
    """Residuo acumulado desde la última recolección en cada bucket (% de un contenedor)."""
    ordered = sorted(buckets, key=lambda bucket: bucket.timestamp)
    return accumulated_from_levels(
        [bucket.level for bucket in ordered],
        [bucket.has_collection_signal for bucket in ordered],
        containers,
    )


def accumulated_from_levels(
    levels: Sequence[float],
    collected: Sequence[bool],
    containers: int,
) -> np.ndarray:
    """Igual que accumulated_since_collection, a partir de la serie ya ordenada.

    Hay recolección cuando el bucket tiene señal de recolección o el nivel cae
    al menos `COLLECTION_DROP` puntos (la misma regla que detect_collection_event).
    En el bucket de una recolección el nivel ya es el de después de vaciar, así
    que el acumulado arranca de ese nivel. Mientras el sitio está lleno, se
    supone que siguió subiendo al ritmo promedio de los buckets previos.
    """
    if len(levels) == 0:
        return np.array([])
    accumulated = np.empty(len(levels))
    total = levels[0] * containers
    accumulated[0] = total
    recent: list[float] = []
    for index in range(1, len(levels)):
        previous, current = levels[index - 1], levels[index]
        if (
            collected[index]
            or round(max(0.0, previous - current), 2) >= COLLECTION_DROP
        ):
            total = current * containers
            recent.clear()
        else:
            saturated = previous >= SATURATION_LEVEL and current >= SATURATION_LEVEL
            if saturated and recent:
                rise = sum(recent[-RECENT_RISES:]) / len(recent[-RECENT_RISES:])
            else:
                rise = max(0.0, current - previous)
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


def build_overload_profile_from_levels(
    levels: Sequence[float],
    collected: Sequence[bool],
    containers: int,
    interval_minutes: int = 60,
) -> OverloadProfile | None:
    """Como build_overload_profile, a partir de la serie ya ordenada."""
    if len(levels) == 0 or containers <= 0:
        return None
    return OverloadProfile(
        accumulated_from_levels(levels, collected, containers),
        hours_per_bucket=interval_minutes / 60,
    )

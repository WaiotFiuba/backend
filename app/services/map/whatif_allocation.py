"""Regla de reparto del what-if: traduce cada lectura real al mundo virtual.

El what-if es la red como si el plan ya se hubiera aplicado, pero los sensores
siguen midiendo la red real. Cada lectura real de un contenedor `r` en su sitio
real `S` es basura que llegó a `S`; en el mundo virtual esa basura se reparte
entre los contenedores que `S` tendría con el plan:

- `K`: los contenedores que se quedan en `S`.
- `A`: los que llegan a `S` según el plan.
- `V = K ∪ A`: los contenedores virtuales de `S`.

| Caso | Qué recibe cada contenedor virtual |
|---|---|
| `r` se queda en `S` | `r` recibe `Δ·|K|/|V|` y cada uno de `A` recibe `Δ/|V|`. |
| `r` se va a otro sitio | Su sensor sigue midiendo en `S`: `Δ` se reparte entre los de `K`. |
| `S` no participa del plan | `|A| = 0`: el nivel virtual sigue exactamente al real. |

Lo que se suma en los virtuales de `S` es siempre la generación medida en `S`.
Funciones puras: no tocan la base de datos.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.thresholds import get_level_thresholds
from app.services.map.redistribution_overload import SATURATION_LEVEL

FULL_LEVEL = float(get_level_thresholds().full)
# Suavizado de la velocidad de llenado: cuánto pesa la última lectura frente al
# promedio anterior. 0,3 sigue los cambios en pocas horas sin saltar con el ruido.
RATE_SMOOTHING = 0.3


@dataclass
class VirtualContainer:
    """Estado what-if de un contenedor (una fila de optimization_whatif_levels)."""

    container_id: int
    # Sitio real del contenedor y sitio que tiene según el plan.
    original_site_id: int | None
    optimized_site_id: int | None
    level: float
    # Última lectura real de su sensor y última vez que cambió su nivel virtual.
    last_reading: datetime | None
    virtual_updated_at: datetime | None
    # Velocidad reciente de llenado del contenedor real (puntos por hora).
    rate_ewma: float | None = None
    overflow_hours: float = 0.0
    real_overflow_hours: float = 0.0

    @property
    def stays(self) -> bool:
        return self.original_site_id == self.optimized_site_id


@dataclass(frozen=True)
class Reading:
    """Lectura real de un contenedor, ya aceptada por la ingesta."""

    container_id: int
    level_before: float | None
    level_after: float
    reading_date: datetime
    collected: bool


def apply_readings(
    readings: Sequence[Reading],
    containers: dict[int, VirtualContainer],
) -> set[int]:
    """Aplica las lecturas al estado virtual y devuelve los contenedores que cambiaron.

    `containers` tiene que incluir, para cada sitio real de las lecturas, todos
    sus contenedores virtuales (los que se quedan, los que se van y los que
    llegan). Se modifica en el lugar. Las lecturas de contenedores que no están
    en `containers` (por ejemplo, agregados después de activar el plan) se ignoran.
    """
    stay_by_site: dict[int | None, list[VirtualContainer]] = defaultdict(list)
    arrive_by_site: dict[int | None, list[VirtualContainer]] = defaultdict(list)
    for container in containers.values():
        if container.stays:
            stay_by_site[container.original_site_id].append(container)
        else:
            arrive_by_site[container.optimized_site_id].append(container)

    changed: set[int] = set()
    for reading in sorted(readings, key=lambda r: r.reading_date):
        sensor = containers.get(reading.container_id)
        if sensor is None:
            continue
        site = sensor.original_site_id
        changed |= _apply_reading(
            reading, sensor, stay_by_site[site], arrive_by_site[site]
        )
    return changed


def _apply_reading(
    reading: Reading,
    sensor: VirtualContainer,
    stay: list[VirtualContainer],
    arrive: list[VirtualContainer],
) -> set[int]:
    now = reading.reading_date
    elapsed = _hours_between(sensor.last_reading, now)
    before = reading.level_before
    generated = _generated(reading, sensor, elapsed)

    # Horas desbordado del contenedor real, con la misma regla que los virtuales.
    if before is not None and not reading.collected:
        sensor.real_overflow_hours += _overflow_hours(
            before, before + generated, elapsed
        )
    elif before is not None and before >= SATURATION_LEVEL:
        sensor.real_overflow_hours += elapsed  # lleno hasta que pasó el camión
    _update_rate(reading, sensor, elapsed)
    sensor.last_reading = now

    if reading.collected:
        return _apply_collection(reading, sensor, arrive)

    if sensor.stays:
        shares = len(stay) + len(arrive)
        targets = [(sensor, generated * len(stay) / shares)]
        targets += [(container, generated / shares) for container in arrive]
    elif stay:
        # El sensor se va según el plan, pero en la realidad sigue en el sitio:
        # lo que mide lo reciben los que se quedan.
        targets = [(container, generated / len(stay)) for container in stay]
    else:
        targets = []

    for container, amount in targets:
        _add_level(container, amount, now)
    return {container.container_id for container, _ in targets}


def _generated(reading: Reading, sensor: VirtualContainer, elapsed: float) -> float:
    """Basura que llegó al contenedor real desde su lectura anterior (en puntos).

    Con signo: las bajadas chicas son ruido del sensor. Si el contenedor real
    quedó lleno, el sensor no puede mostrar todo lo que llegó: se completa con
    su velocidad reciente (censura).
    """
    if reading.level_before is None:
        return 0.0
    observed = reading.level_after - reading.level_before
    if reading.level_after >= SATURATION_LEVEL and sensor.rate_ewma is not None:
        return max(observed, sensor.rate_ewma * elapsed)
    return observed


def _update_rate(reading: Reading, sensor: VirtualContainer, elapsed: float) -> None:
    """Actualiza la velocidad de llenado solo con lecturas que la miden bien."""
    if (
        reading.collected
        or reading.level_before is None
        or reading.level_after >= SATURATION_LEVEL
        or elapsed <= 0
    ):
        return
    rate = max(reading.level_after - reading.level_before, 0.0) / elapsed
    if sensor.rate_ewma is None:
        sensor.rate_ewma = rate
    else:
        sensor.rate_ewma = (
            RATE_SMOOTHING * rate + (1 - RATE_SMOOTHING) * sensor.rate_ewma
        )


def _apply_collection(
    reading: Reading, sensor: VirtualContainer, arrive: list[VirtualContainer]
) -> set[int]:
    """Pasó el camión por el sitio real.

    Si el sensor se queda en el sitio, su virtual queda como el real después de
    vaciar, y los que llegan al sitio se vacían en la misma pasada. Si el sensor
    se va según el plan, su recolección no cambia nada en el mundo virtual: los
    que se quedan se vacían con sus propias recolecciones.
    """
    now = reading.reading_date
    if not sensor.stays:
        return set()
    _set_level(sensor, reading.level_after, now)
    for container in arrive:
        _set_level(container, 0.0, now)
    return {sensor.container_id, *(container.container_id for container in arrive)}


def _add_level(container: VirtualContainer, amount: float, now: datetime) -> None:
    elapsed = _hours_between(container.virtual_updated_at, now)
    raw = container.level + amount
    container.overflow_hours += _overflow_hours(container.level, raw, elapsed)
    container.level = min(FULL_LEVEL, max(0.0, raw))
    container.virtual_updated_at = now


def _set_level(container: VirtualContainer, level: float, now: datetime) -> None:
    if container.level >= FULL_LEVEL:
        # Estuvo lleno hasta que pasó el camión.
        container.overflow_hours += _hours_between(container.virtual_updated_at, now)
    container.level = min(FULL_LEVEL, max(0.0, level))
    container.virtual_updated_at = now


def _overflow_hours(before: float, after: float, elapsed: float) -> float:
    """Tiempo al 100% en un intervalo en el que el nivel pasó de `before` a `after`.

    Si estuvo lleno todo el intervalo, cuenta entero; si cruzó el 100% en el
    medio, cuenta la parte proporcional (de 90% a 110% es medio intervalo).
    """
    if elapsed <= 0 or after < FULL_LEVEL:
        return 0.0
    if before >= FULL_LEVEL:
        return elapsed
    return elapsed * (after - FULL_LEVEL) / (after - before)


def _hours_between(start: datetime | None, end: datetime) -> float:
    if start is None:
        return 0.0
    return max((end - start).total_seconds() / 3600, 0.0)

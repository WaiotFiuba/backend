"""Elección de movimientos de contenedores con el modelo de sobrecarga.

Greedy por ganancia marginal: en cada paso se hace el movimiento de un
contenedor (donante -> receptor) que más reduce la sobrecarga total, es decir,
lo que mejora el receptor menos lo que empeora el donante. Después se
recalculan los pares afectados y se repite hasta que ningún movimiento mejora.

Restricciones:
- ningún sitio queda con 0 contenedores;
- un receptor no supera la capacidad física de su cuadra (si se conoce);
- donante y receptor están a menos de `max_distance_km`.

Los donantes cercanos a cada receptor se buscan con un KD-Tree, así que no se
calculan las distancias entre todos los pares de sitios.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from app.services.map.redistribution_overload import (
    DEFAULT_CRITICAL_LEVEL,
    DEFAULT_OVERFLOW_WEIGHT,
    OverloadProfile,
)

_EARTH_RADIUS_KM = 6371.0
# Una mejora menor a esto se considera nula (evita movimientos por redondeo).
_MIN_GAIN = 1e-9


@dataclass(frozen=True)
class SiteState:
    site_id: int
    latitude: float
    longitude: float
    containers: int
    profile: OverloadProfile
    max_containers: int | None = None  # None: sin límite de capacidad conocido


@dataclass(frozen=True)
class PlannedMove:
    donor_site_id: int
    receiver_site_id: int
    distance_km: float
    overload_reduction: float  # mejora del receptor menos daño del donante


def plan_overload_moves(
    sites: list[SiteState],
    max_distance_km: float,
    critical_level: float = DEFAULT_CRITICAL_LEVEL,
    overflow_weight: float = DEFAULT_OVERFLOW_WEIGHT,
    move_cost: float = 0.0,
) -> list[PlannedMove]:
    """Movimientos de a un contenedor, en el orden en que se eligieron.

    Solo hace los que mejoran más que `move_cost` (en las unidades de
    OverloadProfile.cost).
    """
    if not sites:
        return []
    count = [site.containers for site in sites]
    cost_cache: dict[tuple[int, int], float] = {}

    def cost(index: int, containers: int) -> float:
        key = (index, containers)
        if key not in cost_cache:
            cost_cache[key] = sites[index].profile.cost(
                containers, critical_level, overflow_weight
            )
        return cost_cache[key]

    def can_receive(index: int) -> bool:
        limit = sites[index].max_containers
        return limit is None or count[index] < limit

    def gain(donor: int, receiver: int) -> float:
        if count[donor] <= 1 or not can_receive(receiver):
            return 0.0
        relief = cost(receiver, count[receiver]) - cost(receiver, count[receiver] + 1)
        damage = cost(donor, count[donor] - 1) - cost(donor, count[donor])
        return relief - damage

    receivers = [
        i for i in range(len(sites)) if can_receive(i) and cost(i, count[i]) > 0
    ]
    donors = [i for i in range(len(sites)) if count[i] >= 2]
    if not receivers or not donors:
        return []

    coords = _planar_km(sites)
    tree = cKDTree(coords[donors])
    donors_of: dict[int, list[int]] = {}
    receivers_of: dict[int, list[int]] = {}
    for receiver in receivers:
        nearby = [
            donors[j]
            for j in tree.query_ball_point(coords[receiver], max_distance_km)
            if donors[j] != receiver
        ]
        donors_of[receiver] = nearby
        for donor in nearby:
            receivers_of.setdefault(donor, []).append(receiver)

    def distance(a: int, b: int) -> float:
        return float(np.hypot(*(coords[a] - coords[b])))

    heap: list[tuple[float, float, int, int]] = []

    def push(donor: int, receiver: int) -> None:
        value = gain(donor, receiver)
        if value > move_cost + _MIN_GAIN:
            # A igual mejora, primero el donante más cercano.
            heapq.heappush(heap, (-value, distance(donor, receiver), donor, receiver))

    for receiver, nearby in donors_of.items():
        for donor in nearby:
            push(donor, receiver)

    moves: list[PlannedMove] = []
    while heap:
        negative_gain, dist, donor, receiver = heapq.heappop(heap)
        current = gain(donor, receiver)
        if current <= move_cost + _MIN_GAIN:
            continue
        if -negative_gain - current > _MIN_GAIN:
            push(donor, receiver)  # la ganancia cambió desde que se encoló
            continue
        count[donor] -= 1
        count[receiver] += 1
        moves.append(
            PlannedMove(
                donor_site_id=sites[donor].site_id,
                receiver_site_id=sites[receiver].site_id,
                distance_km=round(dist, 4),
                overload_reduction=round(current, 4),
            )
        )
        # Cambió la cantidad de contenedores de los dos sitios: se reevalúan
        # todos sus pares, como donantes y como receptores.
        for site in (donor, receiver):
            for other_receiver in receivers_of.get(site, []):
                push(site, other_receiver)
            for other_donor in donors_of.get(site, []):
                push(other_donor, site)
    return moves


def _planar_km(sites: list[SiteState]) -> np.ndarray:
    """Proyecta lat/lng a kilómetros (equirectangular); exacto a escala de ciudad."""
    latitudes = np.radians([site.latitude for site in sites])
    longitudes = np.radians([site.longitude for site in sites])
    reference = math.cos(float(np.mean(latitudes)))
    return np.column_stack(
        (
            _EARTH_RADIUS_KM * longitudes * reference,
            _EARTH_RADIUS_KM * latitudes,
        )
    )

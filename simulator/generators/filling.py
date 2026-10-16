"""Llenado de los contenedores en cada tick: cuánto genera cada uno y el
desborde al sitio de enfrente cuando se llena."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime

import numpy as np

from simulator.domain.entities import Container
from simulator.simulation.scenario import ScenarioConfig
from simulator.zone_classifier import ZoneProfile, get_zone_classifier

logger = logging.getLogger(__name__)


def calibration_factor(profiles: Sequence[ZoneProfile]) -> float:
    """Factor fijo que lleva la media semanal de zone_mult a target_zone_mult
    (zone_profiles.yaml) si se sale de target ± tolerance_pct; si no, 1.

    La curva horaria y semanal de cada perfil tiene media 1 en la semana
    (ZoneProfile.temporal_normalization_factor), así que la media semanal de
    zone_mult es la media de los demand_multiplier. Se calcula una vez por
    simulación: normalizar la media de cada tick aplanaba la curva horaria de
    toda la ciudad (la madrugada generaba lo mismo que la noche).
    """
    zone_classifier = get_zone_classifier()
    if not profiles:
        return 1.0
    weekly_mean = sum(p.demand_multiplier for p in profiles) / len(profiles)
    target = zone_classifier.calibration_target
    tol = zone_classifier.calibration_tolerance_pct / 100.0
    lower, upper = target * (1.0 - tol), target * (1.0 + tol)
    factor = 1.0
    if weekly_mean > 0.0 and not (lower <= weekly_mean <= upper):
        factor = target / weekly_mean
    logger.info(
        "Calibración global: media semanal de zone_mult=%.4f, rango [%.4f, %.4f], "
        "factor=%.4f.",
        weekly_mean,
        lower,
        upper,
        factor,
    )
    return factor


def zone_multipliers(
    profiles: Sequence[ZoneProfile],
    timestamp: datetime,
    calibration: float,
    neighborhood_multiplier: Callable[[str], float] | None = None,
) -> np.ndarray:
    """Multiplicador de demanda de cada contenedor en este instante.

    Base automática: el ZoneProfile del radio de cada contenedor (zone_type y
    curva horaria/semanal propia) por el factor de calibración. Encima, el
    ajuste manual por barrio que manda el front, si hay: multiplica sobre la
    base en vez de reemplazarla, así un barrio sin ajuste sigue
    diferenciándose por zone_type y hora. El barrio sale del perfil del radio:
    site.zone es el código de radio, no el barrio que manda el front.
    """
    mults = np.array(
        [
            profile.effective_multiplier(timestamp.hour, timestamp.weekday())
            for profile in profiles
        ],
        dtype=np.float64,
    )
    mults = mults * calibration
    if neighborhood_multiplier:
        overrides = np.array(
            [neighborhood_multiplier(profile.barrio) for profile in profiles],
            dtype=np.float64,
        )
        mults = mults * overrides
    return mults


def filling_increments(
    demand_bases: np.ndarray,
    waste_factors: np.ndarray,
    zone_mults: np.ndarray,
    global_mult: float,
    config: ScenarioConfig,
    rng: np.random.Generator,
) -> np.ndarray:
    """Cuánto sube cada contenedor en el tick, en % de su capacidad: demanda
    base (%/hora) por la duración del tick, los factores y un ruido lognormal."""
    noises = rng.lognormal(0.0, 0.18, size=len(demand_bases))
    noises = np.maximum(0.2, noises)
    time_ratio = config.frequency_minutes / 60.0
    increments = (
        demand_bases
        * time_ratio
        * waste_factors
        * global_mult
        * zone_mults
        * config.overflow_stress_multiplier
        * noises
    )
    return np.round(increments, 4)


def apply_filling(
    levels: np.ndarray,
    increments: np.ndarray,
    containers: Sequence[Container],
    opposing_site_by_site_id: dict[str, str],
    containers_by_site_and_waste: dict[tuple[str, str], list[int]],
    containers_by_site: dict[str, list[int]],
) -> np.ndarray:
    """Niveles después de sumar los incrementos, con tope en 100.

    Lo que no entra en un contenedor lleno pasa, repartido en partes iguales,
    a los contenedores del sitio de enfrente (los del mismo tipo de residuo, o
    todos si no hay). Si no hay sitio de enfrente, se pierde.
    """
    tentative_levels = levels + increments
    spillovers: dict[int, float] = defaultdict(float)

    for i, container in enumerate(containers):
        opposing_site_id = opposing_site_by_site_id.get(container.site_id)
        if not opposing_site_id:
            continue
        if levels[i] >= 100.0:
            excess = increments[i]
        elif tentative_levels[i] > 100.0:
            excess = tentative_levels[i] - 100.0
        else:
            excess = 0.0
        if excess <= 0.0:
            continue
        targets = containers_by_site_and_waste.get(
            (opposing_site_id, container.waste_type)
        ) or containers_by_site.get(opposing_site_id, [])
        if targets:
            share = excess / len(targets)
            for target_idx in targets:
                spillovers[target_idx] += share

    for target_idx, added_level in spillovers.items():
        tentative_levels[target_idx] += added_level

    return np.minimum(100.0, tentative_levels)

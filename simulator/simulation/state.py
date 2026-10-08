"""Estado de una simulación: los datos fijos de cada contenedor, lo que cambia
tick a tick y el resultado de cada tick."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from simulator.domain.entities import (
    Alarm,
    CollectionEvent,
    Container,
    Device,
    Measurement,
    Site,
)
from simulator.generators.filling import calibration_factor
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import SimulationTopology
from simulator.zone_classifier import ZoneProfile, get_zone_classifier


def waste_type_factor(waste_type: str, factors: dict[str, float]) -> float:
    if waste_type in factors:
        return factors[waste_type]

    name = waste_type.casefold()
    if "vidrio" in name and "vidrio" in factors:
        return factors["vidrio"]
    if any(token in name for token in ("recicl", "seca", "verde")) and "reciclables" in factors:
        return factors["reciclables"]
    if any(token in name for token in ("humed", "húmed")) and "residuos_humedos" in factors:
        return factors["residuos_humedos"]

    return 1.0


@dataclass(frozen=True)
class SimulationResult:
    sites: list[Site]
    containers: list[Container]
    devices: list[Device]
    measurements: list[Measurement]
    collections: list[CollectionEvent]
    alarms: list[Alarm]


@dataclass(frozen=True)
class ContainerArrays:
    """Datos fijos de cada contenedor durante toda la simulación, en el orden de
    topology.containers: el índice i es el mismo contenedor en todos los
    arrays y listas. Se arman una vez en initialize."""

    sites: list[Site]
    devices: list[Device]
    demand_bases: np.ndarray
    waste_factors: np.ndarray
    heights: np.ndarray
    reading_offsets: np.ndarray
    zone_profiles: list[ZoneProfile]
    calibration_factor: float
    containers_by_site_and_waste: dict[tuple[str, str], list[int]]
    containers_by_site: dict[str, list[int]]


@dataclass
class SimulationState:
    topology: SimulationTopology
    site_by_id: dict[str, Site]
    device_by_container_id: dict[str, Device]
    levels: dict[str, float]
    batteries: dict[str, float]
    reading_offsets: dict[str, int]
    stuck_distances: dict[str, float]
    opposing_site_by_site_id: dict[str, str]
    arrays: ContainerArrays


def build_container_arrays(
    topology: SimulationTopology,
    site_by_id: dict[str, Site],
    device_by_container_id: dict[str, Device],
    reading_offsets: dict[str, int],
    config: ScenarioConfig,
) -> ContainerArrays:
    sites: list[Site] = []
    devices: list[Device] = []
    demand_bases: list[float] = []
    waste_factors: list[float] = []
    heights: list[float] = []
    offsets: list[int] = []
    zone_profiles: list[ZoneProfile] = []
    containers_by_site_and_waste: dict[tuple[str, str], list[int]] = defaultdict(list)
    containers_by_site: dict[str, list[int]] = defaultdict(list)

    zone_classifier = get_zone_classifier()
    for idx, container in enumerate(topology.containers):
        site = site_by_id[container.site_id]
        device = device_by_container_id[container.id]
        sites.append(site)
        devices.append(device)
        demand_bases.append(site.demand_base)
        waste_factors.append(
            waste_type_factor(container.waste_type, config.waste_type_factors)
        )
        heights.append(container.height_cm)
        offsets.append(reading_offsets[device.id])
        zone_profiles.append(zone_classifier.get_profile(site.zone))
        containers_by_site_and_waste[(container.site_id, container.waste_type)].append(
            idx
        )
        containers_by_site[container.site_id].append(idx)

    return ContainerArrays(
        sites=sites,
        devices=devices,
        demand_bases=np.array(demand_bases, dtype=np.float64),
        waste_factors=np.array(waste_factors, dtype=np.float64),
        heights=np.array(heights, dtype=np.float64),
        reading_offsets=np.array(offsets, dtype=np.int32),
        zone_profiles=zone_profiles,
        calibration_factor=calibration_factor(zone_profiles),
        containers_by_site_and_waste=dict(containers_by_site_and_waste),
        containers_by_site=dict(containers_by_site),
    )

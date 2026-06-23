from __future__ import annotations

import random

from app.digital_twin.synthetic_data.domain.entities import Container, Device, Site
from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig
from app.digital_twin.synthetic_data.topology import SimulationTopology

CABA_ZONES = (
    ("Palermo", -34.5832, -58.4243, 1.15),
    ("Recoleta", -34.5889, -58.3974, 1.05),
    ("Almagro", -34.6093, -58.4210, 1.0),
    ("Caballito", -34.6180, -58.4410, 1.08),
    ("Flores", -34.6282, -58.4633, 0.95),
)


def generate_synthetic_topology(
    config: ScenarioConfig,
    rng: random.Random,
) -> SimulationTopology:
    sites: list[Site] = []
    containers: list[Container] = []
    devices: list[Device] = []
    initial_levels: dict[str, float] = {}
    waste_types = tuple(config.waste_type_factors)

    for index in range(config.synthetic_site_count):
        zone, lat, lon, demand = CABA_ZONES[index % len(CABA_ZONES)]
        site = Site(
            id=f"SITE-{index + 1:04d}",
            name=f"{zone} {index + 1:03d}",
            zone=zone,
            latitude=round(lat + rng.uniform(-0.006, 0.006), 7),
            longitude=round(lon + rng.uniform(-0.006, 0.006), 7),
            demand_base=round(demand * rng.uniform(0.85, 1.25), 4),
        )
        sites.append(site)

        for container_index in range(config.synthetic_containers_per_site):
            waste_type = waste_types[(index + container_index) % len(waste_types)]
            container = Container(
                id=f"CONT-{index + 1:04d}-{container_index + 1:02d}",
                site_id=site.id,
                name=f"Container {index + 1:04d}-{container_index + 1:02d}",
                waste_type=waste_type,
                height_cm=rng.choice((120.0, 145.0, 165.0)),
                volume_m3=None,
            )
            device = Device(
                id=f"DEV-{index + 1:04d}-{container_index + 1:02d}",
                container_id=container.id,
            )
            containers.append(container)
            devices.append(device)
            initial_levels[container.id] = rng.uniform(5, 35)

    return SimulationTopology(
        sites=sites,
        containers=containers,
        devices=devices,
        initial_levels=initial_levels,
    )

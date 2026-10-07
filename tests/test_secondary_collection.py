from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from simulator.generators.collections import collect_probabilistic
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import Container, Device, SimulationTopology, Site

NIGHT = (21, 22, 23, 0, 1, 2, 3, 4, 5, 6)
MONDAY_NIGHT = datetime(2026, 10, 5, 21, 0)


def _topology(sites: int = 12, containers_per_site: int = 2) -> SimulationTopology:
    site_list = [
        Site(
            id=f"s{i}",
            name=f"Sitio {i}",
            zone="20350105",
            latitude=-34.604,
            longitude=-58.404,
            demand_base=1.0,
            address="SAN LUIS 2650",
        )
        for i in range(sites)
    ]
    containers = [
        Container(
            id=f"s{i}-c{j}",
            site_id=f"s{i}",
            name="RSU",
            waste_type="RSU Fracción Húmeda",
            height_cm=145,
        )
        for i in range(sites)
        for j in range(containers_per_site)
    ]
    return SimulationTopology(
        sites=site_list,
        containers=containers,
        devices=[Device(id=f"d-{c.id}", container_id=c.id) for c in containers],
        initial_levels={c.id: 50.0 for c in containers},
    )


def _simulator(**scenario) -> SyntheticDataSimulator:
    sim = SyntheticDataSimulator(
        ScenarioConfig(
            seed=3,
            collection_hours=NIGHT,
            no_collection_days=(5, 6),
            collection_probability=1.0,
            partial_collection_probability=0.0,
            **scenario,
        ),
        topology=_topology(),
    )
    sim.initialize()
    return sim


def _collect(sim, levels, timestamp):
    return collect_probabilistic(
        np.array(levels, dtype=np.float64),
        timestamp,
        sim.state.topology.containers,
        sim.state.arrays,
        sim.config,
        np.random.default_rng(0),
    )


class TestSecondaryCollection(unittest.TestCase):
    def test_outside_collection_hours_or_days_nothing_is_collected(self):
        sim = _simulator()
        n = len(sim.state.topology.containers)
        noon = _collect(sim, [50.0] * n, datetime(2026, 10, 5, 12, 0))
        saturday = _collect(sim, [50.0] * n, datetime(2026, 10, 10, 22, 0))
        self.assertFalse(noon.collected.any())
        self.assertFalse(saturday.collected.any())

    def test_each_site_is_collected_once_per_night_with_all_its_containers(self):
        sim = _simulator()
        containers = sim.state.topology.containers
        times_collected = {c.id: 0 for c in containers}
        for hour in range(len(NIGHT)):
            outcome = _collect(
                sim, [50.0] * len(containers), MONDAY_NIGHT + timedelta(hours=hour)
            )
            for idx in np.where(outcome.collected)[0]:
                times_collected[containers[idx].id] += 1
            # Los contenedores de un mismo sitio se recolectan juntos.
            for indices in sim.state.arrays.containers_by_site.values():
                self.assertEqual(len(set(outcome.collected[indices])), 1)

        self.assertEqual(set(times_collected.values()), {1})

    def test_empty_containers_are_not_collected_and_levels_never_go_up(self):
        sim = _simulator()
        containers = sim.state.topology.containers
        for hour in range(len(NIGHT)):
            levels = [0.0 if i % 2 else 5.0 for i in range(len(containers))]
            outcome = _collect(sim, levels, MONDAY_NIGHT + timedelta(hours=hour))
            self.assertFalse(outcome.collected[1::2].any())
            self.assertTrue((outcome.levels <= np.array(levels)).all())


class TestFleetFailureFallsBackToSecondaryCollection(unittest.TestCase):
    def test_without_route_stops_the_session_collects_without_trucks(self):
        # Sin CSV de rutas ninguna ruta queda con paradas: antes la flota quedaba
        # vacia y no se recolectaba nada, sin avisar.
        missing = Path("/no/existe.csv")
        with (
            patch(
                "simulator.trucks.truck_routes.resolve_routes_csv_path",
                return_value=missing,
            ),
            patch(
                "simulator.trucks.truck_routes.resolve_green_routes_csv_path",
                return_value=missing,
            ),
            self.assertLogs("simulator.simulation.engine", level="ERROR") as logs,
        ):
            sim = _simulator()

        self.assertIsNone(sim.truck_fleet)
        self.assertIn("método secundario", logs.output[0])
        collected = sum(
            len(sim.run_tick(MONDAY_NIGHT + timedelta(hours=h)).collections)
            for h in range(len(NIGHT))
        )
        self.assertEqual(collected, len(sim.state.topology.containers))


if __name__ == "__main__":
    unittest.main()

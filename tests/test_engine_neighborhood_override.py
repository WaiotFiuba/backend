from __future__ import annotations

import unittest
from datetime import datetime

from simulator.simulation.controls import ControlSnapshot
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import Container, Device, SimulationTopology, Site

# Radios censales de land_use_by_radio.csv con barrio conocido.
PALERMO_RADIO = "20980101"
ALMAGRO_RADIO = "20350105"


def _topology() -> SimulationTopology:
    sites = [
        Site(
            id=site_id,
            name=f"Sitio {site_id}",
            zone=radio,
            latitude=-34.6,
            longitude=-58.4,
            demand_base=2.0,
        )
        for site_id, radio in (("1", PALERMO_RADIO), ("2", ALMAGRO_RADIO))
    ]
    containers = [
        Container(
            id=site.id,
            site_id=site.id,
            name="RSU",
            waste_type="RSU Fracción Húmeda",
            height_cm=145,
        )
        for site in sites
    ]
    return SimulationTopology(
        sites=sites,
        containers=containers,
        devices=[Device(id=f"d{c.id}", container_id=c.id) for c in containers],
        initial_levels={c.id: 0.0 for c in containers},
    )


def _generated(zone_multiplier) -> dict[str, float]:
    sim = SyntheticDataSimulator(
        ScenarioConfig(seed=1, frequency_minutes=60, collection_hours=(3,)),
        topology=_topology(),
    )
    sim.initialize()
    sim.run_tick(datetime(2026, 10, 7, 12, 0), zone_multiplier=zone_multiplier)
    return dict(sim.state.levels)


class TestNeighborhoodOverride(unittest.TestCase):
    def test_front_override_applies_to_the_neighborhood_of_each_container(self):
        # Regresion: el engine buscaba el ajuste por site.zone, que es el codigo
        # de radio censal, y el front lo manda por barrio: nunca coincidia.
        snapshot = ControlSnapshot(
            speedup=1.0,
            global_current=1.0,
            global_target=1.0,
            zones=(("Palermo", 2.0, 2.0),),
        )

        base = _generated(None)
        adjusted = _generated(snapshot.zone_multiplier_fn())

        self.assertAlmostEqual(adjusted["1"], 2 * base["1"], places=2)
        self.assertAlmostEqual(adjusted["2"], base["2"], places=4)


if __name__ == "__main__":
    unittest.main()

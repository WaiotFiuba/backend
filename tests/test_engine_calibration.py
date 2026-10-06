from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from simulator.generators.filling import calibration_factor
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import Container, Device, SimulationTopology, Site
from simulator.zone_classifier import get_zone_classifier


def _profiles(*multipliers: float) -> list[SimpleNamespace]:
    return [SimpleNamespace(demand_multiplier=m) for m in multipliers]


class TestCalibrationFactor(unittest.TestCase):
    def test_brings_weekly_mean_to_target_when_out_of_range(self):
        target = get_zone_classifier().calibration_target
        factor = calibration_factor(_profiles(1.0, 1.2, 1.3))
        # media 1.1667, fuera de target ± tolerancia: la lleva al target.
        self.assertAlmostEqual(factor * (1.0 + 1.2 + 1.3) / 3, target, 6)

    def test_keeps_multipliers_when_weekly_mean_is_in_range(self):
        target = get_zone_classifier().calibration_target
        self.assertEqual(calibration_factor(_profiles(target, target)), 1.0)


class TestCalibrationKeepsDailyCurve(unittest.TestCase):
    def test_night_generates_less_than_evening(self):
        # Regresion: la calibracion normalizaba la media de cada tick, asi que
        # un contenedor solo generaba lo mismo a las 3 h que a las 21 h.
        site = Site(
            id="1",
            name="Sitio",
            zone="UNKNOWN",
            latitude=-34.6,
            longitude=-58.4,
            demand_base=4.0,
        )
        container = Container(
            id="1",
            site_id="1",
            name="RSU",
            waste_type="RSU Fracción Húmeda",
            height_cm=145,
        )
        topology = SimulationTopology(
            sites=[site],
            containers=[container],
            devices=[Device(id="d1", container_id="1")],
            initial_levels={"1": 0.0},
        )
        sim = SyntheticDataSimulator(
            ScenarioConfig(seed=1, frequency_minutes=60, collection_hours=(12,)),
            topology=topology,
        )
        sim.initialize()

        generated = {}
        for hour in (3, 21):
            sim.state.levels["1"] = 0.0
            sim.run_tick(datetime(2026, 10, 5, hour, 0))
            generated[hour] = sim.state.levels["1"]

        self.assertGreater(generated[21], 2 * generated[3])


if __name__ == "__main__":
    unittest.main()

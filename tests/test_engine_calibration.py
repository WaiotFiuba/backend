from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from simulator.generators.filling import calibration_factor
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import ScenarioConfig
from simulator.simulation.state import build_container_arrays, waste_type_factor
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
            sim.run_tick(datetime(2026, 10, 5, hour, 0))  # noqa: DTZ001
            generated[hour] = sim.state.levels["1"]

        self.assertGreater(generated[21], 2 * generated[3])


class TestWasteTypeFactors(unittest.TestCase):
    def test_recyclable_backend_waste_type_uses_recyclable_factor(self):
        config = ScenarioConfig()

        self.assertEqual(
            waste_type_factor(
                "RSU Fraccion Seca (Reciclables)", config.waste_type_factors
            ),
            config.waste_type_factors["reciclables"],
        )
        self.assertEqual(
            waste_type_factor(
                "RSU Fracci\u00f3n Seca (Reciclables)",
                config.waste_type_factors,
            ),
            config.waste_type_factors["reciclables"],
        )

    def test_container_arrays_apply_recyclable_factor(self):
        site = Site(
            id="S1",
            name="Sitio verde",
            zone="UNKNOWN",
            latitude=-34.6,
            longitude=-58.4,
            demand_base=2.0,
        )
        container = Container(
            id="C1",
            site_id="S1",
            name="RSU Fraccion Seca - Carga Lateral",
            waste_type="RSU Fraccion Seca (Reciclables)",
            height_cm=145,
        )
        device = Device(id="D1", container_id="C1")
        topology = SimulationTopology(
            sites=[site],
            containers=[container],
            devices=[device],
            initial_levels={"C1": 0.0},
        )

        arrays = build_container_arrays(
            topology=topology,
            site_by_id={"S1": site},
            device_by_container_id={"C1": device},
            reading_offsets={"D1": 0},
            config=ScenarioConfig(),
        )

        self.assertEqual(arrays.waste_factors[0], 0.55)


if __name__ == "__main__":
    unittest.main()

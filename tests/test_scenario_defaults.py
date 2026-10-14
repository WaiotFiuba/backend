from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from simulator.simulation.scenario import (
    ScenarioConfig,
    load_scenario,
    load_scenario_defaults,
    scenario_from_mapping,
    with_defaults,
)


class TestScenarioDefaultsFile(unittest.TestCase):
    def test_defaults_file_only_has_scenario_fields(self):
        fields = {f.name for f in dataclasses.fields(ScenarioConfig)}
        defaults = load_scenario_defaults()

        self.assertTrue(defaults)
        self.assertLessEqual(set(defaults), fields)

    def test_defaults_file_has_no_run_specific_keys(self):
        defaults = load_scenario_defaults()

        for key in ("start", "end", "periods", "container_limit"):
            self.assertNotIn(key, defaults)

    def test_missing_file_means_no_file_defaults(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(load_scenario_defaults(Path(directory) / "no.yaml"), {})


FILE_DEFAULTS = {
    "waste_type_factors": {"reciclables": 0.4},
    "collection_probability": 0.5,
    "collection_days": [0, 1, 2],
}


class TestWithDefaults(unittest.TestCase):
    def test_scenario_values_override_the_file(self):
        merged = with_defaults({"collection_probability": 0.9}, FILE_DEFAULTS)

        self.assertEqual(merged["collection_probability"], 0.9)
        self.assertEqual(merged["waste_type_factors"], {"reciclables": 0.4})

    def test_file_values_reach_the_config(self):
        config = scenario_from_mapping(with_defaults({}, FILE_DEFAULTS))

        self.assertEqual(config.waste_type_factors, {"reciclables": 0.4})
        self.assertEqual(config.collection_days, (0, 1, 2))

    def test_no_collection_days_in_scenario_replaces_file_collection_days(self):
        config = scenario_from_mapping(
            with_defaults({"no_collection_days": [6]}, FILE_DEFAULTS)
        )

        self.assertEqual(config.collection_days, (0, 1, 2, 3, 4, 5))
        self.assertEqual(config.no_collection_days, (6,))

    def test_null_does_not_override_a_file_value(self):
        merged = with_defaults({"collection_probability": None}, FILE_DEFAULTS)

        self.assertEqual(merged["collection_probability"], 0.5)

    def test_null_end_still_means_open_ended(self):
        merged = with_defaults({"end": None}, FILE_DEFAULTS)

        self.assertIn("end", merged)
        self.assertIsNone(merged["end"])

    def test_null_collection_days_keeps_the_file_days(self):
        config = scenario_from_mapping(
            with_defaults({"collection_days": None}, FILE_DEFAULTS)
        )

        self.assertEqual(config.collection_days, (0, 1, 2))

    def test_partial_dict_is_merged_with_the_file(self):
        defaults = {"waste_type_factors": {"reciclables": 0.4, "vidrio": 0.55}}
        merged = with_defaults({"waste_type_factors": {"reciclables": 0.8}}, defaults)

        self.assertEqual(
            merged["waste_type_factors"], {"reciclables": 0.8, "vidrio": 0.55}
        )

    def test_open_ended_session_stays_open_ended(self):
        config = scenario_from_mapping(
            with_defaults({"start": "2026-10-06T06:00:00", "end": None})
        )

        self.assertEqual(config.periods, 0)


class TestLoadScenario(unittest.TestCase):
    def test_cli_scenario_is_layered_over_the_defaults(self):
        config = load_scenario("simulator/config/semana_normal.yaml")
        defaults = load_scenario_defaults()

        self.assertEqual(config.name, "semana_normal")
        self.assertEqual(config.container_limit, 100)
        self.assertEqual(config.waste_type_factors, defaults["waste_type_factors"])
        self.assertEqual(
            config.collection_probability, defaults["collection_probability"]
        )

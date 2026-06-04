from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError

from app.digital_twin.synthetic_data.exporters.files import export_simulation
from app.digital_twin.synthetic_data.loaders.backend_http import (
    BackendConnectionError,
    load_topology_from_backend_api,
)
from app.digital_twin.synthetic_data.simulation.engine import SyntheticDataSimulator
from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig, scenario_from_mapping
from app.digital_twin.synthetic_data.topology import (
    BackendContainerRecord,
    topology_from_backend_api,
    topology_from_backend_records,
)
from app.digital_twin.synthetic_data.validation.checks import validate_result


class SyntheticDataSimulatorTest(unittest.TestCase):
    def test_generates_expected_amount_of_measurements(self) -> None:
        config = ScenarioConfig(seed=7, periods=24, synthetic_site_count=3, synthetic_containers_per_site=2)

        result = SyntheticDataSimulator(config).run()

        self.assertEqual(len(result.sites), 3)
        self.assertEqual(len(result.containers), 6)
        self.assertEqual(len(result.devices), 6)
        self.assertEqual(len(result.measurements), 24 * 6)

    def test_is_reproducible_with_seed(self) -> None:
        config = ScenarioConfig(seed=99, periods=8, synthetic_site_count=2, synthetic_containers_per_site=2)

        first = SyntheticDataSimulator(config).run()
        second = SyntheticDataSimulator(config).run()

        self.assertEqual(
            [measurement.to_record() for measurement in first.measurements],
            [measurement.to_record() for measurement in second.measurements],
        )

    def test_sensor_readings_have_device_level_jitter(self) -> None:
        config = ScenarioConfig(
            seed=15,
            periods=1,
            synthetic_site_count=3,
            synthetic_containers_per_site=2,
            reading_jitter_minutes=10,
        )

        result = SyntheticDataSimulator(config).run()

        reading_minutes = {measurement.timestamp.minute for measurement in result.measurements}
        self.assertGreater(len(reading_minutes), 1)

    def test_validation_report_is_ok_for_default_ranges(self) -> None:
        config = ScenarioConfig(seed=11, periods=12, synthetic_site_count=2, synthetic_containers_per_site=1)

        report = validate_result(SyntheticDataSimulator(config).run())

        self.assertTrue(report.ok, report.errors)
        self.assertGreater(report.metrics["measurements"], 0)

    def test_scenario_end_date_calculates_periods(self) -> None:
        config = scenario_from_mapping(
            {
                "start": "2026-01-01T00:00:00",
                "end": "2026-01-01T03:00:00",
                "frequency_minutes": 60,
            }
        )

        self.assertEqual(config.start, datetime(2026, 1, 1, 0, 0, 0))
        self.assertEqual(config.end, datetime(2026, 1, 1, 3, 0, 0))
        self.assertEqual(config.periods, 3)

    def test_scenario_end_date_must_be_after_start(self) -> None:
        with self.assertRaises(ValueError):
            scenario_from_mapping(
                {
                    "start": "2026-01-01T00:00:00",
                    "end": "2026-01-01T00:00:00",
                }
            )

    def test_can_simulate_using_backend_container_records(self) -> None:
        topology = topology_from_backend_records(
            [
                BackendContainerRecord(
                    id=123,
                    site_id="GCBA-001",
                    site_name="Contenedor real",
                    latitude=-34.6,
                    longitude=-58.4,
                    current_level=41,
                    device_imei="imei-123",
                    container_type="negro",
                    waste_type="residuos_humedos",
                    height_cm=150,
                    volume_m3=3.2,
                )
            ]
        )
        config = ScenarioConfig(seed=17, periods=2)

        result = SyntheticDataSimulator(config, topology=topology).run()

        self.assertEqual(result.containers[0].id, "123")
        self.assertEqual(result.sites[0].id, "GCBA-001")
        self.assertEqual(result.devices[0].id, "imei-123")
        self.assertEqual(result.measurements[0].container_id, "123")

    def test_can_build_topology_from_backend_api_payload(self) -> None:
        topology = topology_from_backend_api(
            [
                {
                    "id": 456,
                    "site_id": "SITE-BACKEND-456",
                    "latitude": -34.61,
                    "longitude": -58.42,
                    "current_level": 28,
                    "available": True,
                    "container_type": {
                        "name": "Campana",
                        "height_cm": 150,
                        "volume_m3": 3.2,
                        "overflow_zone_cm": 20,
                        "waste_types": [{"name": "reciclables", "color": "#00AA00"}],
                    },
                    "site_name": "Sitio real",
                    "device_imei": "imei-456",
                }
            ]
        )

        self.assertEqual(topology.sites[0].id, "SITE-BACKEND-456")
        self.assertEqual(topology.containers[0].id, "456")
        self.assertEqual(topology.containers[0].height_cm, 150)
        self.assertEqual(topology.containers[0].volume_m3, 3.2)
        self.assertEqual(topology.containers[0].waste_type, "reciclables")
        self.assertEqual(topology.devices[0].id, "imei-456")
        self.assertEqual(topology.initial_levels["456"], 28)

    def test_measurement_export_uses_backend_metadata(self) -> None:
        topology = topology_from_backend_api(
            [
                {
                    "id": 456,
                    "site_id": "SITE-BACKEND-456",
                    "site_name": "Sitio 456",
                    "device_imei": "imei-456",
                    "latitude": -34.61,
                    "longitude": -58.42,
                    "current_level": 28,
                    "available": True,
                    "container_type": {
                        "name": "Container Media Manzana",
                        "height_cm": 150,
                        "volume_m3": 3.2,
                        "waste_types": [{"name": "Organico", "color": "#00AA00"}],
                    },
                }
            ]
        )
        result = SyntheticDataSimulator(ScenarioConfig(seed=3, periods=1), topology=topology).run()

        with TemporaryDirectory() as output_dir:
            paths = export_simulation(result, output_dir, include_topology=False)
            content = paths["measurements"].read_text(encoding="utf-8")

        self.assertIn("imei-456,,", content)
        self.assertIn("Container Media Manzana", content)
        self.assertIn("Sitio 456", content)
        self.assertIn("Organico", content)

    def test_exports_time_series_as_csv_by_default(self) -> None:
        config = ScenarioConfig(seed=21, periods=2, synthetic_site_count=1, synthetic_containers_per_site=1)
        result = SyntheticDataSimulator(config).run()

        with TemporaryDirectory() as output_dir:
            paths = export_simulation(result, output_dir)

            self.assertEqual(paths["measurements"].name, "measurements.csv")
            self.assertEqual(paths["collections"].name, "collections.csv")
            self.assertEqual(paths["alarms"].name, "alarms.csv")
            self.assertEqual(paths["sites"].name, "sites.csv")
            self.assertEqual(paths["containers"].name, "containers.csv")
            self.assertEqual(paths["devices"].name, "devices.csv")
            self.assertTrue(paths["measurements"].read_text(encoding="utf-8").startswith("imei,m_id,reading_date,"))

    def test_can_skip_topology_exports(self) -> None:
        config = ScenarioConfig(seed=21, periods=2, synthetic_site_count=1, synthetic_containers_per_site=1)
        result = SyntheticDataSimulator(config).run()

        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            paths = export_simulation(result, output_dir, include_topology=False)

            self.assertNotIn("sites", paths)
            self.assertNotIn("containers", paths)
            self.assertNotIn("devices", paths)
            self.assertFalse((output_path / "sites.csv").exists())
            self.assertFalse((output_path / "containers.csv").exists())
            self.assertFalse((output_path / "devices.csv").exists())
            self.assertTrue((output_path / "measurements.csv").exists())

    def test_export_removes_previous_known_outputs(self) -> None:
        config = ScenarioConfig(seed=21, periods=2, synthetic_site_count=1, synthetic_containers_per_site=1)
        result = SyntheticDataSimulator(config).run()

        with TemporaryDirectory() as output_dir:
            output_path = Path(output_dir)
            (output_path / "measurements.parquet").write_text("old", encoding="utf-8")
            (output_path / "collections.parquet").write_text("old", encoding="utf-8")
            (output_path / "alarms.parquet").write_text("old", encoding="utf-8")

            export_simulation(result, output_dir, export_format="csv")

            self.assertFalse((output_path / "measurements.parquet").exists())
            self.assertFalse((output_path / "collections.parquet").exists())
            self.assertFalse((output_path / "alarms.parquet").exists())
            self.assertTrue((output_path / "measurements.csv").exists())

    def test_backend_loader_reports_connection_errors_without_raw_urlerror(self) -> None:
        with patch(
            "app.digital_twin.synthetic_data.loaders.backend_http.urlopen",
            side_effect=URLError("[Errno 111] Connection refused"),
        ):
            with self.assertRaises(BackendConnectionError) as context:
                load_topology_from_backend_api("http://localhost:8000")

        self.assertIn("No se pudo conectar al backend", str(context.exception))

    def test_backend_loader_fetches_paginated_containers(self) -> None:
        responses = [
            _FakeResponse(
                [
                    {
                        "id": 1,
                        "site_id": "SITE-1",
                        "latitude": -34.61,
                        "longitude": -58.42,
                        "current_level": 10,
                        "available": True,
                        "container_type": {"name": "Campana", "waste_types": []},
                    }
                ]
            ),
            _FakeResponse([]),
        ]

        with patch(
            "app.digital_twin.synthetic_data.loaders.backend_http.urlopen",
            side_effect=responses,
        ) as urlopen_mock:
            topology = load_topology_from_backend_api("http://backend", page_size=1)

        self.assertEqual(len(topology.containers), 1)
        self.assertIn("limit=1", urlopen_mock.call_args_list[0].args[0].full_url)
        self.assertIn("offset=0", urlopen_mock.call_args_list[0].args[0].full_url)
        self.assertIn("offset=1", urlopen_mock.call_args_list[1].args[0].full_url)

class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        import json

        return json.dumps(self.payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()

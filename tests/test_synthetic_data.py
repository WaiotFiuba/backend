from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import URLError

from app.digital_twin.synthetic_data.exporters.files import export_simulation
from app.digital_twin.synthetic_data.loaders.backend_http import (
    BackendConnectionError,
    load_topology_from_backend_api,
)
from app.digital_twin.synthetic_data.simulation.engine import SyntheticDataSimulator
from app.digital_twin.synthetic_data.simulation.scenario import (
    ScenarioConfig,
    scenario_from_mapping,
)
from app.digital_twin.synthetic_data.topology import (
    BackendContainerRecord,
    topology_from_backend_api,
    topology_from_backend_records,
)
from app.digital_twin.synthetic_data.transport.backend_http import (
    DeliveryReport,
    StreamingInterrupted,
    send_measurements_batch,
    send_result_batch,
    stream_result,
)
from app.digital_twin.synthetic_data.worker import (
    _deliver_tick_measurements,
    _remaining_tick_delay,
)
from app.digital_twin.synthetic_data.validation.checks import validate_result
from app.models.map.container import Container as MapContainer
from app.models.map.container_type import ContainerType
from app.models.map.waste_type import WasteType
from app.schemas.digital_twin import TelemetryFlags, TelemetryIngestPayload, TelemetryValues
from app.services.digital_twin_ingest_service import _data_level_row, ingest_telemetry_batch


class SyntheticDataSimulatorTest(unittest.TestCase):
    def test_generates_expected_amount_of_measurements(self) -> None:
        config = ScenarioConfig(
            seed=7,
            periods=24,
            synthetic_site_count=3,
            synthetic_containers_per_site=2,
        )

        result = SyntheticDataSimulator(config).run()

        self.assertEqual(len(result.sites), 3)
        self.assertEqual(len(result.containers), 6)
        self.assertEqual(len(result.devices), 6)
        self.assertEqual(len(result.measurements), 24 * 6)

    def test_is_reproducible_with_seed(self) -> None:
        config = ScenarioConfig(
            seed=99,
            periods=8,
            synthetic_site_count=2,
            synthetic_containers_per_site=2,
        )

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

        reading_minutes = {
            measurement.timestamp.minute for measurement in result.measurements
        }
        self.assertGreater(len(reading_minutes), 1)

    def test_validation_report_is_ok_for_default_ranges(self) -> None:
        config = ScenarioConfig(
            seed=11,
            periods=12,
            synthetic_site_count=2,
            synthetic_containers_per_site=1,
        )

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
        result = SyntheticDataSimulator(
            ScenarioConfig(seed=3, periods=1),
            topology=topology,
        ).run()

        with TemporaryDirectory() as output_dir:
            paths = export_simulation(result, output_dir, include_topology=False)
            content = paths["measurements"].read_text(encoding="utf-8")

        self.assertIn("imei-456,,", content)
        self.assertIn("Container Media Manzana", content)
        self.assertIn("Sitio 456", content)
        self.assertIn("Organico", content)

    def test_exports_time_series_as_csv_by_default(self) -> None:
        config = ScenarioConfig(
            seed=21,
            periods=2,
            synthetic_site_count=1,
            synthetic_containers_per_site=1,
        )
        result = SyntheticDataSimulator(config).run()

        with TemporaryDirectory() as output_dir:
            paths = export_simulation(result, output_dir)

            self.assertEqual(paths["measurements"].name, "measurements.csv")
            self.assertEqual(paths["collections"].name, "collections.csv")
            self.assertEqual(paths["alarms"].name, "alarms.csv")
            self.assertEqual(paths["sites"].name, "sites.csv")
            self.assertEqual(paths["containers"].name, "containers.csv")
            self.assertEqual(paths["devices"].name, "devices.csv")
            self.assertTrue(
                paths["measurements"]
                .read_text(encoding="utf-8")
                .startswith("imei,m_id,reading_date,")
            )

    def test_can_skip_topology_exports(self) -> None:
        config = ScenarioConfig(
            seed=21,
            periods=2,
            synthetic_site_count=1,
            synthetic_containers_per_site=1,
        )
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
        config = ScenarioConfig(
            seed=21,
            periods=2,
            synthetic_site_count=1,
            synthetic_containers_per_site=1,
        )
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

    def test_can_send_generated_measurements_in_batch(self) -> None:
        result = SyntheticDataSimulator(
            ScenarioConfig(
                seed=4,
                periods=5,
                synthetic_site_count=1,
                synthetic_containers_per_site=1,
            )
        ).run()
        requests = []

        def fake_post(url, payload, token, timeout_seconds):
            requests.append((url, payload, token, timeout_seconds))
            return {"updated": len(payload["measurements"]), "not_found": 0}

        report = send_result_batch(
            result,
            backend_url="http://backend",
            batch_size=2,
            token="token",
            post_json=fake_post,
        )

        self.assertEqual(report.sent, 5)
        self.assertEqual(report.updated, 5)
        self.assertEqual(report.requests, 3)
        self.assertEqual(requests[0][0], "http://backend/digital-twin/telemetry/batch")
        self.assertEqual(requests[0][2], "token")
        self.assertEqual(len(requests[0][1]["measurements"]), 2)

    def test_worker_can_send_one_tick_as_a_single_batch(self) -> None:
        result = SyntheticDataSimulator(
            ScenarioConfig(
                seed=4,
                periods=1,
                synthetic_site_count=1,
                synthetic_containers_per_site=3,
            )
        ).run()
        requests = []

        def fake_post(url, payload, token, timeout_seconds):
            requests.append((url, payload, token, timeout_seconds))
            return {"updated": len(payload["measurements"]), "not_found": 0}

        report = send_measurements_batch(
            result.measurements,
            backend_url="http://backend",
            batch_size=2,
            post_json=fake_post,
        )

        self.assertEqual(report.sent, 3)
        self.assertEqual(report.updated, 3)
        self.assertEqual(report.requests, 2)
        self.assertEqual(len(requests[0][1]["measurements"]), 2)
        self.assertEqual(len(requests[1][1]["measurements"]), 1)

    def test_can_stream_generated_measurements_in_timestamp_order(self) -> None:
        result = SyntheticDataSimulator(
            ScenarioConfig(
                seed=8,
                periods=3,
                synthetic_site_count=1,
                synthetic_containers_per_site=1,
            )
        ).run()
        posted_timestamps = []
        sleeps = []

        def fake_post(url, payload, token, timeout_seconds):
            posted_timestamps.append(payload["timestamp"])
            return {"updated": 1, "not_found": 0}

        report = stream_result(
            result,
            backend_url="http://backend",
            delay_seconds=0.25,
            sleep=sleeps.append,
            post_json=fake_post,
        )

        self.assertEqual(report.sent, 3)
        self.assertEqual(report.requests, 3)
        self.assertEqual(posted_timestamps, sorted(posted_timestamps))
        self.assertEqual(sleeps, [0.25, 0.25])

    def test_stream_speedup_calculates_delay_correctly(self) -> None:
        result = SyntheticDataSimulator(
            ScenarioConfig(
                seed=8,
                periods=3,
                frequency_minutes=30,
                synthetic_site_count=1,
                synthetic_containers_per_site=1,
            )
        ).run()
        posted_timestamps = []
        sleeps = []

        def fake_post(url, payload, token, timeout_seconds):
            posted_timestamps.append(payload["timestamp"])
            return {"updated": 1, "not_found": 0}

        report = stream_result(
            result,
            backend_url="http://backend",
            speedup=30.0,
            sleep=sleeps.append,
            post_json=fake_post,
        )

        self.assertEqual(report.sent, 3)
        self.assertEqual(sleeps, [1.0, 1.0])

    def test_stream_interruption_keeps_partial_report(self) -> None:
        result = SyntheticDataSimulator(
            ScenarioConfig(
                seed=8,
                periods=3,
                synthetic_site_count=1,
                synthetic_containers_per_site=1,
            )
        ).run()

        def fake_post(url, payload, token, timeout_seconds):
            return {"updated": 1, "not_found": 0}

        def interrupt(_seconds):
            raise KeyboardInterrupt

        with self.assertRaises(StreamingInterrupted) as context:
            stream_result(
                result,
                backend_url="http://backend",
                delay_seconds=1,
                sleep=interrupt,
                post_json=fake_post,
            )

        self.assertEqual(context.exception.report.sent, 1)
        self.assertEqual(context.exception.report.updated, 1)
        self.assertEqual(context.exception.report.requests, 1)

    def test_ingest_payload_maps_to_data_level_shape(self) -> None:
        container_type = ContainerType(
            id=7,
            name="RSU Humeda",
            height_cm=150,
            volume_m3=3.2,
        )
        container_type.waste_types = [WasteType(id=9, name="RSU Fraccion Humeda")]
        container = MapContainer(
            id=123,
            site_id="SITE-123",
            site_name="Sitio 123",
            description="Contenedor 123",
            latitude=-34.6,
            longitude=-58.4,
            current_level=41,
            available=True,
            device_imei="imei-123",
            container_type=container_type,
        )
        payload = TelemetryIngestPayload(
            device_id="imei-123",
            container_id="123",
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            telemetry=TelemetryValues(
                fill_level_pct=50,
                ultrasonic_distance_cm=75,
                battery_pct=12,
                signal_rssi_dbm=-80,
                temperature_c=24,
                acceleration_g=3,
            ),
            flags=TelemetryFlags(is_collection_detected=True, anomaly=None),
        )

        row = _data_level_row(payload, container)

        self.assertEqual(row.__tablename__, "data_level")
        self.assertEqual(row.imei, "imei-123")
        self.assertEqual(row.reading_date, datetime(2026, 1, 1, 12, 0, 0))
        self.assertEqual(row.reported_height, 75)
        self.assertTrue(row.garbage_collection_alarm)
        self.assertTrue(row.crash_alarm)
        self.assertTrue(row.reported_low_consumption_voltage)
        self.assertEqual(row.container_current_level_old, 41)
        self.assertEqual(row.container_current_level, 50)
        self.assertEqual(row.container_current_m3, 1.6)
        self.assertEqual(row.container_type_id, 7)
        self.assertEqual(row.container_type_name, "RSU Humeda")
        self.assertEqual(row.waste_type_id, 9)
        self.assertEqual(row.waste_type_name, "RSU Fraccion Humeda")


class SimulatorWorkerTest(unittest.IsolatedAsyncioTestCase):
    def test_tick_wait_subtracts_delivery_time(self) -> None:
        self.assertEqual(_remaining_tick_delay(15, 60, 4), 11)
        self.assertEqual(_remaining_tick_delay(15, 60, 20), 0)

    async def test_checks_session_state_before_each_telemetry_batch(self) -> None:
        result = SyntheticDataSimulator(
            ScenarioConfig(
                seed=4,
                periods=1,
                synthetic_site_count=1,
                synthetic_containers_per_site=5,
            )
        ).run()
        runnable = AsyncMock(return_value=object())

        with (
            patch(
                "app.digital_twin.synthetic_data.worker._wait_until_runnable",
                runnable,
            ),
            patch(
                "app.digital_twin.synthetic_data.worker.send_measurements_batch",
                return_value=DeliveryReport(
                    sent=2,
                    updated=2,
                    not_found=0,
                    requests=1,
                ),
            ) as send_batch,
            patch(
                "app.digital_twin.synthetic_data.worker._record_delivered_measurements",
                AsyncMock(),
            ) as record_delivered,
        ):
            report = await _deliver_tick_measurements(
                simulation_id=7,
                measurements=result.measurements,
                backend_url="http://backend",
                batch_size=2,
            )

        self.assertIsNotNone(report)
        self.assertEqual(runnable.await_count, 3)
        self.assertEqual(send_batch.call_count, 3)
        self.assertEqual(record_delivered.await_count, 3)


class DigitalTwinIngestTest(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_marks_updated_container_with_next_change_version(self) -> None:
        container_type = ContainerType(
            id=7,
            name="RSU Humeda",
            height_cm=150,
            volume_m3=3.2,
        )
        container_type.waste_types = []
        container = MapContainer(
            id=123,
            site_id="SITE-123",
            latitude=-34.6,
            longitude=-58.4,
            current_level=10,
            available=True,
            container_type=container_type,
        )
        payload = TelemetryIngestPayload(
            device_id="imei-123",
            container_id="123",
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            telemetry=TelemetryValues(
                fill_level_pct=50,
                ultrasonic_distance_cm=75,
                battery_pct=90,
                signal_rssi_dbm=-80,
                temperature_c=24,
                acceleration_g=1,
            ),
            flags=TelemetryFlags(is_collection_detected=False, anomaly=None),
        )
        db = MagicMock()
        db.commit = AsyncMock()

        with (
            patch(
                "app.services.digital_twin_ingest_service._find_container",
                AsyncMock(return_value=container),
            ),
            patch(
                "app.services.digital_twin_ingest_service._find_neighborhood",
                AsyncMock(return_value=None),
            ),
        ):
            result = await ingest_telemetry_batch(db, [payload])

        self.assertEqual(result.updated, 1)
        self.assertEqual(container.current_level, 50)
        self.assertEqual(container.change_version.name, "nextval")
        db.commit.assert_awaited_once()


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

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import simulator.trucks.collection_schedule as cs

SCHEDULE = {"R1": {"route_id": "R1", "total_stops": 2, "stops": []}}
EMPTY_SCHEDULE = {"R1": {"route_id": "R1", "total_stops": 0, "stops": []}}


class TestEnsureCollectionSchedule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.containers = base / "contenedores_negros.json"
        self.routes = base / "rutas.csv"
        self.target = base / "routes" / "collection_schedule.json"
        self.containers.write_text("{}")
        self.routes.write_text("route\n")
        self.patches = [
            patch.object(cs, "CANDIDATE_PATHS", [self.target]),
            patch.object(cs, "containers_json_path", lambda: self.containers),
            patch(
                "simulator.trucks.truck_routes.resolve_routes_csv_path",
                lambda: self.routes,
            ),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        cs._CACHED_SCHEDULE = None
        self.tmp.cleanup()

    def _write_target(self, data: dict, age_seconds: int) -> None:
        """Escribe el cronograma con una antiguedad relativa a las entradas."""
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(json.dumps(data))
        inputs_mtime = self.containers.stat().st_mtime
        os.utime(self.target, (inputs_mtime - age_seconds,) * 2)

    def test_up_to_date_schedule_is_not_regenerated(self):
        # El cronograma es mas nuevo que sus entradas.
        self._write_target({"viejo": True}, age_seconds=-60)
        with patch.object(cs, "generate_all_schedules") as generate:
            self.assertFalse(cs.ensure_collection_schedule())
        generate.assert_not_called()

    def test_regenerates_when_inputs_are_newer(self):
        # El cronograma es mas viejo que sus entradas.
        self._write_target({"viejo": True}, age_seconds=60)
        with patch.object(cs, "generate_all_schedules", return_value=SCHEDULE):
            self.assertTrue(cs.ensure_collection_schedule())
        self.assertEqual(json.loads(self.target.read_text()), SCHEDULE)

    def test_generates_when_schedule_is_missing(self):
        with patch.object(cs, "generate_all_schedules", return_value=SCHEDULE):
            self.assertTrue(cs.ensure_collection_schedule())
        self.assertEqual(json.loads(self.target.read_text()), SCHEDULE)

    def test_keeps_schedule_when_an_input_is_missing(self):
        self._write_target({"viejo": True}, age_seconds=60)
        self.containers.unlink()
        with patch.object(cs, "generate_all_schedules") as generate:
            self.assertFalse(cs.ensure_collection_schedule())
        generate.assert_not_called()
        self.assertEqual(json.loads(self.target.read_text()), {"viejo": True})

    def test_never_overwrites_with_an_empty_schedule(self):
        self._write_target({"viejo": True}, age_seconds=60)
        with patch.object(cs, "generate_all_schedules", return_value=EMPTY_SCHEDULE):
            self.assertFalse(cs.ensure_collection_schedule())
        self.assertEqual(json.loads(self.target.read_text()), {"viejo": True})

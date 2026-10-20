from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from simulator.simulation.controls import ControlSnapshot


def _session(scenario: dict) -> MagicMock:
    return MagicMock(
        scenario=scenario,
        speedup=60.0,
        global_demand_current=1.0,
        global_demand_target=1.0,
        zone_overrides=[],
    )


class TestControlSnapshotEnd(unittest.TestCase):
    def test_the_end_comes_from_the_session_scenario(self):
        snapshot = ControlSnapshot.from_session(
            _session({"end": "2026-10-08T06:00:00"})
        )
        self.assertEqual(snapshot.end, datetime(2026, 10, 8, 6, 0))

    def test_without_end_there_is_none(self):
        for scenario in ({}, {"end": None}, {"end": ""}):
            with self.subTest(scenario=scenario):
                self.assertIsNone(ControlSnapshot.from_session(_session(scenario)).end)

    def test_the_end_drops_the_time_zone_like_the_scenario(self):
        snapshot = ControlSnapshot.from_session(
            _session({"end": "2026-10-08T06:00:00+00:00"})
        )
        self.assertEqual(snapshot.end, datetime(2026, 10, 8, 6, 0))


class TestControlSnapshotCollectionSchedule(unittest.TestCase):
    def test_the_schedule_comes_from_the_session_scenario(self):
        snapshot = ControlSnapshot.from_session(
            _session({"collection_hours": [21, 22], "collection_days": [0, 2]})
        )
        self.assertEqual(snapshot.collection_hours, (21, 22))
        self.assertEqual(snapshot.collection_days, (0, 2))

    def test_without_schedule_there_is_no_change(self):
        snapshot = ControlSnapshot.from_session(_session({}))
        self.assertIsNone(snapshot.collection_hours)
        self.assertIsNone(snapshot.collection_days)


if __name__ == "__main__":
    unittest.main()

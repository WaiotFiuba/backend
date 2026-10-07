from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.services.simulation_session_service as svc
from app.core.map_database import MapBase
from app.models.map.simulation import SimulationSession, SimulationZoneOverride
from app.schemas.digital_twin import SimulationCreate
from simulator.simulation.scenario import (
    ScenarioConfig,
    scenario_from_mapping,
    scenario_to_record,
)


# En sqlite, una PK BigInteger no se autoincrementa (solo INTEGER lo hace).
# create_simulation no fija el id, asi que en estos tests se compila como INTEGER.
@compiles(BigInteger, "sqlite")
def _bigint_as_integer_on_sqlite(type_, compiler, **kw):
    return "INTEGER"


class TestScenarioRecord(unittest.TestCase):
    def test_record_round_trips_through_scenario_from_mapping(self):
        configs = [
            ScenarioConfig(start=datetime(2026, 10, 6, 6, 0)),
            scenario_from_mapping(
                {
                    "start": "2026-10-06T06:00:00",
                    "end": "2026-10-08T06:00:00",
                    "frequency_minutes": 30,
                    "collection_hours": [21, 22],
                    "collection_days": [0, 2, 4],
                }
            ),
        ]
        for config in configs:
            self.assertEqual(scenario_from_mapping(scenario_to_record(config)), config)

    def test_record_is_json_friendly(self):
        record = scenario_to_record(ScenarioConfig(start=datetime(2026, 10, 6, 6, 0)))
        self.assertEqual(record["start"], "2026-10-06T06:00:00")
        self.assertIsInstance(record["collection_hours"], list)


class TestSimulationScenarioBackend(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: MapBase.metadata.create_all(
                    sync_conn,
                    tables=[
                        SimulationSession.__table__,
                        SimulationZoneOverride.__table__,
                    ],
                )
            )
        self.session_maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.patches = [
            patch.object(svc, "reset_database_container_levels", AsyncMock()),
            patch.object(svc, "_validate_neighborhoods", AsyncMock()),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.engine.dispose()

    async def _create(self, scenario: dict, **kwargs):
        payload = SimulationCreate(scenario=scenario, **kwargs)
        async with self.session_maker() as db:
            return await svc.create_simulation(db, payload, user_id=1)

    async def test_stores_scenario_as_sent_without_simulator_defaults(self):
        created = await self._create(
            {
                "start": "2026-10-06T06:00:00",
                "frequency_minutes": 60,
                "collection_hours": [21, 22],
                "daily_kg_per_person": 1.8,
            }
        )
        self.assertEqual(
            created.scenario,
            {
                "start": "2026-10-06T06:00:00",
                "frequency_minutes": 60,
                "collection_hours": [21, 22],
                "daily_kg_per_person": 1.8,
            },
        )
        self.assertNotIn("seed", created.scenario)
        self.assertEqual(created.total_periods, 0)

    async def test_start_time_overrides_scenario_start(self):
        created = await self._create(
            {"start": "2026-01-01T00:00:00"},
            start_time=datetime(2026, 10, 6, 6, 0),
        )
        self.assertEqual(created.scenario["start"], "2026-10-06T06:00:00")

    async def test_invalid_scenarios_are_rejected_with_422(self):
        invalid = [
            {"start": "no-es-una-fecha"},
            {"start": "2026-10-06T06:00:00", "end": "2026-10-05T06:00:00"},
            {"collection_hours": [21, 25]},
            {"frequency_minutes": 0},
        ]
        for scenario in invalid:
            with self.subTest(scenario=scenario):
                with self.assertRaises(HTTPException) as ctx:
                    await self._create(scenario)
                self.assertEqual(ctx.exception.status_code, 422)

    async def test_worker_reports_effective_scenario_and_total_periods(self):
        created = await self._create({"start": "2026-10-06T06:00:00"})
        effective = scenario_to_record(
            scenario_from_mapping({**created.scenario, "periods": 24})
        )
        async with self.session_maker() as db:
            updated = await svc.update_simulation_progress(
                db,
                created.id,
                status="running",
                scenario=effective,
                total_periods=24,
            )
        self.assertEqual(updated.scenario, effective)
        self.assertEqual(updated.total_periods, 24)

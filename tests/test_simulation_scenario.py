from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import BigInteger, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.services.simulation_session_service as svc
from app.core.map_database import MapBase
from app.models.map.data_level import DataLevel
from app.models.map.optimization import (
    OptimizationWhatIfLevel,
    OptimizationWhatIfTotals,
    RedistributionPlan,
)
from app.models.map.simulation import SimulationSession, SimulationZoneOverride
from app.models.map.zone_profile_layer import ZoneProfileDefaults
from app.services.map import zone_profile_service
from app.schemas.digital_twin import SimulationControlsUpdate, SimulationCreate
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
                        DataLevel.__table__,
                        RedistributionPlan.__table__,
                        OptimizationWhatIfLevel.__table__,
                        OptimizationWhatIfTotals.__table__,
                        ZoneProfileDefaults.__table__,
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

    async def test_worker_restart_only_fails_sessions_it_had_taken(self):
        async with self.session_maker() as db:
            for status, claimed in (
                ("pending", False),
                ("pending", True),
                ("running", True),
                ("running", False),
            ):
                db.add(
                    SimulationSession(
                        status=status,
                        scenario={},
                        total_periods=0,
                        created_by=1,
                        claimed_at=datetime(2026, 1, 1) if claimed else None,
                    )
                )
            await db.commit()

        async with self.session_maker() as db:
            failed = await svc.fail_interrupted_sessions(db)
            statuses = (
                (
                    await db.execute(
                        select(SimulationSession.status).order_by(SimulationSession.id)
                    )
                )
                .scalars()
                .all()
            )

        # La pending sin tomar sigue esperando; las demas eran del worker.
        self.assertEqual(failed, 3)
        self.assertEqual(statuses, ["pending", "failed", "failed", "failed"])

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

    async def test_new_simulation_clears_previous_readings(self):
        async with self.session_maker() as db:
            db.add(
                DataLevel(
                    id=1,
                    reading_date=datetime(2026, 1, 1, 10, 0),
                    container_current_level=50,
                    fire_alarm=False,
                    freeze_alarm=False,
                    crash_alarm=False,
                    garbage_collection_alarm=False,
                    reported_low_consumption_voltage=False,
                    reported_high_consumption_voltage=False,
                )
            )
            await db.commit()

        await self._create({"start": "2026-10-06T06:00:00"})

        async with self.session_maker() as db:
            self.assertEqual(await db.scalar(select(func.count(DataLevel.id))), 0)

    async def test_new_simulation_resets_whatif_but_keeps_the_plan(self):
        async with self.session_maker() as db:
            plan = RedistributionPlan(status="active_whatif", config={}, moves=[])
            db.add(plan)
            await db.flush()
            db.add(
                OptimizationWhatIfLevel(
                    plan_id=plan.id,
                    container_id=1,
                    original_site_id=1,
                    optimized_site_id=2,
                    virtual_level=70.5,
                    last_reading=datetime(2026, 1, 1, 10, 0),
                    rate_ewma=4.0,
                    overflow_hours=3.0,
                    real_overflow_hours=5.0,
                )
            )
            db.add(
                OptimizationWhatIfTotals(
                    plan_id=plan.id,
                    observed_hours=10.0,
                    critical_site_hours=40.0,
                    last_observed_at=datetime(2026, 1, 1, 10, 0),
                    last_critical_sites=4,
                )
            )
            await db.commit()

        await self._create({"start": "2026-10-06T06:00:00"})

        async with self.session_maker() as db:
            plan = await db.get(RedistributionPlan, plan.id)
            level = (await db.scalars(select(OptimizationWhatIfLevel))).one()
            totals = await db.get(OptimizationWhatIfTotals, plan.id)
        self.assertEqual(plan.status, "active_whatif")
        self.assertEqual(
            (
                totals.observed_hours,
                totals.critical_site_hours,
                totals.last_observed_at,
                totals.last_critical_sites,
            ),
            (0, 0, None, None),
        )
        # Se conserva la distribución del plan; el estado vuelve a cero.
        self.assertEqual((level.original_site_id, level.optimized_site_id), (1, 2))
        self.assertEqual(
            (
                level.virtual_level,
                level.last_reading,
                level.rate_ewma,
                level.overflow_hours,
                level.real_overflow_hours,
            ),
            (0, None, None, 0, 0),
        )

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

    async def _running(self, simulated_time: datetime):
        created = await self._create({"start": "2026-10-06T06:00:00"})
        async with self.session_maker() as db:
            await svc.update_simulation_progress(
                db,
                created.id,
                status="running",
                scenario=scenario_to_record(
                    scenario_from_mapping({**created.scenario, "periods": 24})
                ),
                total_periods=24,
                simulated_time=simulated_time,
            )
        return created

    async def _set_end(self, simulation_id: int, end: datetime):
        async with self.session_maker() as db:
            return await svc.update_simulation_controls(
                db, simulation_id, SimulationControlsUpdate(end=end)
            )

    async def test_the_new_end_is_stored_for_the_simulator(self):
        created = await self._running(datetime(2026, 10, 6, 12, 0))

        updated = await self._set_end(created.id, datetime(2026, 10, 8, 6, 0))

        self.assertEqual(updated.scenario["end"], "2026-10-08T06:00:00")
        # Los períodos los recalcula el simulador, no el backend.
        self.assertEqual(updated.total_periods, 24)

    async def test_the_end_cannot_be_before_the_current_simulated_time(self):
        created = await self._running(datetime(2026, 10, 7, 6, 0))

        with self.assertRaises(HTTPException) as ctx:
            await self._set_end(created.id, datetime(2026, 10, 7, 5, 0))

        self.assertEqual(ctx.exception.status_code, 422)

    async def test_waste_type_multipliers_use_the_map_filter_keys(self):
        created = await self._running(datetime(2026, 10, 6, 12, 0))
        async with self.session_maker() as db:
            updated = await svc.update_simulation_controls(
                db,
                created.id,
                SimulationControlsUpdate(waste_type_multipliers={"reciclable": 2.0}),
            )

        self.assertEqual(
            scenario_from_mapping(updated.scenario).waste_type_multipliers,
            {"reciclable": 2.0},
        )
        for invalid in ({"reciclables": 2.0}, {"humedo": 0}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    SimulationControlsUpdate(waste_type_multipliers=invalid)

    async def test_the_collection_schedule_is_stored_for_the_simulator(self):
        created = await self._running(datetime(2026, 10, 6, 12, 0))
        schedule = SimulationControlsUpdate(
            collection_hours=[8, 9], collection_days=[0, 2]
        )
        async with self.session_maker() as db:
            updated = await svc.update_simulation_controls(db, created.id, schedule)

        config = scenario_from_mapping(updated.scenario)
        self.assertEqual(config.collection_hours, (8, 9))
        self.assertEqual(config.no_collection_days, (1, 3, 4, 5, 6))
        with self.assertRaises(ValueError):
            SimulationControlsUpdate(collection_hours=[24])

    async def test_the_zone_profile_defaults_are_saved_and_read(self):
        async with self.session_maker() as db:
            empty = await zone_profile_service.get_zone_profile_defaults(db)
            self.assertEqual(empty, {})
            for profiles in (
                {"office": {"demand_multiplier": 0.85}},
                {"office": {"demand_multiplier": 0.9}, "other": {}},
            ):
                await zone_profile_service.save_zone_profile_defaults(db, profiles)
            # El ultimo publicado reemplaza al anterior.
            self.assertEqual(
                await zone_profile_service.get_zone_profile_defaults(db),
                {"office": {"demand_multiplier": 0.9}, "other": {}},
            )

    async def test_zone_profiles_are_checked_against_the_published_defaults(self):
        changes = {"office": {"demand_multiplier": 0.7, "hour_weights": {"13": 2.0}}}
        # Sin valores por defecto publicados todavia, no se puede validar: pasa.
        created = await self._create({"zone_profiles": changes})
        self.assertEqual(created.scenario["zone_profiles"], changes)

        async with self.session_maker() as db:
            await zone_profile_service.save_zone_profile_defaults(
                db, {"office": {"demand_multiplier": 0.85}, "other": {}}
            )
            self.assertEqual(
                await zone_profile_service.get_zone_profile_defaults(db),
                {"office": {"demand_multiplier": 0.85}, "other": {}},
            )
        await self._create({"zone_profiles": changes})
        with self.assertRaises(HTTPException) as ctx:
            await self._create({"zone_profiles": {"oficinas": {}}})
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, {"unknown_zone_profiles": ["oficinas"]})

    async def test_zone_profile_values_are_validated(self):
        invalid = [
            {"office": {"hour_weights": {"24": 1.0}}},
            {"office": {"weekday_factors": {"7": 1.0}}},
            {"office": {"demand_multiplier": 0}},
            {"office": {"hour_weights": {"13": -1}}},
            {"office": {"weekend_factor": 0.5}},
        ]
        for zone_profiles in invalid:
            with self.subTest(zone_profiles=zone_profiles):
                with self.assertRaises(HTTPException) as ctx:
                    await self._create({"zone_profiles": zone_profiles})
                self.assertEqual(ctx.exception.status_code, 422)

    async def test_the_end_cannot_change_before_the_simulation_starts(self):
        created = await self._create({"start": "2026-10-06T06:00:00"})

        with self.assertRaises(HTTPException) as ctx:
            await self._set_end(created.id, datetime(2026, 10, 8, 6, 0))

        self.assertEqual(ctx.exception.status_code, 409)

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from geoalchemy2.elements import WKTElement
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.map_database import MapBase
from app.models.map.container import Container
from app.models.map.container_type import ContainerType, container_type_waste_types
from app.models.map.data_level import DataLevel
from app.models.map.optimization import OptimizationWhatIfLevel, RedistributionPlan
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.schemas.map.optimization import OptimizationConfig
from app.services.map.redistribution_overload_planner import PlannedMove
from app.services.map.redistribution_overload_service import (
    _assign_containers,
    generate_overload_plan,
    group_by_compatibility,
    load_overload_candidates,
)

TABLES = [
    WasteType.__table__,
    ContainerType.__table__,
    container_type_waste_types,
    Site.__table__,
    Container.__table__,
    DataLevel.__table__,
    RedistributionPlan.__table__,
    OptimizationWhatIfLevel.__table__,
]
NO_CAPACITY = OptimizationConfig(algorithm="overload", apply_capacity_constraints=False)
START = datetime(2026, 1, 1, tzinfo=UTC)


def _register_sqlite_stubs(engine):
    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection, _record):
        for name in (
            "RecoverGeometryColumn",
            "InitSpatialMetaData",
            "AddGeometryColumn",
            "DiscardGeometryColumn",
        ):
            dbapi_connection.create_function(name, -1, lambda *a: 1)
        for name in ("CheckSpatialIndex", "DisableSpatialIndex"):
            dbapi_connection.create_function(name, -1, lambda *a: None)
        dbapi_connection.create_function("GeomFromEWKT", 1, lambda x: str(x))
        dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda x: str(x))
        for name in ("ST_AsEWKB", "ST_AsBinary", "AsEWKB", "AsBinary"):
            dbapi_connection.create_function(
                name, 1, lambda x: str(x).encode() if x else None
            )


class TestLoadOverloadCandidates(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        _register_sqlite_stubs(self.engine)
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda sc: MapBase.metadata.create_all(sc, tables=TABLES)
            )
        self.session_maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        async with self.session_maker() as session:
            await self._seed(session)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, session: AsyncSession):
        session.add(WasteType(id=1, name="RSU", color="#333"))
        session.add_all(
            [
                ContainerType(id=1, name="Lateral", height_cm=145, volume_m3=3.2),
                ContainerType(id=2, name="Trasera", height_cm=120, volume_m3=1.1),
            ]
        )
        await session.flush()
        # (sitio, tipo de contenedor, cantidad, niveles horarios o None)
        layout = [
            (1, 1, 1, [30, 50, 70, 90]),  # llega a zona crítica: receptor
            (2, 1, 3, [10, 15, 20, 25]),  # 3 contenedores bajos: donante
            (3, 1, 1, [10, 20, 30, 40]),  # 1 contenedor bajo: no participa
            (4, 2, 2, [10, 15, 20, 25]),  # donante de otro tipo de contenedor
            (5, 1, 2, None),  # sin historia: no participa
        ]
        container_id = reading_id = 0
        for site_id, type_id, count, levels in layout:
            lat, lng = -34.60 + site_id * 0.001, -58.40
            session.add(
                Site(
                    id=site_id,
                    name=f"Sitio {site_id}",
                    latitude=lat,
                    longitude=lng,
                    geom=WKTElement(f"POINT({lng} {lat})", srid=4326),
                    waste_type_id=1,
                )
            )
            await session.flush()
            for _ in range(count):
                container_id += 1
                session.add(
                    Container(
                        id=container_id,
                        site_id=site_id,
                        latitude=lat,
                        longitude=lng,
                        geom=WKTElement(f"POINT({lng} {lat})", srid=4326),
                        current_level=(levels or [0])[-1],
                        container_type_id=type_id,
                    )
                )
                for hour, level in enumerate(levels or []):
                    reading_id += 1
                    session.add(
                        DataLevel(
                            id=reading_id,
                            reading_date=START + timedelta(hours=hour),
                            container_id=container_id,
                            container_current_level=level,
                            fire_alarm=False,
                            freeze_alarm=False,
                            crash_alarm=False,
                            garbage_collection_alarm=False,
                            reported_low_consumption_voltage=False,
                            reported_high_consumption_voltage=False,
                        )
                    )
        await session.commit()

    async def test_loads_only_sites_that_can_receive_or_donate(self):
        async with self.session_maker() as session:
            candidates = await load_overload_candidates(
                session, apply_capacity_constraints=False
            )

        by_id = {c.state.site_id: c for c in candidates}
        self.assertEqual(set(by_id), {1, 2, 4})
        self.assertEqual(by_id[2].state.containers, 3)
        self.assertIsNone(by_id[1].state.max_containers)
        self.assertEqual(by_id[1].average_level, 60)
        # El sitio receptor tiene sobrecarga: 1 hora al 90% en zona crítica.
        self.assertEqual(by_id[1].state.profile.hours(1).critical, 1)

    async def test_groups_sites_by_waste_and_container_type(self):
        async with self.session_maker() as session:
            candidates = await load_overload_candidates(
                session, apply_capacity_constraints=False
            )

        groups = group_by_compatibility(candidates)
        self.assertEqual(
            {
                group: sorted(c.state.site_id for c in members)
                for group, members in groups.items()
            },
            {(1, 1): [1, 2], (1, 2): [4]},
        )

    async def test_uses_the_physical_capacity_when_requested(self):
        async with self.session_maker() as session:
            candidates = await load_overload_candidates(session)

        from app.services.map.site_capacity_service import get_site_capacity_service

        capacity = get_site_capacity_service()
        for candidate in candidates:
            state = candidate.state
            expected, _ = capacity.evaluate_site(
                state.latitude, state.longitude, state.containers
            )
            self.assertEqual(state.max_containers, expected)

    async def test_generates_a_plan_from_the_idle_site_to_the_overloaded_one(self):
        async with self.session_maker() as session:
            plan = await generate_overload_plan(session, NO_CAPACITY)

        # El sitio 1 (1 contenedor, llega al 90%) recibe uno del sitio 2 (3
        # contenedores al 25%). El sitio 4 es de otro tipo y no participa.
        self.assertEqual(len(plan.moves), 1)
        move = plan.moves[0]
        self.assertEqual((move.from_site_id, move.to_site_id), (2, 1))
        self.assertIn(move.container_id, {2, 3, 4})
        self.assertEqual(
            (move.receiver_fill_before, move.receiver_fill_after), (60, 30)
        )
        self.assertEqual(move.donor_fill_before, 17.5)
        self.assertAlmostEqual(move.donor_fill_after, 26.2, places=1)
        self.assertEqual(move.overload_reduction, 1)
        # Antes: 1 hora en zona crítica (el 90%); con 2 contenedores, ninguna.
        self.assertEqual(
            (plan.critical_hours_before, plan.critical_hours_after), (1, 0)
        )
        self.assertEqual(plan.overflow_hours_before, 0)
        self.assertEqual(plan.expected_improvement, 1)
        self.assertEqual(plan.config.algorithm, "overload")

    async def test_move_impact_is_cumulative_for_the_same_donor(self):
        async with self.session_maker() as session:
            candidates = await load_overload_candidates(
                session, apply_capacity_constraints=False
            )
            by_site = {c.state.site_id: c for c in candidates}
            planned = [PlannedMove(2, 1, 0.1, 1.0), PlannedMove(2, 1, 0.1, 0.5)]
            moves = await _assign_containers(session, planned, by_site)

        first, second = moves
        self.assertNotEqual(first.container_id, second.container_id)
        self.assertEqual(second.donor_fill_before, first.donor_fill_after)
        self.assertEqual(second.receiver_fill_before, first.receiver_fill_after)
        self.assertEqual(second.receiver_fill_after, 20)  # 60% repartido en 3

    async def test_plan_endpoint_accepts_the_overload_algorithm(self):
        from httpx import ASGITransport, AsyncClient

        from app.core.map_database import get_map_db
        from app.main import app

        async def override_get_map_db():
            async with self.session_maker() as s:
                yield s

        app.dependency_overrides[get_map_db] = override_get_map_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                res = await client.post(
                    "/map/optimization/plan",
                    json={"algorithm": "overload", "apply_capacity_constraints": False},
                )
        finally:
            app.dependency_overrides.pop(get_map_db, None)

        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["status"], "active_whatif")
        self.assertEqual(len(data["moves"]), 1)
        self.assertEqual(data["critical_hours_before"], 1)

    async def test_cpu_work_runs_off_the_event_loop(self):
        import asyncio
        from unittest.mock import patch

        import app.services.map.redistribution_overload_service as service

        offloaded = []
        original_to_thread = asyncio.to_thread

        async def recording_to_thread(function, *args, **kwargs):
            offloaded.append(function.__name__)
            return await original_to_thread(function, *args, **kwargs)

        with patch.object(service.asyncio, "to_thread", recording_to_thread):
            async with self.session_maker() as session:
                plan = await generate_overload_plan(session, NO_CAPACITY)

        self.assertEqual(offloaded, ["_build_candidates", "_plan_by_group"])
        self.assertEqual(len(plan.moves), 1)

    async def test_cpu_work_does_not_hold_a_db_connection(self):
        import asyncio
        from unittest.mock import patch

        import app.services.map.redistribution_overload_service as service

        holding_connection = {}
        original_to_thread = asyncio.to_thread

        async with self.session_maker() as session:

            async def recording_to_thread(function, *args, **kwargs):
                # Sin transacción abierta, la sesión no retiene una conexión.
                holding_connection[function.__name__] = session.in_transaction()
                return await original_to_thread(function, *args, **kwargs)

            with patch.object(service.asyncio, "to_thread", recording_to_thread):
                plan = await generate_overload_plan(session, NO_CAPACITY)

        self.assertEqual(
            holding_connection, {"_build_candidates": False, "_plan_by_group": False}
        )
        self.assertEqual(len(plan.moves), 1)

    async def test_hourly_site_levels_fallback_matches_the_python_aggregation(self):
        from app.services.site_projection.history import (
            fetch_hourly_site_levels,
        )

        async with self.session_maker() as session:
            hourly = await fetch_hourly_site_levels(
                session, [1, 2], lookback_days=28, end_time=START + timedelta(hours=3)
            )

        self.assertEqual(hourly[1].levels, [30, 50, 70, 90])
        self.assertEqual(hourly[2].levels, [10, 15, 20, 25])
        self.assertEqual(hourly[1].collected, [False] * 4)

"""Tests para el módulo de optimización de distribución de contenedores."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from geoalchemy2.elements import WKTElement
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.map_database import MapBase
from app.models.map.container import Container
from app.models.map.container_type import ContainerType, container_type_waste_types
from app.models.map.data_level import DataLevel
from app.models.map.optimization import (
    OptimizationWhatIfLevel,
    OptimizationWhatIfTotals,
    RedistributionPlan,
)
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.schemas.map.optimization import OptimizationConfig
from app.services.map.optimization_service import (
    _classify,
    haversine_distance,
)


# ---------------------------------------------------------------------------
# Tests unitarios puros (sin DB)
# ---------------------------------------------------------------------------


def _move(container_id: int, from_site_id: int, to_site_id: int) -> dict:
    """Movimiento mínimo con los campos que exige el esquema de respuesta."""
    return {
        "container_id": container_id,
        "from_site_id": from_site_id,
        "from_site_name": f"Sitio {from_site_id}",
        "to_site_id": to_site_id,
        "to_site_name": f"Sitio {to_site_id}",
        "distance_km": 0.5,
    }


class TestHaversineDistance(unittest.TestCase):
    def test_same_point_is_zero(self):
        self.assertAlmostEqual(
            haversine_distance(-34.60, -58.38, -34.60, -58.38), 0.0, places=5
        )

    def test_known_distance_obelisco_to_congreso(self):
        # Obelisco (-34.6037, -58.3816) -> Congreso (-34.6099, -58.3925)
        dist = haversine_distance(-34.6037, -58.3816, -34.6099, -58.3925)
        # Distancia real ~1.2 km
        self.assertGreater(dist, 0.8)
        self.assertLess(dist, 1.6)

    def test_large_distance_bsas_to_cordoba(self):
        # Buenos Aires (-34.60, -58.38) -> Córdoba (-31.42, -64.18)
        dist = haversine_distance(-34.60, -58.38, -31.42, -64.18)
        # ~650 km
        self.assertGreater(dist, 600)
        self.assertLess(dist, 700)

    def test_symmetry(self):
        d1 = haversine_distance(-34.60, -58.38, -34.61, -58.39)
        d2 = haversine_distance(-34.61, -58.39, -34.60, -58.38)
        self.assertAlmostEqual(d1, d2, places=10)


class TestClassifySites(unittest.TestCase):
    def test_critical(self):
        self.assertEqual(_classify(0.90), "critical")
        self.assertEqual(_classify(0.85), "critical")
        self.assertEqual(_classify(1.0), "critical")

    def test_high(self):
        self.assertEqual(_classify(0.70), "high")
        self.assertEqual(_classify(0.65), "high")

    def test_normal(self):
        self.assertEqual(_classify(0.50), "normal")
        self.assertEqual(_classify(0.35), "normal")

    def test_low(self):
        self.assertEqual(_classify(0.25), "low")
        self.assertEqual(_classify(0.15), "low")

    def test_idle(self):
        self.assertEqual(_classify(0.10), "idle")
        self.assertEqual(_classify(0.0), "idle")


class TestOptimizationConfig(unittest.TestCase):
    def test_defaults(self):
        config = OptimizationConfig()
        self.assertIsNone(config.window_days)
        self.assertEqual(config.max_distance_km, 2.0)
        self.assertEqual(config.min_utilization_donor, 0.35)
        self.assertEqual(config.min_utilization_receiver, 0.65)
        self.assertEqual(config.target_utilization, 0.50)
        self.assertEqual(config.algorithm, "greedy")
        self.assertEqual(config.move_cost_hours_per_week, 20.0)

    def test_custom_config(self):
        config = OptimizationConfig(window_days=30, max_distance_km=5.0, algorithm="lp")
        self.assertEqual(config.window_days, 30)
        self.assertEqual(config.max_distance_km, 5.0)
        self.assertEqual(config.algorithm, "lp")

    def test_invalid_values_are_rejected(self):
        from pydantic import ValidationError

        invalid = [
            {"window_days": 0},
            {"window_days": -7},
            {"max_distance_km": 0},
            {"max_distance_km": 50},
            {"algorithm": "simulated_annealing"},
            {"move_cost_hours_per_week": -1},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                OptimizationConfig(**values)


# ---------------------------------------------------------------------------
# Tests de integración con DB in-memory (SQLite)
# ---------------------------------------------------------------------------


def _register_sqlite_stubs(engine):
    """Registra funciones stub de GIS para SQLite."""

    @event.listens_for(engine.sync_engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        dbapi_connection.create_function("RecoverGeometryColumn", -1, lambda *a: 1)
        dbapi_connection.create_function("InitSpatialMetaData", -1, lambda *a: 1)
        dbapi_connection.create_function("AddGeometryColumn", -1, lambda *a: 1)
        dbapi_connection.create_function("DiscardGeometryColumn", -1, lambda *a: 1)
        dbapi_connection.create_function("CheckSpatialIndex", -1, lambda *a: None)
        dbapi_connection.create_function("DisableSpatialIndex", -1, lambda *a: None)
        dbapi_connection.create_function("bool_or", 1, lambda x: bool(x))
        dbapi_connection.create_function("GeomFromEWKT", 1, lambda x: str(x))
        dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda x: str(x))
        dbapi_connection.create_function(
            "ST_AsEWKB", 1, lambda x: str(x).encode() if x else None
        )
        dbapi_connection.create_function(
            "ST_AsBinary", 1, lambda x: str(x).encode() if x else None
        )
        dbapi_connection.create_function(
            "AsEWKB", 1, lambda x: str(x).encode() if x else None
        )
        dbapi_connection.create_function(
            "AsBinary", 1, lambda x: str(x).encode() if x else None
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
    OptimizationWhatIfTotals.__table__,
]


class TestOptimizationMetrics(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda sc: MapBase.metadata.drop_all(sc, tables=TABLES))
        await self.engine.dispose()

    async def _seed_scenario(self, session: AsyncSession):
        """
        Crea un escenario con:
        - Sitio A: 2 contenedores, niveles bajos (5%, 10%) -> idle/low
        - Sitio B: 2 contenedores, niveles altos (90%, 95%) -> critical
        - Sitio C: 2 contenedores, niveles medios (40%, 50%) -> normal
        Todos del mismo waste_type y container_type, a <2km entre sí.
        """
        wt = WasteType(id=1, name="RSU Fracción Húmeda", color="#333")
        ct = ContainerType(id=1, name="Carga Lateral", height_cm=145, volume_m3=3.2)
        ct.waste_types.append(wt)
        session.add_all([wt, ct])
        await session.flush()

        # Sitio A: Palermo (-34.5800, -58.4200)
        site_a = Site(
            id=1,
            name="Sitio A - Bajo uso",
            latitude=-34.5800,
            longitude=-58.4200,
            geom=WKTElement("POINT(-58.4200 -34.5800)", srid=4326),
            waste_type_id=1,
        )
        # Sitio B: 1.5km al este (-34.5800, -58.4040)
        site_b = Site(
            id=2,
            name="Sitio B - Saturado",
            latitude=-34.5800,
            longitude=-58.4040,
            geom=WKTElement("POINT(-58.4040 -34.5800)", srid=4326),
            waste_type_id=1,
        )
        # Sitio C: entre A y B (-34.5800, -58.4120)
        site_c = Site(
            id=3,
            name="Sitio C - Normal",
            latitude=-34.5800,
            longitude=-58.4120,
            geom=WKTElement("POINT(-58.4120 -34.5800)", srid=4326),
            waste_type_id=1,
        )
        session.add_all([site_a, site_b, site_c])
        await session.flush()

        # Contenedores Sitio A (bajo uso)
        for i, level in enumerate([5, 10], start=1):
            session.add(
                Container(
                    id=i,
                    site_id=1,
                    serie_id=f"A{i}",
                    latitude=-34.5800,
                    longitude=-58.4200,
                    current_level=level,
                    available=True,
                    container_type_id=1,
                    geom=WKTElement("POINT(-58.4200 -34.5800)", srid=4326),
                )
            )
        # Contenedores Sitio B (saturado)
        for i, level in enumerate([90, 95], start=3):
            session.add(
                Container(
                    id=i,
                    site_id=2,
                    serie_id=f"B{i}",
                    latitude=-34.5800,
                    longitude=-58.4040,
                    current_level=level,
                    available=True,
                    container_type_id=1,
                    geom=WKTElement("POINT(-58.4040 -34.5800)", srid=4326),
                )
            )
        # Contenedores Sitio C (normal)
        for i, level in enumerate([40, 50], start=5):
            session.add(
                Container(
                    id=i,
                    site_id=3,
                    serie_id=f"C{i}",
                    latitude=-34.5800,
                    longitude=-58.4120,
                    current_level=level,
                    available=True,
                    container_type_id=1,
                    geom=WKTElement("POINT(-58.4120 -34.5800)", srid=4326),
                )
            )
        await session.flush()

        # DataLevel: lecturas históricas para reforzar patrones
        ts = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        for dl_id, (container_id, site_id, level) in enumerate(
            [
                (1, 1, 5),
                (2, 1, 10),  # Sitio A bajo
                (3, 2, 90),
                (4, 2, 95),  # Sitio B alto
                (5, 3, 40),
                (6, 3, 50),  # Sitio C normal
            ],
            start=1,
        ):
            session.add(
                DataLevel(
                    id=dl_id,
                    reading_date=ts,
                    container_id=container_id,
                    container_current_level=level,
                    site_id=str(site_id),
                    fire_alarm=False,
                    freeze_alarm=False,
                    crash_alarm=False,
                    garbage_collection_alarm=False,
                    reported_low_consumption_voltage=False,
                    reported_high_consumption_voltage=False,
                )
            )
        await session.commit()

    async def test_compute_metrics_classifies_correctly(self):
        """Verifica que las métricas clasifican sitios saturados, normales e infrautilizados."""
        from app.services.map.optimization_service import (
            compute_site_utilization_metrics,
        )

        async with self.session_maker() as session:
            await self._seed_scenario(session)

            config = OptimizationConfig()
            result = await compute_site_utilization_metrics(session, config)

            self.assertEqual(result.total_sites, 3)

            by_id = {s.site_id: s for s in result.sites}

            # Sitio A (bajo uso): score bajo
            self.assertIn(1, by_id)
            self.assertLess(by_id[1].utilization_score, 0.35)
            self.assertIn(by_id[1].category, ("low", "idle"))

            # Sitio B (saturado): score alto
            self.assertIn(2, by_id)
            self.assertGreater(by_id[2].utilization_score, 0.35)

            # Sitio C (normal): score medio
            self.assertIn(3, by_id)

    async def test_metrics_exclude_soft_deleted_containers(self):
        from app.services.map.optimization_service import (
            compute_site_utilization_metrics,
        )

        async with self.session_maker() as session:
            await self._seed_scenario(session)
            session.add(
                Container(
                    id=99,
                    site_id=1,
                    latitude=-34.5800,
                    longitude=-58.4100,
                    current_level=100,
                    container_type_id=1,
                    deleted_at=datetime(2026, 9, 1, tzinfo=UTC),
                    geom=WKTElement("POINT(-58.4100 -34.5800)", srid=4326),
                )
            )
            await session.commit()

            result = await compute_site_utilization_metrics(
                session, OptimizationConfig()
            )

            site_a = next(s for s in result.sites if s.site_id == 1)
            self.assertEqual(site_a.container_count, 2)  # sin el dado de baja

    async def test_metrics_window_is_relative_to_the_latest_reading(self):
        """La ventana se mide desde la última lectura, no desde el reloj del servidor."""
        from app.services.map.optimization_service import (
            compute_site_utilization_metrics,
        )

        async with self.session_maker() as session:
            await self._seed_scenario(session)  # lecturas del 2026-09-01 10:00 UTC

            result = await compute_site_utilization_metrics(
                session, OptimizationConfig(window_days=7)
            )

            latest = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
            self.assertEqual(result.window_end.replace(tzinfo=UTC), latest)
            self.assertEqual(
                result.window_start.replace(tzinfo=UTC), latest - timedelta(days=7)
            )
            # Las lecturas del sitio B (90% y 95%) entran en la ventana.
            by_id = {s.site_id: s for s in result.sites}
            self.assertEqual(by_id[2].peak_fill_rate, 1.0)

    async def test_generate_plan_greedy_moves_from_low_to_high(self):
        """Verifica que el plan greedy mueve contenedores de sitios de bajo uso a saturados."""
        from app.services.map.optimization_service import generate_redistribution_plan

        async with self.session_maker() as session:
            await self._seed_scenario(session)

            config = OptimizationConfig(
                max_distance_km=5.0,
                algorithm="greedy",
            )
            plan = await generate_redistribution_plan(session, config)

            self.assertEqual(plan.status, "draft")

            if plan.total_containers_moved > 0:
                # Verificar que los movimientos van a Sitio B (saturado) desde donantes
                donor_ids = set()
                for move in plan.moves:
                    donor_ids.add(move.from_site_id)
                    self.assertEqual(
                        move.to_site_id,
                        2,
                        "Los movimientos deben dirigirse al sitio saturado (B)",
                    )
                    self.assertIn(
                        move.from_site_id,
                        {1, 3},
                        "Los donantes deben ser Sitio A (1) o Sitio C (3)",
                    )
                    self.assertLessEqual(
                        move.distance_km,
                        config.max_distance_km,
                        "La distancia no debe exceder el máximo configurado",
                    )

                # Verificar que el plan se persistió en DB
                db_plan = await session.get(RedistributionPlan, plan.id)
                self.assertIsNotNone(db_plan)
                self.assertEqual(db_plan.status, "draft")

    async def test_generate_plan_lp_moves_from_low_to_high(self):
        """Verifica que el solver de programación lineal (LP) genera movimientos válidos."""
        from app.services.map.optimization_service import generate_redistribution_plan

        async with self.session_maker() as session:
            await self._seed_scenario(session)

            config = OptimizationConfig(
                max_distance_km=5.0,
                algorithm="lp",
            )
            plan = await generate_redistribution_plan(session, config)

            self.assertEqual(plan.status, "draft")
            if plan.total_containers_moved > 0:
                for move in plan.moves:
                    self.assertEqual(move.to_site_id, 2)
                    self.assertIn(move.from_site_id, {1, 3})
                    self.assertLessEqual(move.distance_km, config.max_distance_km)
                    self.assertIsNotNone(move.from_lat)
                    self.assertIsNotNone(move.to_lat)

    async def test_generate_plan_respects_type_compatibility(self):
        """Verifica que no se mueven contenedores entre sitios de distinto tipo."""
        from app.services.map.optimization_service import generate_redistribution_plan

        async with self.session_maker() as session:
            wt1 = WasteType(id=10, name="Húmedo", color="#111")
            wt2 = WasteType(id=20, name="Seco", color="#222")
            ct1 = ContainerType(id=10, name="Lateral", height_cm=145, volume_m3=3.2)
            ct2 = ContainerType(id=20, name="Campana", height_cm=160, volume_m3=2.5)
            ct1.waste_types.append(wt1)
            ct2.waste_types.append(wt2)
            session.add_all([wt1, wt2, ct1, ct2])
            await session.flush()

            # Sitio X: húmedo, bajo uso
            site_x = Site(
                id=10,
                name="Sitio X",
                latitude=-34.58,
                longitude=-58.42,
                geom=WKTElement("POINT(-58.42 -34.58)", srid=4326),
                waste_type_id=10,
            )
            # Sitio Y: seco, saturado
            site_y = Site(
                id=20,
                name="Sitio Y",
                latitude=-34.58,
                longitude=-58.41,
                geom=WKTElement("POINT(-58.41 -34.58)", srid=4326),
                waste_type_id=20,
            )
            session.add_all([site_x, site_y])
            await session.flush()

            session.add(
                Container(
                    id=100,
                    site_id=10,
                    latitude=-34.58,
                    longitude=-58.42,
                    current_level=5,
                    available=True,
                    container_type_id=10,
                    geom=WKTElement("POINT(-58.42 -34.58)", srid=4326),
                )
            )
            session.add(
                Container(
                    id=200,
                    site_id=20,
                    latitude=-34.58,
                    longitude=-58.41,
                    current_level=95,
                    available=True,
                    container_type_id=20,
                    geom=WKTElement("POINT(-58.41 -34.58)", srid=4326),
                )
            )
            await session.commit()

            config = OptimizationConfig(max_distance_km=5.0)
            plan = await generate_redistribution_plan(session, config)

            # No debe haber movimientos porque los tipos son incompatibles
            self.assertEqual(
                plan.total_containers_moved,
                0,
                "No se deben mover contenedores entre sitios de tipos distintos",
            )

    async def test_generate_plan_respects_distance_limit(self):
        """Verifica que no se mueven contenedores si la distancia excede el máximo."""
        from app.services.map.optimization_service import generate_redistribution_plan

        async with self.session_maker() as session:
            wt = WasteType(id=1, name="RSU", color="#333")
            ct = ContainerType(id=1, name="Lateral", height_cm=145, volume_m3=3.2)
            ct.waste_types.append(wt)
            session.add_all([wt, ct])
            await session.flush()

            # Sitios a 10+ km de distancia
            site_a = Site(
                id=1,
                name="Sitio Lejos A",
                latitude=-34.58,
                longitude=-58.50,
                geom=WKTElement("POINT(-58.50 -34.58)", srid=4326),
                waste_type_id=1,
            )
            site_b = Site(
                id=2,
                name="Sitio Lejos B",
                latitude=-34.58,
                longitude=-58.38,
                geom=WKTElement("POINT(-58.38 -34.58)", srid=4326),
                waste_type_id=1,
            )
            session.add_all([site_a, site_b])
            await session.flush()

            session.add(
                Container(
                    id=1,
                    site_id=1,
                    latitude=-34.58,
                    longitude=-58.50,
                    current_level=5,
                    available=True,
                    container_type_id=1,
                    geom=WKTElement("POINT(-58.50 -34.58)", srid=4326),
                )
            )
            session.add(
                Container(
                    id=2,
                    site_id=2,
                    latitude=-34.58,
                    longitude=-58.38,
                    current_level=95,
                    available=True,
                    container_type_id=1,
                    geom=WKTElement("POINT(-58.38 -34.58)", srid=4326),
                )
            )
            await session.commit()

            # Distancia entre puntos ~10 km, límite 2 km
            config = OptimizationConfig(max_distance_km=2.0)
            plan = await generate_redistribution_plan(session, config)

            self.assertEqual(
                plan.total_containers_moved,
                0,
                "No se deben mover contenedores a más de 2 km",
            )

    async def test_plan_persistence_and_retrieval(self):
        """Verifica que los planes se persisten y recuperan correctamente."""
        from app.services.map.optimization_service import (
            generate_redistribution_plan,
            get_plan_by_id,
        )

        async with self.session_maker() as session:
            await self._seed_scenario(session)

            config = OptimizationConfig(max_distance_km=5.0)
            plan = await generate_redistribution_plan(session, config)

            # Recuperar desde DB
            retrieved = await get_plan_by_id(session, plan.id)
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.id, plan.id)
            self.assertEqual(retrieved.status, "draft")
            self.assertEqual(retrieved.config.max_distance_km, 5.0)

            # Plan inexistente
            not_found = await get_plan_by_id(session, 99999)
            self.assertIsNone(not_found)

    async def test_whatif_activate_and_deactivate(self):
        """Verifica que se puede activar y desactivar el modo what-if."""
        from app.services.map.optimization_service import generate_redistribution_plan
        from app.services.map.optimization_whatif_service import (
            activate_whatif,
            deactivate_whatif,
            get_active_plan_id,
        )

        async with self.session_maker() as session:
            await self._seed_scenario(session)

            config = OptimizationConfig(max_distance_km=5.0)
            plan = await generate_redistribution_plan(session, config)

            # Activar what-if
            activated = await activate_whatif(session, plan.id)
            self.assertEqual(activated.status, "active_whatif")
            self.assertEqual(await get_active_plan_id(session), plan.id)

            # Verificar que se crearon niveles virtuales
            count = await session.scalar(
                select(
                    __import__("sqlalchemy").func.count(
                        OptimizationWhatIfLevel.container_id
                    )
                ).where(OptimizationWhatIfLevel.plan_id == plan.id)
            )
            self.assertEqual(
                count, 6, "Debe haber 6 registros virtuales (6 contenedores)"
            )

            # Desactivar what-if
            deactivated = await deactivate_whatif(session, plan.id)
            self.assertEqual(deactivated.status, "completed")
            self.assertIsNone(await get_active_plan_id(session))

    async def test_whatif_keeps_updating_after_a_restart(self):
        """El plan activo se lee de la base: no depende de memoria del proceso."""
        from datetime import UTC, datetime

        from app.models.map.optimization import RedistributionPlan
        from app.services.map.optimization_whatif_service import (
            apply_readings_to_whatif,
        )
        from app.services.map.whatif_allocation import Reading

        # Estado que encuentra un backend recién reiniciado: el plan activo y sus
        # niveles virtuales están en la base, y nada en memoria.
        async with self.session_maker() as session:
            await self._seed_scenario(session)
            plan = RedistributionPlan(
                status="active_whatif", config={}, moves=[], summary={}
            )
            session.add(plan)
            await session.flush()
            session.add(
                OptimizationWhatIfLevel(
                    plan_id=plan.id,
                    container_id=1,
                    original_site_id=1,
                    optimized_site_id=1,
                    virtual_level=10,
                )
            )
            await session.commit()

        async with self.session_maker() as session:
            reading = Reading(
                container_id=1,
                level_before=10,
                level_after=25,
                reading_date=datetime(2026, 1, 1, 12, tzinfo=UTC),
                collected=False,
            )
            await apply_readings_to_whatif(session, [reading], {1: 1})
            await session.commit()
            level = await session.scalar(
                select(OptimizationWhatIfLevel.virtual_level).where(
                    OptimizationWhatIfLevel.container_id == 1
                )
            )
        # El sitio 1 no participa del plan: el virtual sigue al real.
        self.assertEqual(level, 25)

    async def test_activation_snapshot_moves_containers_empty(self):
        from datetime import UTC, datetime

        from app.services.map.optimization_whatif_service import activate_whatif

        async with self.session_maker() as session:
            await self._seed_scenario(session)
            (await session.get(Container, 6)).deleted_at = datetime(
                2026, 1, 1, tzinfo=UTC
            )
            reading = datetime(2026, 1, 2, 10, tzinfo=UTC)
            (await session.get(Container, 3)).last_reading = reading
            plan = RedistributionPlan(
                status="draft",
                config={},
                moves=[_move(container_id=1, from_site_id=1, to_site_id=2)],
            )
            session.add(plan)
            await session.commit()

            await activate_whatif(session, plan.id)

            rows = {
                r.container_id: r
                for r in (await session.scalars(select(OptimizationWhatIfLevel))).all()
            }
        # El dado de baja no entra; el movido va al receptor y llega vacío.
        self.assertEqual(set(rows), {1, 2, 3, 4, 5})
        self.assertEqual(
            (
                rows[1].original_site_id,
                rows[1].optimized_site_id,
                rows[1].virtual_level,
            ),
            (1, 2, 0),
        )
        self.assertEqual((rows[3].optimized_site_id, rows[3].virtual_level), (2, 90))
        self.assertEqual(rows[3].last_reading.replace(tzinfo=UTC), reading)
        self.assertEqual(rows[3].overflow_hours, 0)

    async def test_activating_a_plan_replaces_the_previous_one(self):
        from app.services.map.optimization_whatif_service import activate_whatif

        async with self.session_maker() as session:
            await self._seed_scenario(session)
            first = RedistributionPlan(status="draft", config={}, moves=[])
            second = RedistributionPlan(
                status="draft",
                config={},
                moves=[_move(container_id=1, from_site_id=1, to_site_id=2)],
            )
            session.add_all([first, second])
            await session.commit()

            await activate_whatif(session, first.id)
            await activate_whatif(session, second.id)

            await session.refresh(first)
            self.assertEqual(first.status, "completed")
            self.assertEqual(second.status, "active_whatif")
            totals = await session.get(OptimizationWhatIfTotals, second.id)
            self.assertEqual(
                (totals.observed_hours, totals.critical_site_hours), (0, 0)
            )
            plan_ids = set(
                await session.scalars(select(OptimizationWhatIfLevel.plan_id))
            )
            self.assertEqual(plan_ids, {second.id})

    async def test_failed_activation_keeps_the_previous_plan(self):
        """Generar y activar es todo o nada: si falla, no queda un plan a medias."""
        from unittest.mock import patch

        import app.services.map.optimization_whatif_service as whatif_svc

        async with self.session_maker() as session:
            await self._seed_scenario(session)
            previous = RedistributionPlan(status="draft", config={}, moves=[])
            session.add(previous)
            await session.commit()
            await whatif_svc.activate_whatif(session, previous.id)

        async def broken_activate(db, plan):
            raise RuntimeError("falla a mitad de la activación")

        async with self.session_maker() as session:
            with (
                patch.object(whatif_svc, "_activate", broken_activate),
                self.assertRaises(RuntimeError),
            ):
                await whatif_svc.create_and_activate_plan(
                    session, OptimizationConfig(max_distance_km=5.0)
                )

        async with self.session_maker() as session:
            plans = (await session.scalars(select(RedistributionPlan))).all()
        self.assertEqual(
            [(p.id, p.status) for p in plans], [(previous.id, "active_whatif")]
        )

    async def test_database_allows_a_single_active_plan(self):
        from sqlalchemy.exc import IntegrityError

        async with self.session_maker() as session:
            session.add_all(
                [
                    RedistributionPlan(status="active_whatif", config={}, moves=[]),
                    RedistributionPlan(status="completed", config={}, moves=[]),
                    RedistributionPlan(status="completed", config={}, moves=[]),
                ]
            )
            await session.commit()

            session.add(RedistributionPlan(status="active_whatif", config={}, moves=[]))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_comparison_of_an_active_plan(self):
        from app.services.map.optimization_whatif_service import (
            activate_whatif,
            get_comparison_metrics,
        )

        async with self.session_maker() as session:
            await self._seed_scenario(session)
            # El plan lleva el contenedor 1 (5 %) del sitio 1 al sitio 2 (90 y 95 %).
            plan = RedistributionPlan(
                status="draft",
                config={},
                moves=[_move(container_id=1, from_site_id=1, to_site_id=2)],
                summary={"original_std_fill": 30.0, "optimized_std_fill": 22.5},
            )
            session.add(plan)
            await session.commit()
            await activate_whatif(session, plan.id)

            # Recién activado: estimación y valores de ahora, sin promedios todavía.
            fresh = await get_comparison_metrics(session, plan.id)
            self.assertEqual(
                (fresh.dispersion.real, fresh.dispersion.with_plan), (30, 22.5)
            )
            self.assertEqual(fresh.observed_hours, 0)
            self.assertIsNone(fresh.avg_critical_sites)
            # Sitio 2: real 92,5 % (crítico); con el plan (90 + 95 + 0) / 3 = 61,7 %.
            self.assertEqual(
                (fresh.critical_sites_now.real, fresh.critical_sites_now.with_plan),
                (1, 0),
            )
            self.assertEqual(fresh.full_container_hours.real, 0)

            # Con mediciones acumuladas por la ingesta.
            totals = await session.get(OptimizationWhatIfTotals, plan.id)
            totals.observed_hours = 10.0
            totals.real_critical_site_hours = 40.0
            totals.critical_site_hours = 25.0
            totals.real_full_container_hours = 12.0
            totals.full_container_hours = 8.0
            level = await session.get(OptimizationWhatIfLevel, (plan.id, 3))
            level.real_overflow_hours = 6.0
            level.overflow_hours = 2.5
            await session.commit()

            measured = await get_comparison_metrics(session, plan.id)
        self.assertEqual(
            (measured.avg_critical_sites.real, measured.avg_critical_sites.with_plan),
            (4.0, 2.5),
        )
        self.assertEqual(
            (measured.avg_full_containers.real, measured.avg_full_containers.with_plan),
            (1.2, 0.8),
        )
        self.assertEqual(
            (
                measured.full_container_hours.real,
                measured.full_container_hours.with_plan,
            ),
            (6.0, 2.5),
        )

    async def test_comparison_of_a_replaced_plan_keeps_its_averages(self):
        from app.services.map.optimization_whatif_service import (
            activate_whatif,
            get_comparison_metrics,
        )

        async with self.session_maker() as session:
            await self._seed_scenario(session)
            first = RedistributionPlan(status="draft", config={}, moves=[])
            second = RedistributionPlan(status="draft", config={}, moves=[])
            session.add_all([first, second])
            await session.commit()
            await activate_whatif(session, first.id)
            totals = await session.get(OptimizationWhatIfTotals, first.id)
            totals.observed_hours = 5.0
            totals.real_critical_site_hours = 10.0
            totals.critical_site_hours = 5.0
            await session.commit()
            await activate_whatif(session, second.id)

            comparison = await get_comparison_metrics(session, first.id)
        self.assertEqual(comparison.plan_status, "completed")
        self.assertEqual(
            (
                comparison.avg_critical_sites.real,
                comparison.avg_critical_sites.with_plan,
            ),
            (2.0, 1.0),
        )
        # Sin niveles virtuales (se borraron al activar el otro plan): nada de ahora.
        self.assertIsNone(comparison.critical_sites_now)
        self.assertIsNone(comparison.full_container_hours)


class TestOptimizationEndpoints(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda sc: MapBase.metadata.drop_all(sc, tables=TABLES))
        await self.engine.dispose()

    async def test_metrics_endpoint(self):
        """Verifica que GET /map/optimization/metrics responde correctamente."""
        from httpx import ASGITransport, AsyncClient

        from app.core.map_database import get_map_db
        from app.main import app

        async with self.session_maker() as session:
            wt = WasteType(id=1, name="RSU", color="#333")
            ct = ContainerType(id=1, name="Lateral", height_cm=145, volume_m3=3.2)
            ct.waste_types.append(wt)
            session.add_all([wt, ct])
            await session.flush()

            site = Site(
                id=1,
                name="Sitio Test",
                latitude=-34.60,
                longitude=-58.40,
                geom=WKTElement("POINT(-58.40 -34.60)", srid=4326),
                waste_type_id=1,
            )
            session.add(site)
            await session.flush()

            session.add(
                Container(
                    id=1,
                    site_id=1,
                    latitude=-34.60,
                    longitude=-58.40,
                    current_level=50,
                    available=True,
                    container_type_id=1,
                    geom=WKTElement("POINT(-58.40 -34.60)", srid=4326),
                )
            )
            await session.commit()

        async def override_get_map_db():
            async with self.session_maker() as s:
                yield s

        app.dependency_overrides[get_map_db] = override_get_map_db
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                res = await client.get("/map/optimization/metrics")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertEqual(data["total_sites"], 1)
                self.assertIn("sites", data)
                self.assertEqual(len(data["sites"]), 1)
                self.assertEqual(data["sites"][0]["site_id"], 1)
                self.assertIn("utilization_score", data["sites"][0])
                self.assertIn("category", data["sites"][0])
                self.assertIn("mean_fill_level", data)
                self.assertIn("std_fill_level", data)
                self.assertIn("total_containers", data)
                self.assertEqual(data["total_containers"], 1)
                self.assertEqual(data["mean_fill_level"], 50.0)
                self.assertEqual(data["std_fill_level"], 0.0)
        finally:
            app.dependency_overrides.pop(get_map_db, None)

    async def test_active_plan_endpoint_without_plan_returns_null(self):
        """Sin plan activo, GET /plan/active responde null y no crea ningún plan."""
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
                res = await client.get("/map/optimization/plan/active")
        finally:
            app.dependency_overrides.pop(get_map_db, None)

        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json())
        async with self.session_maker() as session:
            plans = await session.scalar(
                select(func.count()).select_from(RedistributionPlan)
            )
        self.assertEqual(plans, 0)

    async def test_plan_endpoint(self):
        """Verifica que POST /map/optimization/plan genera un plan correctamente."""
        from httpx import ASGITransport, AsyncClient

        from app.core.map_database import get_map_db
        from app.main import app

        async with self.session_maker() as session:
            wt = WasteType(id=1, name="RSU", color="#333")
            ct = ContainerType(id=1, name="Lateral", height_cm=145, volume_m3=3.2)
            ct.waste_types.append(wt)
            session.add_all([wt, ct])
            await session.flush()

            for i, (lat, level) in enumerate([(-34.580, 5), (-34.580, 95)], start=1):
                site = Site(
                    id=i,
                    name=f"Sitio {i}",
                    latitude=lat,
                    longitude=-58.40 + i * 0.005,
                    geom=WKTElement(f"POINT({-58.40 + i * 0.005} {lat})", srid=4326),
                    waste_type_id=1,
                )
                session.add(site)
                await session.flush()
                session.add(
                    Container(
                        id=i,
                        site_id=i,
                        latitude=lat,
                        longitude=-58.40 + i * 0.005,
                        current_level=level,
                        available=True,
                        container_type_id=1,
                        geom=WKTElement(
                            f"POINT({-58.40 + i * 0.005} {lat})", srid=4326
                        ),
                    )
                )
            await session.commit()

        async def override_get_map_db():
            async with self.session_maker() as s:
                yield s

        app.dependency_overrides[get_map_db] = override_get_map_db
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                res = await client.post(
                    "/map/optimization/plan",
                    json={"max_distance_km": 5.0, "algorithm": "greedy"},
                )
                self.assertEqual(res.status_code, 201)
                data = res.json()
                self.assertIn("id", data)
                self.assertEqual(data["status"], "active_whatif")
                self.assertIn("moves", data)

                # Configuraciones inválidas: 422, no un 500 ni un plan absurdo.
                res_bad_plan = await client.post(
                    "/map/optimization/plan", json={"algorithm": "quantum"}
                )
                self.assertEqual(res_bad_plan.status_code, 422)
                res_bad_metrics = await client.get(
                    "/map/optimization/metrics", params={"window_days": 0}
                )
                self.assertEqual(res_bad_metrics.status_code, 422)
                self.assertIn("original_mean_fill", data)
                self.assertIn("original_std_fill", data)
                self.assertIn("optimized_mean_fill", data)
                self.assertIn("optimized_std_fill", data)
                # El plan queda registrado a nombre del usuario autenticado.
                self.assertEqual(data["created_by"], 1)

                # Verificar /map/optimization/plan/active
                res_active = await client.get("/map/optimization/plan/active")
                self.assertEqual(res_active.status_code, 200)
                active_data = res_active.json()
                self.assertEqual(active_data["id"], data["id"])
                self.assertEqual(active_data["status"], "active_whatif")

                # Recuperar el plan por ID
                plan_id = data["id"]
                res_get = await client.get(f"/map/optimization/plan/{plan_id}")
                self.assertEqual(res_get.status_code, 200)
                plan_data = res_get.json()
                self.assertEqual(plan_data["id"], plan_id)
                self.assertIn("original_mean_fill", plan_data)
                self.assertIn("optimized_mean_fill", plan_data)

                # Comparación del plan recién activado: sin promedios todavía.
                res_cmp = await client.get(
                    f"/map/optimization/plan/{plan_id}/comparison"
                )
                self.assertEqual(res_cmp.status_code, 200)
                comparison = res_cmp.json()
                self.assertEqual(comparison["observed_hours"], 0)
                self.assertIsNone(comparison["avg_critical_sites"])
                self.assertIn("real", comparison["critical_sites_now"])

                # Plan no encontrado
                res_404 = await client.get("/map/optimization/plan/99999")
                self.assertEqual(res_404.status_code, 404)
        finally:
            app.dependency_overrides.pop(get_map_db, None)

    def test_build_redistribution_move_metrics(self):
        """Verifica que cada movimiento calcule la descongestión neta y los niveles antes/después."""
        from app.schemas.map.optimization import SiteUtilizationMetric
        from app.services.map.optimization_service import _build_redistribution_move

        donor = SiteUtilizationMetric(
            site_id=1,
            site_name="Sitio Donante",
            latitude=-34.6,
            longitude=-58.4,
            container_count=2,
            avg_fill_level=20.0,
            peak_fill_rate=0.0,
            overflow_frequency=0,
            utilization_score=0.2,
            category="low",
        )
        receiver = SiteUtilizationMetric(
            site_id=2,
            site_name="Sitio Receptor",
            latitude=-34.61,
            longitude=-58.41,
            container_count=1,
            avg_fill_level=90.0,
            peak_fill_rate=0.8,
            overflow_frequency=3,
            utilization_score=0.9,
            category="critical",
        )

        move = _build_redistribution_move(
            container_id=10, donor=donor, receiver=receiver, dist=1.2
        )

        self.assertEqual(move.container_id, 10)
        self.assertEqual(move.donor_fill_before, 20.0)
        self.assertEqual(move.donor_fill_after, 40.0)
        self.assertEqual(move.receiver_fill_before, 90.0)
        self.assertEqual(move.receiver_fill_after, 45.0)
        self.assertEqual(move.net_decongestion_pct, -25.0)


if __name__ == "__main__":
    unittest.main()

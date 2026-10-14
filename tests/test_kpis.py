from __future__ import annotations

import unittest
from geoalchemy2.elements import WKTElement
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.map_database import MapBase, get_map_db
from app.main import app
from app.models.map.container import Container
from app.models.map.container_type import ContainerType, container_type_waste_types
from app.models.map.optimization import OptimizationWhatIfLevel, RedistributionPlan
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.services.map.kpi_service import (
    get_kpis_comparison,
    get_real_network_kpis,
    get_whatif_network_kpis,
)


def _register_sqlite_stubs(engine):
    @event.listens_for(engine.sync_engine, "connect")
    def _add_sqlite_funcs(dbapi_connection, connection_record):
        dbapi_connection.create_function("RecoverGeometryColumn", -1, lambda *a: 1)
        dbapi_connection.create_function("InitSpatialMetaData", -1, lambda *a: 1)
        dbapi_connection.create_function("AddGeometryColumn", -1, lambda *a: 1)
        dbapi_connection.create_function("DiscardGeometryColumn", -1, lambda *a: 1)
        dbapi_connection.create_function("CheckSpatialIndex", -1, lambda *a: None)
        dbapi_connection.create_function("DisableSpatialIndex", -1, lambda *a: None)
        dbapi_connection.create_function("bool_or", 1, lambda x: bool(x))
        dbapi_connection.create_function("GeomFromEWKT", 1, lambda x: str(x))
        dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda x: str(x))
        dbapi_connection.create_function("ST_GeomFromText", -1, lambda *a: str(a[0]))
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
    RedistributionPlan.__table__,
    OptimizationWhatIfLevel.__table__,
]


class TestKpiServices(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_data(self, session: AsyncSession):
        wt = WasteType(id=1, name="RSU", color="#333")
        ct = ContainerType(id=1, name="Lateral", height_cm=145, volume_m3=3.2)
        ct.waste_types.append(wt)
        session.add_all([wt, ct])
        await session.flush()

        # 4 Sitios
        sites = [
            Site(
                id=1,
                name="Sitio Crítico",
                latitude=-34.60,
                longitude=-58.40,
                geom=WKTElement("POINT(-58.40 -34.60)", srid=4326),
                waste_type_id=1,
            ),
            Site(
                id=2,
                name="Sitio Alto",
                latitude=-34.61,
                longitude=-58.41,
                geom=WKTElement("POINT(-58.41 -34.61)", srid=4326),
                waste_type_id=1,
            ),
            Site(
                id=3,
                name="Sitio Normal",
                latitude=-34.62,
                longitude=-58.42,
                geom=WKTElement("POINT(-58.42 -34.62)", srid=4326),
                waste_type_id=1,
            ),
            Site(
                id=4,
                name="Sitio Vacío/Idle",
                latitude=-34.63,
                longitude=-58.43,
                geom=WKTElement("POINT(-58.43 -34.63)", srid=4326),
                waste_type_id=1,
            ),
        ]
        session.add_all(sites)
        await session.flush()

        # Contenedores:
        # Sitio 1: 1 contenedor con 90% (avg = 90 -> Critical)
        # Sitio 2: 2 contenedores con 70% y 80% (avg = 75 -> High)
        # Sitio 3: 1 contenedor con 50% (avg = 50 -> Normal)
        # Sitio 4: 0 contenedores (avg = 0 -> Idle)
        containers = [
            Container(
                id=1,
                site_id=1,
                latitude=-34.60,
                longitude=-58.40,
                current_level=90,
                available=True,
                container_type_id=1,
                geom=WKTElement("POINT(-58.40 -34.60)", srid=4326),
            ),
            Container(
                id=2,
                site_id=2,
                latitude=-34.61,
                longitude=-58.41,
                current_level=70,
                available=True,
                container_type_id=1,
                geom=WKTElement("POINT(-58.41 -34.61)", srid=4326),
            ),
            Container(
                id=3,
                site_id=2,
                latitude=-34.61,
                longitude=-58.41,
                current_level=80,
                available=True,
                container_type_id=1,
                geom=WKTElement("POINT(-58.41 -34.61)", srid=4326),
            ),
            Container(
                id=4,
                site_id=3,
                latitude=-34.62,
                longitude=-58.42,
                current_level=50,
                available=True,
                container_type_id=1,
                geom=WKTElement("POINT(-58.42 -34.62)", srid=4326),
            ),
        ]
        session.add_all(containers)
        await session.commit()

    async def test_empty_database_kpis(self):
        """Verifica que con base de datos vacía devuelve ceros consistentes."""
        async with self.session_maker() as session:
            kpis = await get_real_network_kpis(session)
            self.assertEqual(kpis.total_sites, 0)
            self.assertEqual(kpis.total_containers, 0)
            self.assertEqual(kpis.mean_fill_level, 0.0)
            self.assertEqual(kpis.std_fill_level, 0.0)
            self.assertEqual(kpis.critical_count, 0)
            self.assertFalse(kpis.is_whatif)

    async def test_real_network_kpis_site_level_aggregation(self):
        """Verifica la agregación precisa a nivel Sitio."""
        async with self.session_maker() as session:
            await self._seed_data(session)

            kpis = await get_real_network_kpis(session)

            self.assertEqual(kpis.total_sites, 4)
            self.assertEqual(kpis.total_containers, 4)
            self.assertEqual(kpis.critical_count, 1)  # Sitio 1 (90%)
            self.assertEqual(kpis.high_count, 1)  # Sitio 2 (75%)
            self.assertEqual(kpis.normal_count, 1)  # Sitio 3 (50%)
            self.assertEqual(kpis.low_count, 0)
            self.assertEqual(kpis.idle_count, 1)  # Sitio 4 (0%)

            # Promedio de sitios: (90 + 75 + 50 + 0) / 4 = 53.75
            self.assertAlmostEqual(kpis.mean_fill_level, 53.75, places=2)

    async def test_critical_category_uses_centralized_threshold(self):
        """Un sitio al 82% es crítico con el umbral crítico centralizado (80%)."""
        async with self.session_maker() as session:
            await self._seed_data(session)
            container = await session.get(Container, 4)  # Sitio 3 (50% -> 82%)
            container.current_level = 82
            await session.commit()

            kpis = await get_real_network_kpis(session)

            self.assertEqual(kpis.critical_count, 2)  # Sitios 1 (90%) y 3 (82%)
            self.assertEqual(kpis.high_count, 1)  # Sitio 2 (75%)
            self.assertEqual(kpis.normal_count, 0)
            self.assertGreater(kpis.std_fill_level, 0.0)
            self.assertFalse(kpis.is_whatif)

    async def test_whatif_kpis_and_comparison(self):
        """Verifica el cálculo de KPIs What-If y la comparación."""
        async with self.session_maker() as session:
            await self._seed_data(session)

            # Crear plan What-If activo
            plan = RedistributionPlan(
                status="active_whatif",
                config={},
                moves=[],
                summary={
                    "total_containers_moved": 1,
                    "avg_distance_km": 1.2,
                },
            )
            session.add(plan)
            await session.flush()

            # En el plan What-If:
            # Rebalanceamos para que todos los sitios tengan 50%
            whatif_levels = [
                OptimizationWhatIfLevel(
                    plan_id=plan.id,
                    container_id=1,
                    original_site_id=1,
                    optimized_site_id=1,
                    virtual_level=55,
                ),
                OptimizationWhatIfLevel(
                    plan_id=plan.id,
                    container_id=2,
                    original_site_id=2,
                    optimized_site_id=2,
                    virtual_level=55,
                ),
                OptimizationWhatIfLevel(
                    plan_id=plan.id,
                    container_id=3,
                    original_site_id=2,
                    optimized_site_id=3,
                    virtual_level=50,
                ),
                OptimizationWhatIfLevel(
                    plan_id=plan.id,
                    container_id=4,
                    original_site_id=3,
                    optimized_site_id=4,
                    virtual_level=50,
                ),
            ]
            session.add_all(whatif_levels)
            await session.commit()

            # Métricas What-If
            whatif_kpis = await get_whatif_network_kpis(session, plan_id=plan.id)
            self.assertEqual(whatif_kpis.total_sites, 4)
            self.assertEqual(whatif_kpis.critical_count, 0)
            self.assertTrue(whatif_kpis.is_whatif)
            self.assertEqual(whatif_kpis.plan_id, plan.id)

            # Comparación
            comp = await get_kpis_comparison(session, plan_id=plan.id)
            self.assertEqual(comp.plan_id, plan.id)
            self.assertEqual(comp.critical_sites_reduction, 1)
            self.assertTrue(comp.is_improved)
            self.assertGreater(comp.std_reduction_pct, 0.0)

    async def test_kpis_http_endpoints(self):
        """Verifica que los endpoints HTTP responden en /map/kpis y /map/kpis/comparison."""
        async with self.session_maker() as session:
            await self._seed_data(session)

            plan = RedistributionPlan(
                status="active_whatif",
                config={},
                moves=[],
                summary={},
            )
            session.add(plan)
            await session.flush()

            session.add(
                OptimizationWhatIfLevel(
                    plan_id=plan.id,
                    container_id=1,
                    optimized_site_id=1,
                    virtual_level=60,
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
                # 1. GET /map/kpis (Real)
                res = await client.get("/map/kpis")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertEqual(data["total_sites"], 4)
                self.assertFalse(data["is_whatif"])

                # 2. GET /map/kpis?whatif=true
                res_w = await client.get("/map/kpis?whatif=true")
                self.assertEqual(res_w.status_code, 200)
                data_w = res_w.json()
                self.assertTrue(data_w["is_whatif"])

                # 3. GET /map/kpis/comparison
                res_c = await client.get("/map/kpis/comparison")
                self.assertEqual(res_c.status_code, 200)
                data_c = res_c.json()
                self.assertIn("baseline", data_c)
                self.assertIn("whatif", data_c)
                self.assertIn("std_reduction_pct", data_c)
        finally:
            app.dependency_overrides.pop(get_map_db, None)

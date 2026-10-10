from __future__ import annotations

import unittest

from geoalchemy2.elements import WKTElement
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.map_database import MapBase, get_map_db
from app.main import app
from app.models.map.site import Site


class TestMapConfigEndpoint(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import event

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        @event.listens_for(self.engine.sync_engine, "connect")
        def do_connect(dbapi_connection, connection_record):
            dbapi_connection.create_function(
                "RecoverGeometryColumn", -1, lambda *args: 1
            )
            dbapi_connection.create_function("InitSpatialMetaData", -1, lambda *args: 1)
            dbapi_connection.create_function("AddGeometryColumn", -1, lambda *args: 1)
            dbapi_connection.create_function(
                "DiscardGeometryColumn", -1, lambda *args: 1
            )
            dbapi_connection.create_function("CreateSpatialIndex", -1, lambda *args: 1)
            dbapi_connection.create_function(
                "CheckSpatialIndex", -1, lambda *args: None
            )
            dbapi_connection.create_function(
                "DisableSpatialIndex", -1, lambda *args: None
            )
            dbapi_connection.create_function("bool_or", 1, lambda x: bool(x))
            dbapi_connection.create_function("GeomFromEWKT", 1, lambda x: str(x))
            dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda x: str(x))
            dbapi_connection.create_function(
                "ST_AsEWKB",
                1,
                lambda x: str(x).encode("utf-8") if x is not None else None,
            )
            dbapi_connection.create_function(
                "ST_AsBinary",
                1,
                lambda x: str(x).encode("utf-8") if x is not None else None,
            )
            dbapi_connection.create_function(
                "AsEWKB", 1, lambda x: str(x).encode("utf-8") if x is not None else None
            )

        self.session_maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.engine.begin() as conn:
            await conn.run_sync(MapBase.metadata.create_all)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(MapBase.metadata.drop_all)
        await self.engine.dispose()

    async def test_map_config_empty_db(self):
        async def override_get_map_db():
            async with self.session_maker() as s:
                yield s

        app.dependency_overrides[get_map_db] = override_get_map_db
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                res = await client.get("/map/config")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertIn("center", data)
                self.assertIn("default_zoom", data)
                self.assertIsNone(data["bounds"])
                self.assertIn("city_name", data)
                self.assertEqual(
                    data["thresholds"],
                    {"normal": 40, "high": 70, "critical": 80, "full": 100},
                )
        finally:
            app.dependency_overrides.pop(get_map_db, None)

    async def test_map_config_with_sites(self):
        async with self.session_maker() as session:
            s1 = Site(
                id=1,
                name="Sitio 1",
                latitude=40.40,
                longitude=-3.70,
                geom=WKTElement("POINT(-3.70 40.40)", srid=4326),
            )
            s2 = Site(
                id=2,
                name="Sitio 2",
                latitude=40.50,
                longitude=-3.60,
                geom=WKTElement("POINT(-3.60 40.50)", srid=4326),
            )
            session.add_all([s1, s2])
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
                res = await client.get("/map/config")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertIsNotNone(data["bounds"])
                self.assertEqual(len(data["bounds"]), 2)
                # bounds: [[min_lat, min_lng], [max_lat, max_lng]]
                self.assertAlmostEqual(data["bounds"][0][0], 40.40, places=2)
                self.assertAlmostEqual(data["bounds"][0][1], -3.70, places=2)
                self.assertAlmostEqual(data["bounds"][1][0], 40.50, places=2)
                self.assertAlmostEqual(data["bounds"][1][1], -3.60, places=2)
                # center: avg
                self.assertAlmostEqual(data["center"]["lat"], 40.45, places=2)
                self.assertAlmostEqual(data["center"]["lng"], -3.65, places=2)
        finally:
            app.dependency_overrides.pop(get_map_db, None)

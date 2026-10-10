from __future__ import annotations

import unittest
from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.map_database import MapBase, get_map_db
from app.main import app
from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.waste_type import WasteType


def _register_sqlite_stubs(engine):
    @event.listens_for(engine.sync_engine, "connect")
    def _add_sqlite_funcs(dbapi_connection, connection_record):
        for name in (
            "RecoverGeometryColumn",
            "InitSpatialMetaData",
            "AddGeometryColumn",
            "DiscardGeometryColumn",
            "CreateSpatialIndex",
        ):
            dbapi_connection.create_function(name, -1, lambda *args: 1)
        for name in ("CheckSpatialIndex", "DisableSpatialIndex"):
            dbapi_connection.create_function(name, -1, lambda *args: None)
        dbapi_connection.create_function("GeomFromEWKT", 1, lambda x: str(x))
        dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda x: str(x))
        for name in ("ST_AsEWKB", "ST_AsBinary", "AsEWKB"):
            dbapi_connection.create_function(
                name, 1, lambda x: str(x).encode("utf-8") if x is not None else None
            )


class TestWorkerContainersEndpoint(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        _register_sqlite_stubs(self.engine)
        async with self.engine.begin() as conn:
            await conn.run_sync(MapBase.metadata.create_all)
        self.session_maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_maker() as session:
            wt = WasteType(id=1, name="RSU", color="#333")
            ct = ContainerType(id=1, name="Lateral", height_cm=145, volume_m3=3.2)
            ct.waste_types.append(wt)
            session.add_all([wt, ct])
            await session.flush()
            for container_id, deleted_at in (
                (1, None),
                (2, None),
                (3, datetime(2026, 1, 1, tzinfo=UTC)),
            ):
                session.add(
                    Container(
                        id=container_id,
                        latitude=-34.60,
                        longitude=-58.40,
                        geom=WKTElement("POINT(-58.40 -34.60)", srid=4326),
                        current_level=10 * container_id,
                        container_type_id=1,
                        deleted_at=deleted_at,
                    )
                )
            await session.commit()

        async def override_get_map_db():
            async with self.session_maker() as s:
                yield s

        app.dependency_overrides[get_map_db] = override_get_map_db

    async def asyncTearDown(self):
        app.dependency_overrides.pop(get_map_db, None)
        async with self.engine.begin() as conn:
            await conn.run_sync(MapBase.metadata.drop_all)
        await self.engine.dispose()

    @pytest.mark.real_auth
    async def test_lists_active_containers_without_authentication(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.get("/digital-twin/worker/containers")

        self.assertEqual(res.status_code, 200)
        self.assertEqual([c["id"] for c in res.json()], [1, 2])

    @pytest.mark.real_auth
    async def test_paginates_with_limit_and_offset(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.get(
                "/digital-twin/worker/containers", params={"limit": 1, "offset": 1}
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual([c["id"] for c in res.json()], [2])

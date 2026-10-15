"""El what-if se actualiza con la ingesta de telemetría (camino de respaldo, SQLite)."""

from __future__ import annotations

import itertools
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from geoalchemy2.elements import WKTElement
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.digital_twin_ingest_service as ingest_svc
from app.core.map_database import MapBase
from app.models.map.container import Container
from app.models.map.container_type import ContainerType, container_type_waste_types
from app.models.map.data_level import DataLevel
from app.models.map.neighborhood import Neighborhood
from app.models.map.optimization import OptimizationWhatIfLevel, RedistributionPlan
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.services.map.optimization_whatif_service import activate_whatif

TABLES = [
    WasteType.__table__,
    ContainerType.__table__,
    container_type_waste_types,
    Neighborhood.__table__,
    Site.__table__,
    Container.__table__,
    DataLevel.__table__,
    RedistributionPlan.__table__,
    OptimizationWhatIfLevel.__table__,
]
START = datetime(2026, 1, 1, tzinfo=UTC)


def _register_sqlite_stubs(engine):
    counter = itertools.count(1)

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection, _record):
        for name in (
            "RecoverGeometryColumn",
            "InitSpatialMetaData",
            "AddGeometryColumn",
            "DiscardGeometryColumn",
            "CreateSpatialIndex",
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
        # La ingesta numera los cambios con una secuencia de Postgres.
        dbapi_connection.create_function("nextval", 1, lambda _: next(counter))


def _measurement(container_id: int, level: float, hours: int, collected=False):
    return {
        "container_id": str(container_id),
        "device_id": f"imei-{container_id}",
        "timestamp": (START + timedelta(hours=hours)).isoformat(),
        "telemetry": {"fill_level_pct": level},
        "flags": {"is_collection_detected": collected},
    }


class TestWhatIfIngest(unittest.IsolatedAsyncioTestCase):
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
        ingest_svc.invalidate_container_cache()
        async with self.session_maker() as session:
            await self._seed(session)

    async def asyncTearDown(self):
        ingest_svc.invalidate_container_cache()
        await self.engine.dispose()

    async def _seed(self, session: AsyncSession):
        """Sitio 1 con los contenedores 1-5 al 20%; el plan le lleva el 9 del sitio 2."""
        session.add(WasteType(id=1, name="RSU", color="#333"))
        session.add(ContainerType(id=1, name="Lateral", height_cm=145, volume_m3=3.2))
        for site_id in (1, 2):
            session.add(
                Site(
                    id=site_id,
                    name=f"Sitio {site_id}",
                    latitude=-34.6,
                    longitude=-58.4,
                    geom=WKTElement("POINT(-58.4 -34.6)", srid=4326),
                    waste_type_id=1,
                )
            )
        await session.flush()
        for container_id, site_id in [(i, 1) for i in range(1, 6)] + [(8, 2), (9, 2)]:
            session.add(
                Container(
                    id=container_id,
                    site_id=site_id,
                    serie_id=str(container_id),
                    latitude=-34.6,
                    longitude=-58.4,
                    geom=WKTElement("POINT(-58.4 -34.6)", srid=4326),
                    current_level=20,
                    last_reading=START,
                    container_type_id=1,
                )
            )
        plan = RedistributionPlan(
            status="draft",
            config={},
            moves=[
                {
                    "container_id": 9,
                    "from_site_id": 2,
                    "from_site_name": "Sitio 2",
                    "to_site_id": 1,
                    "to_site_name": "Sitio 1",
                    "distance_km": 0.5,
                }
            ],
        )
        session.add(plan)
        await session.commit()
        await activate_whatif(session, plan.id)

    async def _ingest(self, measurements):
        async with self.session_maker() as session:
            return await ingest_svc.ingest_telemetry_batch(session, measurements)

    async def _virtual(self) -> dict[int, float]:
        async with self.session_maker() as session:
            rows = await session.execute(
                select(
                    OptimizationWhatIfLevel.container_id,
                    OptimizationWhatIfLevel.virtual_level,
                )
            )
            return {row.container_id: row.virtual_level for row in rows}

    async def test_ingested_reading_is_shared_with_the_arriving_container(self):
        await self._ingest([_measurement(1, 32, hours=1)])

        virtual = await self._virtual()
        # Mide +12 en un sitio de 5 que recibe 1: él suma 10 y el que llega, 2.
        self.assertAlmostEqual(virtual[1], 30)
        self.assertAlmostEqual(virtual[9], 2)
        self.assertAlmostEqual(virtual[2], 20)

    async def test_readings_of_a_batch_are_applied_in_order(self):
        # En el mismo lote: a la 1 pasa el camión, a las 2 sube al 14%.
        await self._ingest(
            [_measurement(1, 2, hours=1, collected=True), _measurement(1, 14, hours=2)]
        )

        virtual = await self._virtual()
        # Después de vaciar queda en 2 y suma 12 · 5/6 = 10; el que llega, 2.
        self.assertAlmostEqual(virtual[1], 12)
        self.assertAlmostEqual(virtual[9], 2)

    async def test_out_of_order_readings_do_not_change_the_whatif(self):
        await self._ingest([_measurement(1, 32, hours=3)])
        await self._ingest([_measurement(1, 80, hours=2)])  # vieja: llega tarde
        await self._ingest([_measurement(1, 38, hours=4)])

        virtual = await self._virtual()
        # Solo cuentan +12 y +6: el 80% de la lectura vieja no se aplica.
        self.assertAlmostEqual(virtual[1], 20 + 18 * 5 / 6)
        async with self.session_maker() as session:
            container = await session.get(Container, 1)
        self.assertEqual(container.current_level, 38)

    async def test_whatif_failure_does_not_lose_the_readings(self):
        async def broken(*_args, **_kwargs):
            raise RuntimeError("falla del what-if")

        with patch(
            "app.services.map.optimization_whatif_service.apply_readings_to_whatif",
            broken,
        ):
            result = await self._ingest([_measurement(1, 32, hours=1)])

        self.assertEqual(result.updated, 1)
        async with self.session_maker() as session:
            readings = await session.scalar(select(func.count()).select_from(DataLevel))
            container = await session.get(Container, 1)
        self.assertEqual(readings, 1)
        self.assertEqual(container.current_level, 32)
        self.assertAlmostEqual((await self._virtual())[1], 20)

    async def test_container_added_after_activation_joins_the_whatif(self):
        async with self.session_maker() as session:
            session.add(
                Container(
                    id=20,
                    site_id=1,
                    serie_id="20",
                    latitude=-34.6,
                    longitude=-58.4,
                    geom=WKTElement("POINT(-58.4 -34.6)", srid=4326),
                    current_level=10,
                    container_type_id=1,
                )
            )
            await session.commit()
        ingest_svc.invalidate_container_cache()

        await self._ingest([_measurement(20, 16, hours=1)])

        virtual = await self._virtual()
        self.assertIn(20, virtual)
        # Ahora el sitio tiene 6 propios y 1 que llega: suma 6 · 6/7.
        self.assertAlmostEqual(virtual[20], 10 + 6 * 6 / 7)

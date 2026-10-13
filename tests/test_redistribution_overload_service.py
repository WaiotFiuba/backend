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
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.services.map.redistribution_overload_service import (
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
]
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

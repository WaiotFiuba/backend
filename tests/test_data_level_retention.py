from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.services.data_level_retention as retention
from app.core.map_database import MapBase
from app.models.map.data_level import DataLevel


# En sqlite, una PK BigInteger no se autoincrementa (solo INTEGER lo hace).
@compiles(BigInteger, "sqlite")
def _bigint_as_integer_on_sqlite(type_, compiler, **kw):
    return "INTEGER"


START = datetime(2026, 3, 1)
CONTAINERS = 3


class TestDataLevelRetention(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        retention._last_check = None
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: MapBase.metadata.create_all(
                    sync_conn, tables=[DataLevel.__table__]
                )
            )
        self.session_maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _add_periods(self, first: int, count: int, hours_per_period: int = 24):
        """Una lectura por contenedor y por periodo."""
        async with self.session_maker() as db:
            for period in range(first, first + count):
                for _ in range(CONTAINERS):
                    db.add(
                        DataLevel(
                            reading_date=START
                            + timedelta(hours=period * hours_per_period)
                        )
                    )
            await db.commit()

    async def _ids(self) -> list[int]:
        async with self.session_maker() as db:
            return list(
                (
                    await db.execute(select(DataLevel.id).order_by(DataLevel.id))
                ).scalars()
            )

    async def _purge(self, periods: int):
        return await retention.purge_if_due(
            self.session_maker, retention_periods=periods, container_count=CONTAINERS
        )

    async def test_keeps_only_the_newest_periods(self):
        await self._add_periods(0, 20)

        deleted = await self._purge(5)

        ids = await self._ids()
        self.assertEqual(deleted, 15 * CONTAINERS)
        self.assertEqual(len(ids), 5 * CONTAINERS)
        self.assertEqual(ids[-1], 20 * CONTAINERS)

    async def test_deletes_by_id_ranges_in_batches(self):
        await self._add_periods(0, 20)

        async with self.session_maker() as db:
            deleted = await retention.purge_readings_up_to(db, 1, 40, batch_rows=7)

        self.assertEqual(deleted, 40)
        self.assertEqual((await self._ids())[0], 41)

    async def test_waits_for_a_whole_period_of_excess(self):
        await self._add_periods(0, 5)
        self.assertIsNone(await self._purge(5))

        await self._add_periods(5, 1)
        self.assertEqual(await self._purge(5), CONTAINERS)
        self.assertIsNone(await self._purge(5))

    async def test_size_does_not_depend_on_period_length(self):
        await self._add_periods(0, 20, hours_per_period=1)

        await self._purge(5)

        self.assertEqual(len(await self._ids()), 5 * CONTAINERS)

    async def test_warns_when_the_window_is_shorter_than_the_projection(self):
        await self._add_periods(0, 20, hours_per_period=1)

        with self.assertLogs(retention.logger, level="WARNING") as logs:
            await self._purge(5)

        self.assertIn("la proyeccion mira 14", logs.output[0])

    async def test_window_checks_are_not_logged_on_every_tick(self):
        await self._add_periods(0, 20, hours_per_period=1)
        with self.assertLogs(retention.logger, level="WARNING"):
            await self._purge(5)

        await self._add_periods(20, 1, hours_per_period=1)
        with self.assertNoLogs(retention.logger, level="WARNING"):
            self.assertEqual(await self._purge(5), CONTAINERS)

    async def test_zero_periods_disables_the_window(self):
        await self._add_periods(0, 20)

        self.assertIsNone(await self._purge(0))
        self.assertEqual(len(await self._ids()), 20 * CONTAINERS)

    async def test_empty_table_does_nothing(self):
        self.assertIsNone(await self._purge(5))

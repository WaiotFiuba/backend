from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services import data_level_partitions as partitions


class TestPartitionDates(unittest.TestCase):
    def test_partition_name_uses_the_day(self):
        self.assertEqual(
            partitions.partition_name(date(2026, 11, 1)), "data_level_p20261101"
        )

    def test_name_round_trips_to_the_day(self):
        day = date(2026, 2, 28)
        self.assertEqual(partitions.day_from_name(partitions.partition_name(day)), day)

    def test_other_tables_are_not_partitions(self):
        self.assertIsNone(partitions.day_from_name("data_level"))
        self.assertIsNone(partitions.day_from_name("data_level_p2026"))

    def test_day_is_taken_in_utc(self):
        # 23:30 en Buenos Aires ya es el día siguiente en UTC.
        buenos_aires = timezone(timedelta(hours=-3))
        self.assertEqual(
            partitions.day_of(datetime(2026, 11, 1, 23, 30, tzinfo=buenos_aires)),
            date(2026, 11, 2),
        )

    def test_dates_without_zone_are_utc(self):
        self.assertEqual(
            partitions.day_of(datetime(2026, 11, 1, 23, 30)), date(2026, 11, 1)
        )

    def test_day_bounds_span_one_day_in_utc(self):
        start, end = partitions.day_bounds(date(2026, 11, 1))
        self.assertEqual(start, datetime(2026, 11, 1, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 11, 2, tzinfo=UTC))


class TestPartitionsOutsidePostgres(unittest.IsolatedAsyncioTestCase):
    """En SQLite (los tests) no hay particiones: las funciones no hacen nada."""

    async def asyncSetUp(self):
        partitions.forget_known_partitions()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.db: AsyncSession = async_sessionmaker(
            self.engine, expire_on_commit=False
        )()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()
        partitions.forget_known_partitions()

    async def test_ensure_partitions_creates_nothing(self):
        created = await partitions.ensure_partitions(
            self.db, [datetime(2026, 11, 1, tzinfo=UTC)]
        )
        self.assertEqual(created, 0)

    async def test_drop_partitions_reports_a_plain_table(self):
        self.assertEqual(await partitions.drop_partitions(self.db), -1)

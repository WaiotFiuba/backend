from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.map_database import MapBase
from app.models.map.data_level import DataLevel
from app.services.map.reference_time_service import get_reference_time

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sc: MapBase.metadata.create_all(sc, tables=[DataLevel.__table__])
        )
    async with async_sessionmaker(engine, class_=AsyncSession)() as s:
        yield s
    await engine.dispose()


def _reading(reading_id: int, reading_date: datetime, level: int | None = 50):
    return DataLevel(
        id=reading_id,
        reading_date=reading_date,
        container_current_level=level,
        fire_alarm=False,
        freeze_alarm=False,
        crash_alarm=False,
        garbage_collection_alarm=False,
        reported_low_consumption_voltage=False,
        reported_high_consumption_voltage=False,
    )


async def test_returns_latest_reading_date(session):
    session.add_all(
        [
            _reading(1, datetime(2030, 1, 1, 10, tzinfo=UTC)),
            _reading(2, datetime(2030, 1, 3, 8, tzinfo=UTC)),
            _reading(3, datetime(2030, 1, 2, 9, tzinfo=UTC)),
        ]
    )
    await session.commit()

    assert await get_reference_time(session) == datetime(2030, 1, 3, 8, tzinfo=UTC)


async def test_ignores_readings_without_level(session):
    session.add_all(
        [
            _reading(1, datetime(2030, 1, 1, 10, tzinfo=UTC)),
            _reading(2, datetime(2030, 1, 5, 10, tzinfo=UTC), level=None),
        ]
    )
    await session.commit()

    assert await get_reference_time(session) == datetime(2030, 1, 1, 10, tzinfo=UTC)


async def test_falls_back_to_now_without_readings(session):
    reference = await get_reference_time(session)

    assert reference.tzinfo is not None
    assert abs(datetime.now(UTC) - reference) < timedelta(seconds=5)

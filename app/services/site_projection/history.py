from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.data_level import DataLevel

LevelAggregation = Literal["avg", "max"]


@dataclass(frozen=True)
class SiteMeasurementSample:
    site_id: int
    container_id: int
    timestamp: datetime
    level: float
    garbage_collection_alarm: bool = False


@dataclass(frozen=True)
class SiteLevelBucket:
    site_id: int
    timestamp: datetime
    level: float
    avg_level: float
    max_level: float
    min_level: float
    measurement_count: int
    container_count: int
    has_collection_signal: bool


# Tiempo máximo que se arrastra el último nivel de un contenedor que no reportó
# en un bucket. Con lecturas horarias equivale a tolerar dos lecturas perdidas.
DEFAULT_MAX_CARRY_MINUTES = 180


def aggregate_site_measurements(
    samples: list[SiteMeasurementSample],
    interval_minutes: int,
    level_aggregation: LevelAggregation = "avg",
    max_carry_minutes: int = DEFAULT_MAX_CARRY_MINUTES,
) -> dict[int, list[SiteLevelBucket]]:
    """Agrega las lecturas en buckets por sitio.

    El nivel del sitio en cada bucket combina a todos sus contenedores: los que
    reportaron en el bucket aportan su última lectura, y los que no, su último
    nivel conocido si no tiene más de `max_carry_minutes` (LOCF). Sin esto, si
    los contenedores reportan en momentos distintos, el promedio salta según
    quién reportó en cada bucket y esos saltos se confunden con recolecciones o
    con generación de residuos.
    """
    max_carry = timedelta(minutes=max_carry_minutes)
    samples_by_site: dict[int, dict[datetime, list[SiteMeasurementSample]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    for sample in samples:
        bucket_ts = floor_to_interval(sample.timestamp, interval_minutes)
        samples_by_site[sample.site_id][bucket_ts].append(sample)

    buckets_by_site: dict[int, list[SiteLevelBucket]] = {}
    for site_id, samples_by_bucket in samples_by_site.items():
        # Último nivel conocido de cada contenedor y el bucket en que se midió.
        last_known: dict[int, tuple[datetime, float]] = {}
        site_buckets: list[SiteLevelBucket] = []
        for bucket_ts in sorted(samples_by_bucket):
            bucket_samples = sorted(
                samples_by_bucket[bucket_ts], key=lambda sample: sample.timestamp
            )
            for sample in bucket_samples:
                last_known[sample.container_id] = (bucket_ts, sample.level)

            levels = [
                level
                for measured_at, level in last_known.values()
                if bucket_ts - measured_at <= max_carry
            ]
            avg_level = sum(levels) / len(levels)
            max_level = max(levels)
            level = max_level if level_aggregation == "max" else avg_level
            site_buckets.append(
                SiteLevelBucket(
                    site_id=site_id,
                    timestamp=bucket_ts,
                    level=round(level, 2),
                    avg_level=round(avg_level, 2),
                    max_level=round(max_level, 2),
                    min_level=round(min(levels), 2),
                    measurement_count=len(bucket_samples),
                    container_count=len(levels),
                    has_collection_signal=any(
                        sample.garbage_collection_alarm for sample in bucket_samples
                    ),
                )
            )
        buckets_by_site[site_id] = site_buckets

    return buckets_by_site


def floor_to_interval(timestamp: datetime, interval_minutes: int) -> datetime:
    timestamp = _as_utc(timestamp)
    minute_of_day = timestamp.hour * 60 + timestamp.minute
    floored_minute = minute_of_day - (minute_of_day % interval_minutes)
    return timestamp.replace(
        hour=floored_minute // 60,
        minute=floored_minute % 60,
        second=0,
        microsecond=0,
    )


async def get_site_level_series(
    db: AsyncSession,
    site_ids: list[int],
    interval_minutes: int = 60,
    level_aggregation: LevelAggregation = "avg",
    lookback_days: int = 14,
    end_time: datetime | None = None,
) -> dict[int, list[SiteLevelBucket]]:
    if not site_ids:
        return {}

    end_time = _as_utc(end_time or datetime.now(UTC))
    start_time = end_time - timedelta(days=lookback_days)

    stmt = (
        select(
            Container.site_id,
            DataLevel.container_id,
            DataLevel.reading_date,
            DataLevel.container_current_level,
            DataLevel.garbage_collection_alarm,
        )
        .join(Container, DataLevel.container_id == Container.id)
        .where(
            Container.site_id.in_(site_ids),
            Container.deleted_at.is_(None),
            DataLevel.container_current_level.is_not(None),
            DataLevel.container_id.is_not(None),
            DataLevel.reading_date >= start_time,
            DataLevel.reading_date <= end_time,
        )
        .order_by(Container.site_id, DataLevel.reading_date)
    )
    result = await db.execute(stmt)
    samples = [
        SiteMeasurementSample(
            site_id=int(row.site_id),
            container_id=int(row.container_id),
            timestamp=row.reading_date,
            level=float(row.container_current_level),
            garbage_collection_alarm=bool(row.garbage_collection_alarm),
        )
        for row in result.all()
        if row.site_id is not None and row.container_id is not None
    ]
    return aggregate_site_measurements(
        samples,
        interval_minutes=interval_minutes,
        level_aggregation=level_aggregation,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

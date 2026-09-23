from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.services.map.site_projection_history_service import SiteLevelBucket

CollectionDetectionReason = Literal["garbage_collection_alarm", "level_drop"]


@dataclass(frozen=True)
class CollectionEvent:
    site_id: int
    timestamp: datetime
    reason: CollectionDetectionReason
    previous_level: float | None
    current_level: float
    drop_points: float | None
    is_partial: bool


@dataclass(frozen=True)
class CollectionCycle:
    site_id: int
    started_at: datetime
    ended_at: datetime | None
    start_level: float
    end_level: float
    bucket_count: int
    collection_event: CollectionEvent | None

    @property
    def duration_hours(self) -> float | None:
        if self.ended_at is None:
            return None
        seconds = (self.ended_at - self.started_at).total_seconds()
        return round(seconds / 3600, 2)


@dataclass(frozen=True)
class CollectionCycleDetectionConfig:
    strong_drop_threshold: float = 25.0
    partial_drop_threshold: float = 10.0


def detect_collection_cycles(
    buckets: list[SiteLevelBucket],
    config: CollectionCycleDetectionConfig | None = None,
) -> list[CollectionCycle]:
    if not buckets:
        return []

    config = config or CollectionCycleDetectionConfig()
    ordered = sorted(buckets, key=lambda bucket: bucket.timestamp)
    cycles: list[CollectionCycle] = []
    cycle_start_index = 0

    for index, bucket in enumerate(ordered):
        previous_bucket = ordered[index - 1] if index > 0 else None
        event = detect_collection_event(
            bucket=bucket,
            previous_bucket=previous_bucket,
            config=config,
        )
        if event is None:
            continue

        cycle_buckets = ordered[cycle_start_index : index + 1]
        cycles.append(
            CollectionCycle(
                site_id=bucket.site_id,
                started_at=cycle_buckets[0].timestamp,
                ended_at=bucket.timestamp,
                start_level=cycle_buckets[0].level,
                end_level=bucket.level,
                bucket_count=len(cycle_buckets),
                collection_event=event,
            )
        )
        cycle_start_index = index

    remaining_buckets = ordered[cycle_start_index:]
    if remaining_buckets:
        cycles.append(
            CollectionCycle(
                site_id=remaining_buckets[0].site_id,
                started_at=remaining_buckets[0].timestamp,
                ended_at=None,
                start_level=remaining_buckets[0].level,
                end_level=remaining_buckets[-1].level,
                bucket_count=len(remaining_buckets),
                collection_event=None,
            )
        )

    return cycles


def detect_collection_event(
    bucket: SiteLevelBucket,
    previous_bucket: SiteLevelBucket | None,
    config: CollectionCycleDetectionConfig | None = None,
) -> CollectionEvent | None:
    config = config or CollectionCycleDetectionConfig()

    if bucket.has_collection_signal:
        drop_points = _drop_points(previous_bucket, bucket)
        return CollectionEvent(
            site_id=bucket.site_id,
            timestamp=bucket.timestamp,
            reason="garbage_collection_alarm",
            previous_level=previous_bucket.level if previous_bucket else None,
            current_level=bucket.level,
            drop_points=drop_points,
            is_partial=_is_partial_drop(drop_points, config),
        )

    if previous_bucket is None:
        return None

    drop_points = _drop_points(previous_bucket, bucket)
    if drop_points is None or drop_points < config.partial_drop_threshold:
        return None

    return CollectionEvent(
        site_id=bucket.site_id,
        timestamp=bucket.timestamp,
        reason="level_drop",
        previous_level=previous_bucket.level,
        current_level=bucket.level,
        drop_points=drop_points,
        is_partial=_is_partial_drop(drop_points, config),
    )


def _drop_points(
    previous_bucket: SiteLevelBucket | None,
    bucket: SiteLevelBucket,
) -> float | None:
    if previous_bucket is None:
        return None
    return round(max(0.0, previous_bucket.level - bucket.level), 2)


def _is_partial_drop(
    drop_points: float | None,
    config: CollectionCycleDetectionConfig,
) -> bool:
    return drop_points is not None and drop_points < config.strong_drop_threshold

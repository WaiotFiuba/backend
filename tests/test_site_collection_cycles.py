from datetime import UTC, datetime

from app.services.map.site_collection_cycle_service import (
    CollectionCycleDetectionConfig,
    detect_collection_cycles,
    detect_collection_event,
)
from app.services.map.site_projection_history_service import SiteLevelBucket


def bucket(
    hour: int,
    level: float,
    has_collection_signal: bool = False,
) -> SiteLevelBucket:
    return SiteLevelBucket(
        site_id=1,
        timestamp=datetime(2026, 1, 1, hour, 0, tzinfo=UTC),
        level=level,
        avg_level=level,
        max_level=level,
        min_level=level,
        measurement_count=1,
        container_count=1,
        has_collection_signal=has_collection_signal,
    )


def test_detect_collection_event_from_garbage_collection_alarm():
    previous = bucket(8, 70)
    current = bucket(9, 15, has_collection_signal=True)

    event = detect_collection_event(current, previous)

    assert event is not None
    assert event.reason == "garbage_collection_alarm"
    assert event.previous_level == 70
    assert event.current_level == 15
    assert event.drop_points == 55
    assert event.is_partial is False


def test_detect_collection_event_from_configurable_level_drop():
    previous = bucket(8, 72)
    current = bucket(9, 50)

    event = detect_collection_event(
        current,
        previous,
        config=CollectionCycleDetectionConfig(
            strong_drop_threshold=30,
            partial_drop_threshold=20,
        ),
    )

    assert event is not None
    assert event.reason == "level_drop"
    assert event.drop_points == 22
    assert event.is_partial is True


def test_detect_collection_cycles_returns_closed_and_open_cycles():
    buckets = [
        bucket(8, 30),
        bucket(9, 55),
        bucket(10, 80),
        bucket(11, 20),
        bucket(12, 35),
    ]

    cycles = detect_collection_cycles(buckets)

    assert len(cycles) == 2
    assert cycles[0].started_at == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    assert cycles[0].ended_at == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    assert cycles[0].start_level == 30
    assert cycles[0].end_level == 20
    assert cycles[0].collection_event is not None
    assert cycles[0].collection_event.reason == "level_drop"
    assert cycles[1].started_at == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    assert cycles[1].ended_at is None
    assert cycles[1].end_level == 35

from datetime import UTC, datetime

import pytest

from app.services.site_projection.collection_cycle import (
    CollectionCycleDetectionConfig,
    detect_collection_cycles,
    detect_collection_event,
)
from app.services.site_projection.history import SiteLevelBucket


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


@pytest.mark.parametrize(
    ("before", "after", "counts"),
    [
        (90, 5, True),
        (60, 8, True),
        (25, 4, True),
        (70, 15, False),  # queda arriba del 10%
        (80, 40, False),  # vacía solo la mitad
        (6, 1, False),  # antes estaba debajo del 10%
        (72, 50, False),  # una caída de 22 puntos ya no alcanza
    ],
)
def test_without_alarm_only_an_almost_empty_site_counts_as_collection(
    before, after, counts
):
    event = detect_collection_event(bucket(9, after), bucket(8, before))

    assert (event is not None) is counts
    if counts:
        assert event.reason == "level_drop"
        assert event.drop_points == before - after


def test_the_fallback_thresholds_are_configurable():
    config = CollectionCycleDetectionConfig(
        min_level_before=20, min_emptied_fraction=0.5, max_level_after=30
    )

    event = detect_collection_event(bucket(9, 25), bucket(8, 60), config=config)

    assert event is not None
    assert event.reason == "level_drop"
    assert event.is_partial is False
    assert detect_collection_event(bucket(9, 5), bucket(8, 15), config=config) is None


def test_detect_collection_cycles_returns_closed_and_open_cycles():
    buckets = [
        bucket(8, 30),
        bucket(9, 55),
        bucket(10, 80),
        bucket(11, 4),
        bucket(12, 35),
    ]

    cycles = detect_collection_cycles(buckets)

    assert len(cycles) == 2
    assert cycles[0].started_at == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    assert cycles[0].ended_at == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    assert cycles[0].start_level == 30
    assert cycles[0].end_level == 4
    assert cycles[0].collection_event is not None
    assert cycles[0].collection_event.reason == "level_drop"
    assert cycles[1].started_at == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    assert cycles[1].ended_at is None
    assert cycles[1].end_level == 35

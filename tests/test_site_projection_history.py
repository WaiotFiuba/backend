from datetime import UTC, datetime

from app.services.map.site_projection_history_service import (
    SiteMeasurementSample,
    aggregate_site_measurements,
    floor_to_interval,
)


def test_floor_to_interval_uses_fixed_wall_clock_buckets():
    timestamp = datetime(2026, 1, 1, 8, 47, 30, tzinfo=UTC)

    assert floor_to_interval(timestamp, interval_minutes=60) == datetime(
        2026, 1, 1, 8, 0, tzinfo=UTC
    )
    assert floor_to_interval(timestamp, interval_minutes=15) == datetime(
        2026, 1, 1, 8, 45, tzinfo=UTC
    )


def test_aggregate_site_measurements_supports_avg_and_multiple_containers():
    samples = [
        SiteMeasurementSample(
            site_id=1,
            container_id=10,
            timestamp=datetime(2026, 1, 1, 8, 5, tzinfo=UTC),
            level=20,
        ),
        SiteMeasurementSample(
            site_id=1,
            container_id=11,
            timestamp=datetime(2026, 1, 1, 8, 55, tzinfo=UTC),
            level=40,
            garbage_collection_alarm=True,
        ),
        SiteMeasurementSample(
            site_id=1,
            container_id=10,
            timestamp=datetime(2026, 1, 1, 9, 1, tzinfo=UTC),
            level=50,
        ),
    ]

    buckets = aggregate_site_measurements(samples, interval_minutes=60)[1]

    assert len(buckets) == 2
    assert buckets[0].timestamp == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    assert buckets[0].level == 30
    assert buckets[0].avg_level == 30
    assert buckets[0].max_level == 40
    assert buckets[0].min_level == 20
    assert buckets[0].measurement_count == 2
    assert buckets[0].container_count == 2
    assert buckets[0].has_collection_signal is True
    assert buckets[1].level == 50


def test_aggregate_site_measurements_supports_max_level():
    samples = [
        SiteMeasurementSample(
            site_id=1,
            container_id=10,
            timestamp=datetime(2026, 1, 1, 8, 5, tzinfo=UTC),
            level=20,
        ),
        SiteMeasurementSample(
            site_id=1,
            container_id=11,
            timestamp=datetime(2026, 1, 1, 8, 55, tzinfo=UTC),
            level=40,
        ),
    ]

    buckets = aggregate_site_measurements(
        samples,
        interval_minutes=60,
        level_aggregation="max",
    )[1]

    assert buckets[0].level == 40
    assert buckets[0].avg_level == 30

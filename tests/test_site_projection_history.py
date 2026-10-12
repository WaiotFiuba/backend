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
    # A las 9:00 solo reportó el contenedor 10 (50%); el 11 conserva su último
    # nivel conocido (40% a las 8:55), así que el sitio queda en 45%.
    assert buckets[1].level == 45
    assert buckets[1].measurement_count == 1
    assert buckets[1].container_count == 2


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


def _sample(container_id: int, hour: int, level: float) -> SiteMeasurementSample:
    return SiteMeasurementSample(
        site_id=1,
        container_id=container_id,
        timestamp=datetime(2026, 1, 1, hour, 10, tzinfo=UTC),
        level=level,
    )


def test_staggered_reports_do_not_create_fake_jumps():
    # A y B reportan juntos a las 7; después se alternan. Sin arrastrar el último
    # nivel, el sitio saltaría 50 -> 82 -> 22 -> 84 (una recolección falsa).
    samples = [
        _sample(10, 7, 80),
        _sample(11, 7, 20),
        _sample(10, 8, 82),
        _sample(11, 9, 22),
        _sample(10, 10, 84),
    ]

    buckets = aggregate_site_measurements(samples, interval_minutes=60)[1]

    assert [bucket.level for bucket in buckets] == [50, 51, 52, 53]
    assert all(bucket.container_count == 2 for bucket in buckets)


def test_last_known_level_expires_after_max_carry():
    samples = [
        _sample(10, 7, 80),
        _sample(11, 7, 20),
        _sample(10, 10, 80),  # B lleva 3 h sin reportar: todavía cuenta
        _sample(10, 11, 80),  # B lleva 4 h: ya no cuenta
    ]

    buckets = aggregate_site_measurements(samples, interval_minutes=60)[1]

    assert [bucket.level for bucket in buckets] == [50, 50, 80]
    assert [bucket.container_count for bucket in buckets] == [2, 2, 1]

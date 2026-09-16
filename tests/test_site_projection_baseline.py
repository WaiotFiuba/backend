from datetime import UTC, datetime, timedelta

import pytest

from app.services.map.site_projection_history_service import SiteLevelBucket
from app.services.map.site_projection_models import (
    DEFAULT_MODEL_KEY,
    ForecastContext,
    ForecastRequest,
    get_forecaster,
    list_projection_models,
)


def bucket(
    day: int,
    hour: int,
    level: float,
    has_collection_signal: bool = False,
) -> SiteLevelBucket:
    return SiteLevelBucket(
        site_id=1,
        timestamp=datetime(2026, 1, day, hour, 0, tzinfo=UTC),
        level=level,
        avg_level=level,
        max_level=level,
        min_level=level,
        measurement_count=1,
        container_count=1,
        has_collection_signal=has_collection_signal,
    )


def bucket_at(
    timestamp: datetime,
    level: float,
    has_collection_signal: bool = False,
) -> SiteLevelBucket:
    return SiteLevelBucket(
        site_id=1,
        timestamp=timestamp,
        level=level,
        avg_level=level,
        max_level=level,
        min_level=level,
        measurement_count=1,
        container_count=1,
        has_collection_signal=has_collection_signal,
    )


@pytest.mark.asyncio
async def test_baseline_operational_is_available_from_registry():
    models = list_projection_models()

    assert models[0].key == DEFAULT_MODEL_KEY
    assert models[0].status == "available"
    assert get_forecaster(DEFAULT_MODEL_KEY) is not None


@pytest.mark.asyncio
async def test_baseline_projects_positive_increments_and_ignores_collection_drop():
    forecaster = get_forecaster(DEFAULT_MODEL_KEY)
    assert forecaster is not None

    result = await forecaster.forecast(
        ForecastRequest(site_id=1, horizon_hours=3, interval_minutes=60),
        ForecastContext(
            current_level=20,
            generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            site_features={},
            history=[
                bucket(1, 8, 20),
                bucket(1, 9, 30),
                bucket(1, 10, 40),
                bucket(1, 11, 5, has_collection_signal=True),
                bucket(1, 12, 15),
            ],
        ),
    )

    assert [point.predicted_level for point in result.points] == [25, 35, 45]
    assert all(0 <= point.predicted_level <= 100 for point in result.points)


@pytest.mark.asyncio
async def test_baseline_uses_density_feature_only_for_low_history_fallback():
    forecaster = get_forecaster(DEFAULT_MODEL_KEY)
    assert forecaster is not None

    result = await forecaster.forecast(
        ForecastRequest(site_id=1, horizon_hours=2, interval_minutes=60),
        ForecastContext(
            current_level=10,
            generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            site_features={"demand.density_factor": 2.0},
            history=[],
        ),
    )

    assert [point.predicted_level for point in result.points] == [11.5, 13.0]
    assert result.points[0].confidence == 0.35
    assert result.points[0].reason == "fallback_due_to_low_history"


@pytest.mark.asyncio
async def test_baseline_stops_at_full_when_configured():
    forecaster = get_forecaster(DEFAULT_MODEL_KEY)
    assert forecaster is not None

    result = await forecaster.forecast(
        ForecastRequest(
            site_id=1,
            horizon_hours=24,
            interval_minutes=60,
            stop_at_full=True,
        ),
        ForecastContext(
            current_level=98,
            generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            site_features={},
            history=[],
        ),
    )

    assert result.points[-1].predicted_level == 100
    assert len(result.points) == 2


@pytest.mark.asyncio
async def test_baseline_aligns_projection_to_fixed_future_buckets():
    forecaster = get_forecaster(DEFAULT_MODEL_KEY)
    assert forecaster is not None

    result = await forecaster.forecast(
        ForecastRequest(site_id=1, horizon_hours=3, interval_minutes=60),
        ForecastContext(
            current_level=10,
            generated_at=datetime(2026, 1, 1, 8, 47, tzinfo=UTC),
            site_features={},
            history=[],
        ),
    )

    assert [point.timestamp for point in result.points] == [
        datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
    ]


@pytest.mark.asyncio
async def test_baseline_ignores_density_feature_when_site_history_is_enough():
    forecaster = get_forecaster(DEFAULT_MODEL_KEY)
    assert forecaster is not None

    history = [
        bucket(1, 8, 10),
        bucket(1, 9, 12),
        bucket(1, 10, 14),
        bucket(1, 11, 16),
        bucket(1, 12, 18),
        bucket(1, 13, 20),
        bucket(1, 14, 22),
    ]

    result = await forecaster.forecast(
        ForecastRequest(site_id=1, horizon_hours=2, interval_minutes=60),
        ForecastContext(
            current_level=0,
            generated_at=datetime(2026, 1, 1, 14, 0, tzinfo=UTC),
            site_features={"demand.density_factor": 2.0},
            history=history,
        ),
    )

    assert [point.predicted_level for point in result.points] == [24, 26]


@pytest.mark.asyncio
async def test_baseline_reports_high_confidence_with_rich_history():
    forecaster = get_forecaster(DEFAULT_MODEL_KEY)
    assert forecaster is not None

    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    history = [
        bucket_at(start + timedelta(hours=offset), level=10 + offset * 0.5)
        for offset in range(49)
    ]

    result = await forecaster.forecast(
        ForecastRequest(site_id=1, horizon_hours=1, interval_minutes=60),
        ForecastContext(
            current_level=0,
            generated_at=start + timedelta(hours=48),
            site_features={},
            history=history,
        ),
    )

    assert result.points[0].confidence == 0.85
    assert result.points[0].reason is None
    assert result.points[0].lower_bound == 26.5
    assert result.points[0].upper_bound == 42.5


@pytest.mark.asyncio
async def test_baseline_clamps_levels_to_full_even_when_not_stopping_at_full():
    forecaster = get_forecaster(DEFAULT_MODEL_KEY)
    assert forecaster is not None

    result = await forecaster.forecast(
        ForecastRequest(
            site_id=1,
            horizon_hours=3,
            interval_minutes=60,
            stop_at_full=False,
        ),
        ForecastContext(
            current_level=99,
            generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            site_features={},
            history=[],
        ),
    )

    assert [point.predicted_level for point in result.points] == [100, 100, 100]

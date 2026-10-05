from datetime import UTC, datetime, timedelta

import pytest

from app.services.map.site_projection_history_service import SiteLevelBucket
from app.services.map.site_projection_models import (
    ForecastContext,
    ForecastRequest,
    get_forecaster,
    list_projection_models,
)

CHRONOS_KEY = "chronos_2_small"


def bucket(
    hour_offset: int,
    level: float,
) -> SiteLevelBucket:
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    return SiteLevelBucket(
        site_id=1,
        timestamp=base_time + timedelta(hours=hour_offset),
        level=level,
        avg_level=level,
        max_level=level,
        min_level=level,
        measurement_count=1,
        container_count=1,
        has_collection_signal=False,
    )


@pytest.mark.asyncio
async def test_chronos_model_is_available_in_registry():
    models = list_projection_models()
    model_keys = [m.key for m in models]

    assert CHRONOS_KEY in model_keys
    forecaster = get_forecaster(CHRONOS_KEY)
    assert forecaster is not None
    assert forecaster.model_info.key == CHRONOS_KEY
    assert forecaster.model_info.status == "available"


@pytest.mark.asyncio
async def test_chronos_fallback_when_insufficient_history():
    forecaster = get_forecaster(CHRONOS_KEY)
    assert forecaster is not None

    result = await forecaster.forecast(
        ForecastRequest(site_id=1, horizon_hours=3, interval_minutes=60),
        ForecastContext(
            current_level=20,
            generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            site_features={},
            history=[
                bucket(0, 10),
                bucket(1, 15),
            ],
        ),
    )

    assert result.model_key == CHRONOS_KEY
    assert len(result.points) == 3
    assert result.points[0].reason == "insufficient_history"
    assert result.points[0].confidence == 0.35
    assert all(0 <= p.predicted_level <= 100 for p in result.points)


@pytest.mark.asyncio
async def test_chronos_forecast_generates_bounded_probabilistic_points():
    forecaster = get_forecaster(CHRONOS_KEY)
    assert forecaster is not None

    # History with 12 hourly observations
    history = [bucket(i, 20.0 + i * 2.5) for i in range(12)]

    result = await forecaster.forecast(
        ForecastRequest(
            site_id=1,
            horizon_hours=6,
            interval_minutes=60,
            critical_level=80,
            stop_at_full=True,
        ),
        ForecastContext(
            current_level=47.5,
            generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            site_features={},
            history=history,
        ),
    )

    assert result.model_key == CHRONOS_KEY
    assert len(result.points) == 6

    for point in result.points:
        assert 0.0 <= point.predicted_level <= 100.0
        assert point.lower_bound is not None
        assert point.upper_bound is not None
        assert point.lower_bound <= point.predicted_level <= point.upper_bound
        assert 0.0 <= point.confidence <= 1.0

from datetime import UTC, datetime

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

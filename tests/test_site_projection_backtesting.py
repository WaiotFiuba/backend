from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.schemas.map.site_projection import SiteProjectionEvaluationRequest
from app.services.map.site_projection_evaluation_service import (
    SiteBacktestRequest,
    _resolve_automatic_cutoff,
    evaluate_site_forecast_backtest,
)
from app.services.map.site_projection_history_service import SiteLevelBucket


def bucket_at(timestamp: datetime, level: float) -> SiteLevelBucket:
    return SiteLevelBucket(
        site_id=1,
        timestamp=timestamp,
        level=level,
        avg_level=level,
        max_level=level,
        min_level=level,
        measurement_count=1,
        container_count=1,
        has_collection_signal=False,
    )


class FakeResult:
    def __init__(self, row):
        self.row = row

    def one(self):
        return self.row


class FakeDb:
    def __init__(self, row):
        self.row = row

    async def execute(self, _stmt):
        return FakeResult(self.row)


@pytest.mark.asyncio
async def test_automatic_cutoff_uses_latest_observation_minus_horizon():
    earliest = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    latest = datetime(2026, 1, 1, 16, 0, tzinfo=UTC)

    cutoff = await _resolve_automatic_cutoff(
        db=FakeDb((earliest, latest)),
        request=SiteProjectionEvaluationRequest(
            site_sample_size=5,
            horizon_hours=3,
        ),
    )

    assert cutoff == datetime(2026, 1, 1, 13, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_automatic_cutoff_rejects_horizon_without_future_observations():
    earliest = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    latest = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_automatic_cutoff(
            db=FakeDb((earliest, latest)),
            request=SiteProjectionEvaluationRequest(
                site_sample_size=5,
                horizon_hours=24,
            ),
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_backtest_compares_predictions_against_future_observations():
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    history = [
        bucket_at(start + timedelta(hours=0), 10),
        bucket_at(start + timedelta(hours=1), 20),
        bucket_at(start + timedelta(hours=2), 30),
        bucket_at(start + timedelta(hours=3), 40),
        bucket_at(start + timedelta(hours=4), 50),
    ]

    result = await evaluate_site_forecast_backtest(
        history=history,
        request=SiteBacktestRequest(
            site_id=1,
            cutoff=start + timedelta(hours=2),
            horizon_hours=2,
            interval_minutes=60,
            critical_level=80,
        ),
    )

    assert result.compared_points == 2
    assert result.mae == 0
    assert result.rmse == 0
    assert [point.predicted_level for point in result.points] == [40, 50]
    assert [point.observed_level for point in result.points] == [40, 50]
    assert result.threshold_metrics.true_negatives == 2


@pytest.mark.asyncio
async def test_backtest_reports_threshold_metrics_and_critical_time_error():
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    history = [
        bucket_at(start + timedelta(hours=0), 40),
        bucket_at(start + timedelta(hours=1), 50),
        bucket_at(start + timedelta(hours=2), 60),
        bucket_at(start + timedelta(hours=3), 95),
        bucket_at(start + timedelta(hours=4), 95),
    ]

    result = await evaluate_site_forecast_backtest(
        history=history,
        request=SiteBacktestRequest(
            site_id=1,
            cutoff=start + timedelta(hours=2),
            horizon_hours=2,
            interval_minutes=60,
            critical_level=80,
        ),
    )

    assert result.predicted_critical_at == start + timedelta(hours=4)
    assert result.observed_critical_at == start + timedelta(hours=3)
    assert result.critical_time_error_hours == 1
    assert result.threshold_metrics.true_positives == 1
    assert result.threshold_metrics.false_negatives == 1
    assert result.threshold_metrics.precision == 1
    assert result.threshold_metrics.recall == 0.5


@pytest.mark.asyncio
async def test_backtest_returns_empty_metrics_when_there_are_no_future_observations():
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    history = [
        bucket_at(start + timedelta(hours=0), 10),
        bucket_at(start + timedelta(hours=1), 20),
    ]

    result = await evaluate_site_forecast_backtest(
        history=history,
        request=SiteBacktestRequest(
            site_id=1,
            cutoff=start + timedelta(hours=1),
            horizon_hours=2,
            interval_minutes=60,
        ),
    )

    assert result.compared_points == 0
    assert result.mae is None
    assert result.rmse is None
    assert result.points == []

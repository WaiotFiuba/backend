from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.map.site_projection import SiteProjectionEvaluationRequest
from app.services.site_projection import evaluation as ev
from app.services.site_projection.evaluation import (
    SiteBacktestRequest,
    SiteBacktestResult,
    ThresholdMetrics,
    _evaluation_window_days,
    _merge_site_results,
    _prepare_backtest,
    rolling_cutoffs,
)
from app.services.site_projection.history import SiteLevelBucket

EARLIEST = datetime(2026, 1, 1, tzinfo=UTC)


def _request(**kwargs):
    return SiteProjectionEvaluationRequest(site_sample_size=10, **kwargs)


def test_cutoffs_are_anchored_spaced_and_fall_on_different_hours():
    latest = EARLIEST + timedelta(days=20, hours=5)
    cutoffs = rolling_cutoffs(latest, EARLIEST, _request())

    assert len(cutoffs) == 14
    assert cutoffs == sorted(cutoffs)
    assert {b - a for a, b in zip(cutoffs, cutoffs[1:])} == {timedelta(hours=13)}
    assert len({cutoff.hour for cutoff in cutoffs}) == 14
    assert cutoffs[-1] <= latest
    # Correr la evaluacion unas horas despues da los mismos cortes.
    assert rolling_cutoffs(latest + timedelta(hours=3), EARLIEST, _request()) == (
        cutoffs
    )


def test_cutoffs_every_12_hours_fall_at_midnight_and_noon():
    latest = EARLIEST + timedelta(days=20, hours=17)
    cutoffs = rolling_cutoffs(
        latest, EARLIEST, _request(cutoff_spacing_hours=12, cutoff_count=4)
    )

    assert [c.hour for c in cutoffs] == [0, 12, 0, 12]
    assert cutoffs[-1] == EARLIEST + timedelta(days=20, hours=12)


def test_cutoffs_without_enough_history_are_dropped():
    latest = EARLIEST + timedelta(days=9)
    cutoffs = rolling_cutoffs(latest, EARLIEST, _request(cutoff_spacing_hours=24))

    assert cutoffs == [EARLIEST + timedelta(days=day) for day in (7, 8, 9)]


def test_a_single_cutoff_is_the_latest_grid_point():
    latest = EARLIEST + timedelta(days=10, hours=7)
    cutoffs = rolling_cutoffs(
        latest, EARLIEST, _request(cutoff_count=1, cutoff_spacing_hours=1)
    )

    assert cutoffs == [latest]


@pytest.mark.asyncio
async def test_short_history_evaluates_only_the_latest_cutoff(monkeypatch):
    async def reading_range(_db):
        return EARLIEST, EARLIEST + timedelta(days=3, minutes=30)

    monkeypatch.setattr(ev, "_reading_range", reading_range)

    cutoffs, short_history = await ev._resolve_cutoffs(object(), _request())

    assert cutoffs == [EARLIEST + timedelta(days=2)]
    assert short_history is True


@pytest.mark.asyncio
async def test_long_history_is_not_flagged(monkeypatch):
    async def reading_range(_db):
        return EARLIEST, EARLIEST + timedelta(days=20)

    monkeypatch.setattr(ev, "_reading_range", reading_range)

    cutoffs, short_history = await ev._resolve_cutoffs(object(), _request())

    assert len(cutoffs) == 14
    assert short_history is False


def test_window_covers_history_of_first_cutoff_and_horizon_of_last():
    cutoffs = rolling_cutoffs(EARLIEST + timedelta(days=30), EARLIEST, _request())

    # 13 x 13 h + 24 h de horizonte = 193 h: 9 dias, mas 14 de historia.
    assert _evaluation_window_days(_request(), cutoffs) == 23


def _bucket(timestamp, level):
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


def test_prepare_backtest_uses_only_lookback_days_before_the_cutoff():
    cutoff = EARLIEST + timedelta(days=5)
    history = [_bucket(EARLIEST + timedelta(hours=h), 10) for h in range(6 * 24)]

    _, context, observed = _prepare_backtest(
        history,
        SiteBacktestRequest(site_id=1, cutoff=cutoff, horizon_hours=2, lookback_days=2),
    )

    assert context.history[0].timestamp == cutoff - timedelta(days=2, hours=-1)
    assert context.history[-1].timestamp == cutoff
    assert [b.timestamp for b in observed] == [
        cutoff + timedelta(hours=1),
        cutoff + timedelta(hours=2),
    ]


def _result(cutoff, mae, rmse, points, tp, fp, fn, error=None):
    return SiteBacktestResult(
        site_id=1,
        model_key="baseline_operational",
        cutoff=cutoff,
        horizon_hours=24,
        interval_minutes=60,
        compared_points=points,
        mae=mae,
        rmse=rmse,
        predicted_critical_at=None,
        observed_critical_at=None,
        critical_time_error_hours=error,
        threshold_metrics=ThresholdMetrics(tp, fp, fn, 0, None, None),
        points=[],
    )


def test_one_row_per_site_weighted_by_compared_points():
    first, second = EARLIEST, EARLIEST + timedelta(hours=13)
    merged = _merge_site_results(
        [
            _result(first, 1.0, 1.0, 10, 2, 1, 0, error=-2.0),
            _result(second, 4.0, 4.0, 30, 1, 0, 1, error=4.0),
        ]
    )

    assert len(merged) == 1
    site = merged[0]
    assert site.cutoff == second
    assert site.compared_points == 40
    assert site.mae == 3.25
    assert site.rmse == round((0.25 * 1 + 0.75 * 16) ** 0.5, 4)
    assert site.critical_time_error_hours == 3.0
    assert (site.threshold_metrics.precision, site.threshold_metrics.recall) == (
        0.75,
        0.75,
    )

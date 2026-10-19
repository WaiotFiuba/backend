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
    _build_evaluation_summary,
    _score_backtest,
    event_metrics,
    rolling_cutoffs,
)
from app.services.site_projection.history import SiteLevelBucket
from app.services.site_projection.models import ForecastPoint, ForecastResult

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


def _bucket(timestamp, level, collected=False):
    return SiteLevelBucket(
        site_id=1,
        timestamp=timestamp,
        level=level,
        avg_level=level,
        max_level=level,
        min_level=level,
        measurement_count=1,
        container_count=1,
        has_collection_signal=collected,
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


def test_events_are_measured_against_the_crossing_without_collection():
    cutoff = EARLIEST + timedelta(hours=5)
    training = [_bucket(EARLIEST + timedelta(hours=h), 10 * h) for h in range(6)]
    # El camion vacia en 70: el nivel real nunca llega a 80.
    observed = [
        _bucket(cutoff + timedelta(hours=1), 60),
        _bucket(cutoff + timedelta(hours=2), 70),
        _bucket(cutoff + timedelta(hours=3), 5, collected=True),
        _bucket(cutoff + timedelta(hours=4), 15),
    ]
    forecast = ForecastResult(
        site_id=1,
        model_key="baseline_operational",
        generated_at=cutoff,
        points=[
            ForecastPoint(
                timestamp=cutoff + timedelta(hours=h),
                predicted_level=level,
                confidence=1,
            )
            for h, level in [(1, 60), (2, 70), (3, 80), (4, 90)]
        ],
    )

    result = _score_backtest(
        SiteBacktestRequest(site_id=1, cutoff=cutoff, horizon_hours=4),
        forecast,
        observed,
        training,
    )

    assert result.observed_critical_at == cutoff + timedelta(hours=3)
    assert result.predicted_critical_at == cutoff + timedelta(hours=3)
    assert result.critical_time_error_hours == 0
    # El vaciado no cuenta en contra: se compara contra 60, 70, 80 y 90.
    assert [point.observed_level for point in result.points] == [60, 70, 80, 90]
    assert result.mae == 0
    assert result.threshold_metrics.true_positives == 2


def test_level_reference_is_capped_at_100():
    cutoff = EARLIEST + timedelta(hours=2)
    training = [_bucket(EARLIEST + timedelta(hours=h), 70 + 10 * h) for h in range(3)]
    observed = [
        _bucket(cutoff + timedelta(hours=1), 100),
        _bucket(cutoff + timedelta(hours=2), 5, collected=True),
    ]
    forecast = ForecastResult(
        site_id=1,
        model_key="baseline_operational",
        generated_at=cutoff,
        points=[
            ForecastPoint(
                timestamp=cutoff + timedelta(hours=h),
                predicted_level=100,
                confidence=1,
            )
            for h in (1, 2)
        ],
    )

    result = _score_backtest(
        SiteBacktestRequest(site_id=1, cutoff=cutoff, horizon_hours=2),
        forecast,
        observed,
        training,
    )

    # Sin recoleccion llegaria a 110: se compara contra 100.
    assert [point.observed_level for point in result.points] == [100, 100]
    assert result.mae == 0


def _event(predicted_hour, observed_hour, critical_at_cutoff=False):
    cutoff = EARLIEST

    def at(hour):
        return None if hour is None else cutoff + timedelta(hours=hour)

    return SiteBacktestResult(
        site_id=1,
        model_key="baseline_operational",
        cutoff=cutoff,
        horizon_hours=24,
        interval_minutes=60,
        compared_points=24,
        mae=None,
        rmse=None,
        predicted_critical_at=at(predicted_hour),
        observed_critical_at=at(observed_hour),
        critical_time_error_hours=(
            predicted_hour - observed_hour
            if predicted_hour is not None and observed_hour is not None
            else None
        ),
        threshold_metrics=ThresholdMetrics(0, 0, 0, 0, None, None),
        points=[],
        critical_at_cutoff=critical_at_cutoff,
    )


def test_event_metrics_count_one_case_per_site_and_cutoff():
    events = event_metrics(
        [
            _event(5, 6),  # avisa 1 h antes: acierto
            _event(9, 8),  # avisa 1 h despues: acierto
            _event(3, None),  # avisa y no cruza: falsa alarma
            _event(None, 4),  # cruza y no avisa: perdido
            _event(None, None),  # ni una cosa ni la otra
        ]
    )

    assert events["event_true_positives"] == 2
    assert events["event_false_positives"] == 1
    assert events["event_false_negatives"] == 1
    assert events["event_true_negatives"] == 1
    assert events["event_observed"] == 3
    assert events["event_prevalence"] == 0.6
    assert events["event_precision"] == round(2 / 3, 4)
    assert events["event_recall"] == round(2 / 3, 4)
    assert events["event_f1"] == round(2 / 3, 4)
    assert events["critical_time_mae_hours"] == 1
    assert events["critical_time_bias_hours"] == 0


def test_warnings_outside_the_window_are_false_alarms_or_misses():
    events = event_metrics(
        [
            _event(4, 6),  # 2 h antes: dentro de la ventana
            _event(0, 6),  # 6 h antes: visita de mas, falsa alarma
            _event(9, 6),  # 3 h despues: desborde, cruce perdido
            _event(7, 6),  # 1 h despues: dentro de la ventana
        ],
        early_hours=3,
        late_hours=1,
    )

    assert events["event_true_positives"] == 2
    assert events["event_too_early"] == 1
    assert events["event_too_late"] == 1
    assert events["event_false_positives"] == 1
    assert events["event_false_negatives"] == 1
    assert events["event_observed"] == 4
    assert events["event_precision"] == round(2 / 3, 4)
    assert events["event_recall"] == round(2 / 3, 4)
    # Sin mirar la hora, los cuatro son aciertos.
    assert events["event_horizon_f1"] == 1
    assert events["critical_time_bias_hours"] == -1


def test_event_f1_is_none_without_hits():
    events = event_metrics([_event(3, None), _event(None, None)])

    assert events["event_precision"] == 0
    assert events["event_recall"] is None
    assert events["event_f1"] is None


def test_a_site_already_critical_at_the_cutoff_is_flagged():
    cutoff = EARLIEST + timedelta(hours=2)
    training = [_bucket(EARLIEST + timedelta(hours=h), 70 + 5 * h) for h in range(3)]
    observed = [_bucket(cutoff + timedelta(hours=1), 85)]
    forecast = ForecastResult(
        site_id=1,
        model_key="baseline_operational",
        generated_at=cutoff,
        points=[
            ForecastPoint(
                timestamp=cutoff + timedelta(hours=1), predicted_level=85, confidence=1
            )
        ],
    )

    result = _score_backtest(
        SiteBacktestRequest(site_id=1, cutoff=cutoff, horizon_hours=1),
        forecast,
        observed,
        training,
    )

    assert result.critical_at_cutoff is True


def test_event_metrics_leave_out_sites_already_critical_at_the_cutoff():
    results = [
        _event(1, 1, critical_at_cutoff=True),  # ya critico: se sabe sin pronosticar
        _event(1, 1, critical_at_cutoff=True),
        _event(5, 6),  # acierto
        _event(None, 4),  # perdido
    ]

    summary = _build_evaluation_summary(results)

    assert summary["case_count"] == 4
    assert summary["critical_at_cutoff"] == 2
    assert summary["events_observed"] == 2
    assert summary["event_recall"] == 0.5

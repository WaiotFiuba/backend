from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.site_projection import chronos
from app.services.site_projection.generation import (
    first_crossing,
    hourly_generation,
    uncollected_levels,
)
from app.services.site_projection.history import SiteLevelBucket
from app.services.site_projection.models import ForecastContext, ForecastRequest

START = datetime(2026, 1, 1, tzinfo=UTC)


def _buckets(levels, signal_at=(), start=START):
    return [
        SiteLevelBucket(
            site_id=1,
            timestamp=start + timedelta(hours=index),
            level=level,
            avg_level=level,
            max_level=level,
            min_level=level,
            measurement_count=1,
            container_count=1,
            has_collection_signal=index in signal_at,
        )
        for index, level in enumerate(levels)
    ]


def test_hourly_generation_uses_the_typical_increment_of_that_hour():
    # El primer día sube 5 a las 3 y 1 el resto; el segundo vacían a las 3.
    first_day = [10 + hour + 4 * (hour >= 3) for hour in range(24)]
    second_day = [first_day[-1] + hour + 1 for hour in range(3)] + [2]
    generation = hourly_generation(_buckets(first_day + second_day))
    assert generation[27] == 5


def test_hourly_generation_treats_collection_signal_as_collection():
    generation = hourly_generation(_buckets([10, 12, 14, 30, 32], signal_at={3}))
    assert generation == [0, 2, 2, 2, 2]


def test_hourly_generation_sorts_by_timestamp():
    buckets = _buckets([10, 12, 15])
    assert hourly_generation(buckets[::-1]) == [0, 2, 3]


def test_hourly_generation_edge_cases():
    assert hourly_generation([]) == []
    assert hourly_generation(_buckets([40])) == [0]
    assert hourly_generation(_buckets([50, 20, 5])) == [0, 0, 0]


def test_hourly_generation_replaces_collection_drop_with_typical_increment():
    assert hourly_generation(_buckets([10, 13, 16, 2, 5, 8])) == [0, 3, 3, 3, 3, 3]


def test_levels_from_hourly_accumulates_without_going_down():
    assert chronos._levels_from_hourly(40, [5, -2, 3]) == [45, 45, 48]
    assert chronos._levels_from_hourly(95, [10]) == [100]


@pytest.mark.asyncio
async def test_chronos_does_not_see_the_collection_and_accumulates_levels_and_bands(
    monkeypatch,
):
    seen = []

    class FakePipeline:
        def predict_df(self, frame, **_kwargs):
            seen.append(list(frame["target"]))
            return pd.DataFrame({"0.1": [1, -1], "0.5": [4, 3], "0.9": [8, 6]})

    monkeypatch.setattr(chronos, "_get_chronos_pipeline", FakePipeline)
    monkeypatch.setattr(chronos, "_forecast_quantile", lambda: 0.5)
    # El camión pasa en la última hora: el sitio queda en 5.
    history = _buckets([30, 40, 50, 60, 5])
    result = await chronos.Chronos2SmallForecaster().forecast(
        ForecastRequest(site_id=1, horizon_hours=2),
        ForecastContext(
            current_level=5,
            history=history,
            site_features={},
            generated_at=history[-1].timestamp,
            snapshot_at=history[-1].timestamp,
        ),
    )

    assert seen == [[0, 10, 10, 10, 10]]
    assert [
        (point.lower_bound, point.predicted_level, point.upper_bound)
        for point in result.points
    ] == [(6, 9, 13), (6, 12, 19)]


def test_forecast_quantile_is_configurable_and_bounded():
    assert Settings().chronos_forecast_quantile == 0.6
    assert Settings(chronos_forecast_quantile=0.5).chronos_forecast_quantile == 0.5
    with pytest.raises(ValidationError):
        Settings(chronos_forecast_quantile=0.95)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quantile", "levels", "expected"),
    [(0.5, [0.1, 0.5, 0.9], 42), (0.6, [0.1, 0.6, 0.9], 44)],
)
async def test_chronos_reads_the_configured_quantile(
    monkeypatch, quantile, levels, expected
):
    seen = []

    class FakePipeline:
        def predict_df(self, _frame, **kwargs):
            seen.append(kwargs["quantile_levels"])
            return pd.DataFrame({"0.1": [1], "0.5": [2], "0.6": [4], "0.9": [6]})

    monkeypatch.setattr(chronos, "_get_chronos_pipeline", FakePipeline)
    monkeypatch.setattr(chronos, "_forecast_quantile", lambda: quantile)
    history = _buckets([10, 20, 30, 40])
    result = await chronos.Chronos2SmallForecaster().forecast(
        ForecastRequest(site_id=1, horizon_hours=1),
        ForecastContext(
            current_level=40,
            history=history,
            site_features={},
            generated_at=history[-1].timestamp,
            snapshot_at=history[-1].timestamp,
        ),
    )

    assert seen == [levels]
    assert result.points[0].predicted_level == expected


def test_uncollected_levels_continue_after_a_real_collection():
    training = _buckets([0, 10, 20, 30, 40, 50])
    observed = _buckets(
        [60, 70, 5, 15, 25], signal_at={2}, start=START + timedelta(hours=6)
    )

    levels = uncollected_levels(training, observed)

    assert [level for _, level in levels] == [50, 60, 70, 80, 90, 100]
    assert first_crossing(levels, 80) == START + timedelta(hours=8)


def test_uncollected_crossing_is_none_when_it_never_gets_there():
    training = _buckets([0, 1, 2, 3])
    observed = _buckets([4, 5, 6], start=START + timedelta(hours=4))

    assert first_crossing(uncollected_levels(training, observed), 80) is None


def test_uncollected_crossing_of_an_already_critical_site_is_the_first_reading():
    training = _buckets([70, 80, 85])
    observed = _buckets([90, 95], start=START + timedelta(hours=3))

    assert first_crossing(uncollected_levels(training, observed), 80) == (
        START + timedelta(hours=3)
    )


def test_uncollected_levels_need_history_on_both_sides():
    assert uncollected_levels([], _buckets([10])) == []
    assert uncollected_levels(_buckets([10]), []) == []
    assert first_crossing([], 80) is None

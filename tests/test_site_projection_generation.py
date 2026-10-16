from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.services.site_projection import chronos
from app.services.site_projection.generation import hourly_generation
from app.services.site_projection.history import SiteLevelBucket
from app.services.site_projection.models import ForecastContext, ForecastRequest

START = datetime(2026, 1, 1, tzinfo=UTC)


def _buckets(levels, signal_at=()):
    return [
        SiteLevelBucket(
            site_id=1,
            timestamp=START + timedelta(hours=index),
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

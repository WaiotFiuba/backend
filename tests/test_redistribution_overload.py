from datetime import UTC, datetime, timedelta

import pytest

from app.services.map.redistribution_overload import (
    accumulated_since_collection,
    build_overload_profile,
)
from app.services.site_projection.history import SiteLevelBucket

START = datetime(2026, 1, 1, tzinfo=UTC)


def _series(levels: list[float], collected_at: frozenset[int] = frozenset()):
    return [
        SiteLevelBucket(
            site_id=1,
            timestamp=START + timedelta(hours=hour),
            level=level,
            avg_level=level,
            max_level=level,
            min_level=level,
            measurement_count=1,
            container_count=1,
            has_collection_signal=hour in collected_at,
        )
        for hour, level in enumerate(levels)
    ]


def test_accumulated_restarts_at_each_collection():
    # 2 contenedores: lo acumulado es el nivel x 2, y vuelve a empezar al vaciar.
    series = _series([10, 30, 50, 5, 25], collected_at=frozenset({3}))

    accumulated = accumulated_since_collection(series, containers=2)

    assert list(accumulated) == [20, 60, 100, 10, 50]


def test_accumulated_keeps_growing_while_the_site_is_full():
    # Sube 20 por hora hasta llenarse; lleno, se supone que siguió a ese ritmo.
    series = _series([40, 60, 80, 100, 100, 100])

    accumulated = accumulated_since_collection(series, containers=1)

    assert list(accumulated) == [40, 60, 80, 100, 120, 140]


def test_site_that_reaches_95_percent_needs_one_more_container():
    # 2 contenedores, suben 5 puntos por hora hasta 95% y pasa el camión.
    levels = [5.0 * hour for hour in range(1, 20)] + [0.0]
    profile = build_overload_profile(_series(levels, frozenset({19})), containers=2)

    current = profile.hours(2)
    assert current.critical == 4  # 80, 85, 90 y 95%
    assert current.overflow == 0
    assert profile.cost(3) == 0  # con uno más no llega al 80%


def test_irregular_truck_shows_up_as_overflow():
    # El camión no pasa: el sitio se llena y queda desbordado varias horas.
    levels = [10.0 * hour for hour in range(1, 11)] + [100.0] * 5
    profile = build_overload_profile(_series(levels), containers=1)

    hours = profile.hours(1)
    assert hours.overflow == 6  # el bucket que llega a 100 y los 5 siguientes
    assert hours.critical == 2  # 80 y 90%
    assert profile.cost(1) == pytest.approx(2 + 3 * 6)


def test_donor_with_two_containers_at_half_would_overflow_with_one():
    levels = [10.0, 20, 30, 40, 50, 0, 10, 20, 30, 40, 50]
    profile = build_overload_profile(_series(levels, frozenset({5})), containers=2)

    assert profile.cost(2) == 0
    # Con un solo contenedor la misma basura lo lleva al 100%.
    assert profile.hours(1).overflow == 2


def test_more_containers_never_increase_the_cost():
    levels = [12.0 * (hour % 12) for hour in range(72)]
    collected = frozenset(hour for hour in range(72) if hour % 12 == 0 and hour > 0)
    profile = build_overload_profile(_series(levels, collected), containers=1)

    costs = [profile.cost(c) for c in range(1, 6)]
    assert costs == sorted(costs, reverse=True)


def test_profile_needs_history_and_containers():
    assert build_overload_profile([], containers=2) is None
    assert build_overload_profile(_series([10, 20]), containers=0) is None
    with pytest.raises(ValueError):
        build_overload_profile(_series([10, 20]), containers=1).hours(0)


def test_array_version_matches_the_bucket_version():
    from app.services.map.redistribution_overload import (
        accumulated_from_levels,
        build_overload_profile_from_levels,
    )

    levels = [10.0, 40, 70, 100, 100, 100, 15, 35, 60, 48, 70]
    collected = frozenset({6})
    series = _series(levels, collected)

    by_buckets = accumulated_since_collection(series, containers=2)
    by_levels = accumulated_from_levels(
        levels, [hour in collected for hour in range(len(levels))], containers=2
    )

    assert list(by_levels) == list(by_buckets)
    assert build_overload_profile_from_levels([], [], containers=2) is None

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.services.map.whatif_allocation import (
    Reading,
    VirtualContainer,
    apply_readings,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _container(container_id, site, optimized_site=None, level=0.0, rate=None):
    return VirtualContainer(
        container_id=container_id,
        original_site_id=site,
        optimized_site_id=site if optimized_site is None else optimized_site,
        level=level,
        last_reading=START,
        virtual_updated_at=START,
        rate_ewma=rate,
    )


def _reading(container_id, before, after, hours=1, collected=False):
    return Reading(
        container_id=container_id,
        level_before=before,
        level_after=after,
        reading_date=START + hours * HOUR,
        collected=collected,
    )


def _receiver_site():
    """Sitio 1 con 5 contenedores (1-5) al 20%; el plan le lleva el 9, vacío."""
    containers = {i: _container(i, site=1, level=20) for i in range(1, 6)}
    containers[9] = _container(9, site=2, optimized_site=1, level=0)
    return containers


def test_receiver_shares_each_reading_with_the_arriving_container():
    containers = _receiver_site()

    changed = apply_readings([_reading(1, 20, 32)], containers)

    # Mide +12: él suma 12·5/6 = +10 y el que llega, 12/6 = +2.
    assert containers[1].level == pytest.approx(30)
    assert containers[9].level == pytest.approx(2)
    assert changed == {1, 9}
    assert containers[2].level == 20  # los demás no reportaron


def test_receiver_containers_end_equal_only_if_they_measured_the_same():
    containers = _receiver_site()

    apply_readings([_reading(i, 20, 32) for i in range(1, 6)], containers)

    # Los cinco suman +10 y el que llega 5 × 2 = +10.
    assert [containers[i].level for i in (1, 2, 3, 4, 5)] == pytest.approx([30] * 5)
    assert containers[9].level == pytest.approx(10)


def test_donor_reading_of_a_leaving_container_goes_to_the_ones_that_stay():
    # Sitio 2 con 5 contenedores; el plan se lleva el 4 y el 5 al sitio 3.
    containers = {
        1: _container(1, site=2, level=10),
        2: _container(2, site=2, level=10),
        3: _container(3, site=2, level=10),
        4: _container(4, site=2, optimized_site=3, level=0),
        5: _container(5, site=2, optimized_site=3, level=0),
    }

    apply_readings([_reading(4, 10, 19)], containers)

    # El sensor del 4 sigue en el sitio 2 y mide +9: cada uno de los 3 suma +3.
    assert [containers[i].level for i in (1, 2, 3)] == pytest.approx([13] * 3)
    assert containers[4].level == 0  # su virtual está en el sitio 3


def test_site_outside_the_plan_follows_the_real_levels():
    containers = {
        1: _container(1, site=7, level=40),
        2: _container(2, site=7, level=55),
    }

    apply_readings([_reading(1, 40, 47), _reading(2, 55, 51)], containers)

    assert containers[1].level == pytest.approx(47)
    assert containers[2].level == pytest.approx(51)


def test_virtual_containers_of_a_site_receive_exactly_what_was_measured():
    rng = random.Random(7)
    containers = {i: _container(i, site=1, level=10) for i in range(1, 5)}
    containers[5] = _container(5, site=1, optimized_site=2, level=0)  # se va
    containers[8] = _container(8, site=3, optimized_site=1, level=0)  # llega
    containers[9] = _container(9, site=3, optimized_site=1, level=0)  # llega
    before = {i: 10.0 for i in range(1, 6)}
    measured = 0.0
    readings = []
    for hour in range(1, 6):
        for sensor in range(1, 6):
            after = before[sensor] + rng.uniform(0, 6)
            readings.append(_reading(sensor, before[sensor], after, hours=hour))
            measured += after - before[sensor]
            before[sensor] = after

    initial = sum(containers[i].level for i in (1, 2, 3, 4, 8, 9))
    apply_readings(readings, containers)
    final = sum(containers[i].level for i in (1, 2, 3, 4, 8, 9))

    # Lo que suman los virtuales del sitio 1 es la generación medida en el sitio 1.
    assert final - initial == pytest.approx(measured)


def test_a_full_real_container_keeps_receiving_at_its_recent_rate():
    containers = _receiver_site()
    containers[1].level = 60
    containers[1].rate_ewma = 6.0

    # El real está lleno: el sensor mide +0 durante 3 horas.
    apply_readings([_reading(1, 100, 100, hours=3)], containers)

    # Se estiman 6 × 3 = 18 puntos: él suma 15 y el que llega 3.
    assert containers[1].level == pytest.approx(75)
    assert containers[9].level == pytest.approx(3)
    assert containers[1].rate_ewma == 6.0  # una lectura saturada no la cambia


def test_rate_follows_unsaturated_readings():
    containers = {1: _container(1, site=1, level=10, rate=4.0)}

    apply_readings([_reading(1, 10, 20, hours=1)], containers)

    # 0,3 × 10 + 0,7 × 4 = 5,8 puntos por hora.
    assert containers[1].rate_ewma == pytest.approx(5.8)


def test_collection_empties_the_site_and_the_containers_that_arrived():
    containers = _receiver_site()
    containers[1].level = 70
    containers[9].level = 35

    apply_readings([_reading(1, 80, 5, collected=True)], containers)

    assert containers[1].level == 5  # queda como el real después de vaciar
    assert containers[9].level == 0  # se vacía en la misma pasada


def test_collection_of_a_leaving_container_does_not_change_the_virtual_world():
    containers = {
        1: _container(1, site=2, level=40),
        2: _container(2, site=2, optimized_site=3, level=25),
    }

    changed = apply_readings([_reading(2, 90, 0, collected=True)], containers)

    assert changed == set()
    assert (containers[1].level, containers[2].level) == (40, 25)


def test_overflow_hours_count_the_time_at_full():
    containers = {1: _container(1, site=1, level=90)}

    # De 90 a 110 en una hora: llegó al 100% a mitad de camino.
    apply_readings([_reading(1, 90, 110)], containers)
    assert containers[1].level == 100
    assert containers[1].overflow_hours == pytest.approx(0.5)

    # Sigue lleno otra hora entera.
    apply_readings([_reading(1, 100, 100, hours=2)], containers)
    assert containers[1].overflow_hours == pytest.approx(1.5)


def test_real_and_virtual_overflow_use_the_same_rule():
    containers = _receiver_site()
    containers[1].level = 60
    containers[1].rate_ewma = 10.0

    # El real llega al 100% y queda lleno; el virtual reparte con el que llega.
    apply_readings(
        [_reading(1, 80, 100, hours=1), _reading(1, 100, 100, hours=2)], containers
    )

    assert containers[1].real_overflow_hours == pytest.approx(1.0)
    assert containers[1].overflow_hours == 0  # con el plan no llegó a llenarse
    assert containers[1].level == pytest.approx(60 + 20 * 5 / 6 + 10 * 5 / 6)


def test_sensor_noise_never_takes_a_level_below_zero():
    containers = {1: _container(1, site=1, level=1)}

    apply_readings([_reading(1, 3, 0)], containers)

    assert containers[1].level == 0


def test_readings_of_unknown_containers_are_ignored():
    containers = {1: _container(1, site=1, level=10)}

    assert apply_readings([_reading(42, 10, 30)], containers) == set()
    assert containers[1].level == 10

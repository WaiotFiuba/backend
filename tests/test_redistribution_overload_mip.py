from collections import Counter

import numpy as np

from app.services.map.redistribution_overload import OverloadProfile
from app.services.map.redistribution_overload_mip import (
    plan_overload_moves_mip,
    solve_overload_mip,
)
from app.services.map.redistribution_overload_planner import (
    SiteState,
    _planar_km,
    plan_overload_moves,
)

# ~0,009 grados de latitud son ~1 km.
KM = 0.009


def _site(site_id, km_north, containers, accumulated, max_containers=None):
    return SiteState(
        site_id=site_id,
        latitude=-34.60 + km_north * KM,
        longitude=-58.40,
        containers=containers,
        profile=OverloadProfile(np.array(accumulated, dtype=float), 1.0),
        max_containers=max_containers,
    )


def _pairs(moves):
    return [(m.donor_site_id, m.receiver_site_id) for m in moves]


# Las mismas historias que en test_redistribution_overload_planner.py:
# OVERLOADED con 1 contenedor desborda 6 horas y con 2 queda sin sobrecarga;
# IDLE con 3 contenedores llega a 90 (con 2, a 45%).
OVERLOADED = [20, 40, 60, 80, 100, 110, 120, 130, 140, 150]
IDLE = [10, 30, 50, 70, 90]


def test_moves_a_container_from_an_idle_site_to_an_overloaded_one():
    sites = [_site(1, 0, 1, OVERLOADED), _site(2, 0.5, 3, IDLE)]

    moves = plan_overload_moves_mip(sites, max_distance_km=2)

    assert _pairs(moves) == [(2, 1)]
    assert 0.4 < moves[0].distance_km < 0.6
    assert moves[0].overload_reduction == 19.0


def test_does_not_move_if_the_donor_would_get_worse_than_the_receiver_improves():
    sites = [_site(1, 0, 1, [40, 60, 80]), _site(2, 0.5, 2, [20, 40, 60, 80, 100, 100])]

    assert plan_overload_moves_mip(sites, max_distance_km=2) == []


def test_respects_the_maximum_distance():
    sites = [_site(1, 0, 1, OVERLOADED), _site(2, 3, 3, IDLE)]

    assert plan_overload_moves_mip(sites, max_distance_km=2) == []
    assert len(plan_overload_moves_mip(sites, max_distance_km=5)) == 1


def test_respects_the_physical_capacity_of_the_receiver():
    sites = [_site(1, 0, 1, OVERLOADED, max_containers=1), _site(2, 0.5, 3, IDLE)]

    assert plan_overload_moves_mip(sites, max_distance_km=2) == []


def test_a_site_with_one_container_never_donates():
    sites = [_site(1, 0, 1, OVERLOADED), _site(2, 0.5, 1, [5, 10])]

    assert plan_overload_moves_mip(sites, max_distance_km=2) == []


def test_prefers_the_closest_donor_when_the_gain_is_the_same():
    sites = [
        _site(1, 0, 1, OVERLOADED),
        _site(2, 1.5, 3, IDLE),
        _site(3, 0.3, 3, IDLE),
    ]

    assert [
        m.donor_site_id for m in plan_overload_moves_mip(sites, max_distance_km=2)
    ] == [3]


def test_a_receiver_can_get_several_containers_until_it_stops_improving():
    heavy = [50, 100, 150, 200, 230]
    sites = [_site(1, 0, 1, heavy), _site(2, 0.5, 4, IDLE)]

    assert _pairs(plan_overload_moves_mip(sites, max_distance_km=2)) == [(2, 1)] * 2


def test_never_relays_containers_through_a_site():
    # A (sitio 1) desborda; B (2), a 1,9 km, está apenas crítico con 2
    # contenedores; C (3), a 1,9 km de B y 3,8 de A, está vacío. El greedy hace
    # B -> A y después C -> B: el contenedor de C llega a A pasando por B.
    sites = [
        _site(1, 0, 1, OVERLOADED),
        _site(2, 1.9, 2, [40, 80, 120, 160, 170]),
        _site(3, 3.8, 3, IDLE),
    ]
    assert _pairs(plan_overload_moves(sites, max_distance_km=2)) == [
        (2, 1),
        (3, 2),
        (3, 2),
    ]

    assert _pairs(plan_overload_moves_mip(sites, max_distance_km=2)) == [(2, 1)]


def test_finds_the_best_combination_where_the_greedy_gets_stuck():
    # Dos donantes con un contenedor de sobra cada uno. El greedy le da a 1 el
    # más cercano (3), que era el único que llegaba a 2; el MIP le da a 1 el
    # otro (4) y atiende a los dos receptores.
    spare = [5, 10]
    sites = [
        _site(1, 0, 1, OVERLOADED),
        _site(2, 1.5, 1, [40, 60, 80, 100, 110]),
        _site(3, 0.5, 2, spare),
        _site(4, -1.0, 2, spare),
    ]
    assert _pairs(plan_overload_moves(sites, max_distance_km=2)) == [(3, 1)]

    assert sorted(_pairs(plan_overload_moves_mip(sites, max_distance_km=2))) == [
        (3, 2),
        (4, 1),
    ]


def test_the_move_cost_discards_moves_that_improve_little():
    # Mover el contenedor le ahorra al receptor 1 hora en zona crítica.
    sites = [_site(1, 0, 1, [40, 60, 80]), _site(2, 0.5, 3, IDLE)]

    assert len(plan_overload_moves_mip(sites, max_distance_km=2, move_cost=0)) == 1
    assert plan_overload_moves_mip(sites, max_distance_km=2, move_cost=2) == []


def test_moves_come_from_the_biggest_to_the_smallest_improvement():
    # El sitio 1 mejora 6 con el primer contenedor y 1 con el segundo; el 2
    # mejora 4 con uno. El donante (4 contenedores casi vacíos) cede los tres.
    sites = [
        _site(1, 0, 1, [50, 90, 130, 170]),
        _site(2, 0.5, 1, [40, 60, 80, 100]),
        _site(3, 0.3, 4, [5, 10]),
    ]

    moves = plan_overload_moves_mip(sites, max_distance_km=2)

    assert [(m.receiver_site_id, m.overload_reduction) for m in moves] == [
        (1, 6.0),
        (2, 4.0),
        (1, 1.0),
    ]


def test_reports_an_optimal_result_and_nothing_to_do_without_receivers():
    result = solve_overload_mip([_site(1, 0, 3, IDLE), _site(2, 0.5, 3, IDLE)], 2)

    assert result.moves == [] and result.optimal


def test_keeps_every_constraint_on_a_random_network():
    rng = np.random.default_rng(7)
    sites = []
    for site_id in range(80):
        containers = int(rng.integers(1, 5))
        peak = rng.uniform(30, 260) * containers
        history = np.sort(rng.uniform(0, peak, 48))
        capacity = None if site_id % 3 else containers + int(rng.integers(0, 3))
        sites.append(_site(site_id, rng.uniform(0, 4), containers, history, capacity))

    moves = plan_overload_moves_mip(sites, max_distance_km=1.5)

    assert moves
    received = Counter(m.receiver_site_id for m in moves)
    donated = Counter(m.donor_site_id for m in moves)
    assert not set(received) & set(donated)
    by_id = {site.site_id: site for site in sites}
    for site_id, count in donated.items():
        assert by_id[site_id].containers - count >= 1
    for site_id, count in received.items():
        limit = by_id[site_id].max_containers
        assert limit is None or by_id[site_id].containers + count <= limit
    coords = dict(zip(by_id, _planar_km(sites)))
    for move in moves:
        gap = coords[move.donor_site_id] - coords[move.receiver_site_id]
        assert np.hypot(*gap) <= 1.5
    assert sum(m.overload_reduction for m in moves) > 0

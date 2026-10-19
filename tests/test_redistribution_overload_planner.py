import numpy as np

from app.services.map.redistribution_overload import OverloadProfile
from app.services.map.redistribution_overload_planner import (
    SiteState,
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


# Historias acumuladas (en % de un contenedor) usadas en los tests:
# - OVERLOADED con 1 contenedor: 6 horas por encima de 100 -> desborda;
#   con 2 contenedores ninguna hora supera 160 -> sin sobrecarga.
OVERLOADED = [20, 40, 60, 80, 100, 110, 120, 130, 140, 150]
# - IDLE con 3 contenedores: llega a 90 -> con 2 llega a 45% (sin sobrecarga).
IDLE = [10, 30, 50, 70, 90]


def test_moves_a_container_from_an_idle_site_to_an_overloaded_one():
    sites = [_site(1, 0, 1, OVERLOADED), _site(2, 0.5, 3, IDLE)]

    moves = plan_overload_moves(sites, max_distance_km=2)

    assert [(m.donor_site_id, m.receiver_site_id) for m in moves] == [(2, 1)]
    assert 0.4 < moves[0].distance_km < 0.6


def test_does_not_move_if_the_donor_would_get_worse_than_the_receiver_improves():
    # Donante con 2 contenedores al 50%: con 1 pasaría 1 hora en crítico y 2
    # desbordado (costo 1 + 3 x 2 = 7).
    donor_history = [20, 40, 60, 80, 100, 100]
    # Receptor que apenas toca la zona crítica (1 hora, costo 1).
    receiver_history = [40, 60, 80]
    sites = [_site(1, 0, 1, receiver_history), _site(2, 0.5, 2, donor_history)]

    assert plan_overload_moves(sites, max_distance_km=2) == []


def test_respects_the_maximum_distance():
    sites = [_site(1, 0, 1, OVERLOADED), _site(2, 3, 3, IDLE)]  # a 3 km

    assert plan_overload_moves(sites, max_distance_km=2) == []
    assert len(plan_overload_moves(sites, max_distance_km=5)) == 1


def test_respects_the_physical_capacity_of_the_receiver():
    sites = [_site(1, 0, 1, OVERLOADED, max_containers=1), _site(2, 0.5, 3, IDLE)]

    assert plan_overload_moves(sites, max_distance_km=2) == []


def test_a_site_with_one_container_never_donates():
    sites = [_site(1, 0, 1, OVERLOADED), _site(2, 0.5, 1, [5, 10])]

    assert plan_overload_moves(sites, max_distance_km=2) == []


def test_prefers_the_closest_donor_when_the_gain_is_the_same():
    sites = [
        _site(1, 0, 1, OVERLOADED),
        _site(2, 1.5, 3, IDLE),
        _site(3, 0.3, 3, IDLE),
    ]

    moves = plan_overload_moves(sites, max_distance_km=2)

    assert [m.donor_site_id for m in moves] == [3]


def test_a_receiver_can_get_several_containers_until_it_stops_improving():
    # Con 1 contenedor desborda mucho; necesita 3 para quedar sin sobrecarga.
    heavy = [50, 100, 150, 200, 230]
    sites = [_site(1, 0, 1, heavy), _site(2, 0.5, 4, IDLE)]

    moves = plan_overload_moves(sites, max_distance_km=2)

    assert [(m.donor_site_id, m.receiver_site_id) for m in moves] == [(2, 1)] * 2
    assert sites[0].profile.cost(3) == 0


def test_the_move_cost_discards_moves_that_improve_little():
    # Mover el contenedor le ahorra al receptor 1 hora en zona crítica.
    sites = [_site(1, 0, 1, [40, 60, 80]), _site(2, 0.5, 3, IDLE)]

    assert len(plan_overload_moves(sites, max_distance_km=2, move_cost=0)) == 1
    assert plan_overload_moves(sites, max_distance_km=2, move_cost=2) == []


def test_no_moves_without_overloaded_sites_or_donors():
    assert plan_overload_moves([], max_distance_km=2) == []
    assert plan_overload_moves([_site(1, 0, 3, IDLE)], max_distance_km=2) == []

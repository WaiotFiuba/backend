"""Elección de movimientos con el modelo de sobrecarga, resuelta como MIP.

Misma entrada y salida que plan_overload_moves (greedy), pero en vez de elegir
los movimientos de a uno busca el conjunto que más reduce la sobrecarga total.

Modelo (flujo de costo mínimo):
- Variables: flow[d, r] contenedores de d a r; leaves[d][k] = 1 si sale el
  k-ésimo contenedor de d; arrives[r][k] = 1 si llega el k-ésimo a r; una
  binaria de papel por sitio que podría recibir y donar.
- Objetivo: minimizar move_cost por movimiento + lo que empeoran los
  donantes - lo que mejoran los receptores.
- Restricciones: balance de cada donante y receptor; ningún sitio queda en 0;
  capacidad de la cuadra; distancia máxima; sin relevos (recibe o dona, nunca
  las dos cosas).

Para que el modelo sea lineal, las mejoras sucesivas de un receptor tienen que
ser decrecientes y los empeoramientos de un donante crecientes: se usa la
envolvente del costo real (regresión isotónica). Con los papeles fijos queda un
problema de transporte cuya solución es entera, así que la binaria de papel es
la única variable entera.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import highspy
import numpy as np
from scipy.optimize import isotonic_regression
from scipy.spatial import cKDTree

from app.services.map.redistribution_overload import (
    DEFAULT_CRITICAL_LEVEL,
    DEFAULT_OVERFLOW_WEIGHT,
)
from app.services.map.redistribution_overload_planner import (
    PlannedMove,
    SiteState,
    _planar_km,
)

# Contenedores extra que se evalúan para un receptor sin capacidad conocida.
MAX_EXTRA_CONTAINERS = 20
DEFAULT_TIME_LIMIT_S = 300.0
# A igual mejora, el donante más cercano.
_DISTANCE_TIEBREAK = 1e-3
_MIN_GAIN = 1e-9

# Pares (índice del donante, índice del receptor) -> distancia en km.
Pairs = dict[tuple[int, int], float]


class OverloadMipError(RuntimeError):
    """El resolver no encontró ninguna solución (por ejemplo, por el límite de tiempo)."""


@dataclass(frozen=True)
class OverloadMipResult:
    moves: list[PlannedMove]
    optimal: bool  # False: se cortó por tiempo y es la mejor solución encontrada
    mip_gap: float | None


@dataclass(frozen=True)
class _MarginalEffects:
    # gains[i][k]: lo que mejora el sitio i con su (k+1)-ésimo contenedor extra.
    gains: dict[int, list[float]]
    # damages[i][k]: lo que empeora el sitio i al ceder su (k+1)-ésimo contenedor.
    damages: dict[int, list[float]]


def plan_overload_moves_mip(
    sites: list[SiteState],
    max_distance_km: float,
    move_cost: float = 0.0,
    critical_level: float = DEFAULT_CRITICAL_LEVEL,
    overflow_weight: float = DEFAULT_OVERFLOW_WEIGHT,
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
) -> list[PlannedMove]:
    """Como plan_overload_moves. `move_cost` va en las unidades de OverloadProfile.cost."""
    return solve_overload_mip(
        sites,
        max_distance_km,
        move_cost,
        critical_level,
        overflow_weight,
        time_limit_s,
    ).moves


def solve_overload_mip(
    sites: list[SiteState],
    max_distance_km: float,
    move_cost: float = 0.0,
    critical_level: float = DEFAULT_CRITICAL_LEVEL,
    overflow_weight: float = DEFAULT_OVERFLOW_WEIGHT,
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
) -> OverloadMipResult:
    """Movimientos de a un contenedor y estado del resolver."""
    nothing = OverloadMipResult(moves=[], optimal=True, mip_gap=0.0)

    effects = _marginal_effects(sites, move_cost, critical_level, overflow_weight)
    if not effects.gains or not effects.damages:
        return nothing

    pairs = _candidate_pairs(sites, effects, max_distance_km, move_cost)
    if not pairs:
        return nothing

    model, flow, has_roles = _build_model(pairs, effects, move_cost, time_limit_s)
    flows, optimal, mip_gap = _solve(model, flow, has_roles)
    moves = _to_moves(sites, pairs, flows, effects, critical_level, overflow_weight)
    return OverloadMipResult(moves=moves, optimal=optimal, mip_gap=mip_gap)


def _relief(
    site: SiteState,
    container_count: int,
    critical_level: float,
    overflow_weight: float,
) -> float:
    """Cuánto baja la sobrecarga del sitio al pasar de `container_count` a `container_count` + 1.

    Es lo que mejora al recibir ese contenedor, o lo que empeora al cederlo
    (pasando de `container_count` + 1 a `container_count`). En horas ponderadas
    de la ventana.
    """
    before = site.profile.cost(container_count, critical_level, overflow_weight)
    after = site.profile.cost(container_count + 1, critical_level, overflow_weight)
    return before - after


# ---------------------------------------------------------------------------
# 1. Mejoras y empeoramientos marginales de cada sitio
# ---------------------------------------------------------------------------


def _marginal_effects(
    sites: list[SiteState],
    move_cost: float,
    critical_level: float,
    overflow_weight: float,
) -> _MarginalEffects:
    """Cuánto mejora cada sitio por contenedor que recibe y cuánto empeora por cada uno que cede.

    Receptor: hasta su capacidad (o +MAX_EXTRA_CONTAINERS si no se conoce),
    sin las mejoras que no pagan el movimiento. Donante: solo con 2 o más
    contenedores, y puede ceder hasta quedar con 1.
    """
    gains: dict[int, list[float]] = {}
    damages: dict[int, list[float]] = {}
    for index, site in enumerate(sites):
        current_count = site.containers

        limit = site.max_containers
        if limit is None:
            limit = current_count + MAX_EXTRA_CONTAINERS
        extra = min(MAX_EXTRA_CONTAINERS, limit - current_count)
        increments = [
            _relief(site, current_count + k - 1, critical_level, overflow_weight)
            for k in range(1, extra + 1)
        ]
        # Envolvente: mejoras no crecientes ("0 y 10" pasa a "5 y 5").
        slopes = list(isotonic_regression(increments, increasing=False).x)
        # Saca del final los contenedores que mejoran menos de lo que cuesta moverlos.
        while slopes and slopes[-1] <= move_cost + _MIN_GAIN:
            slopes.pop()
        if slopes:
            gains[index] = slopes

        if current_count >= 2:
            increments = [
                _relief(site, current_count - k, critical_level, overflow_weight)
                for k in range(1, current_count)
            ]
            # Envolvente: empeoramientos no decrecientes ("6 y 1" pasa a "3,5 y 3,5").
            damages[index] = list(isotonic_regression(increments, increasing=True).x)
    return _MarginalEffects(gains=gains, damages=damages)


# ---------------------------------------------------------------------------
# 2. Pares donante-receptor que tiene sentido evaluar
# ---------------------------------------------------------------------------


def _candidate_pairs(
    sites: list[SiteState],
    effects: _MarginalEffects,
    max_distance_km: float,
    move_cost: float,
) -> Pairs:
    """Pares a <= max_distance_km en los que mover al menos un contenedor conviene."""
    coords = _planar_km(sites)
    receivers, donors = list(effects.gains), list(effects.damages)
    near = cKDTree(coords[receivers]).sparse_distance_matrix(
        cKDTree(coords[donors]), max_distance_km, output_type="ndarray"
    )
    # scipy llama i, j, v a las columnas: posición en `receivers`, en `donors` y km.
    nth_receiver, nth_donor, distance_km = near["i"], near["j"], near["v"]
    receiver_of = np.array(receivers)[nth_receiver]
    donor_of = np.array(donors)[nth_donor]
    first_gain = np.array([effects.gains[r][0] for r in receivers])[nth_receiver]
    first_damage = np.array([effects.damages[d][0] for d in donors])[nth_donor]

    # Las mejoras decrecen y los empeoramientos crecen: si el primer contenedor
    # no conviene, ninguno conviene.
    useful = (donor_of != receiver_of) & (
        first_damage + move_cost < first_gain - _MIN_GAIN
    )
    # (donante, receptor) -> km de los pares útiles; son posiciones en `sites`, no site_id.
    return dict(
        zip(
            zip(donor_of[useful].tolist(), receiver_of[useful].tolist()),
            distance_km[useful].tolist(),
        )
    )


# ---------------------------------------------------------------------------
# 3. Modelo
# ---------------------------------------------------------------------------


def _build_model(
    pairs: Pairs,
    effects: _MarginalEffects,
    move_cost: float,
    time_limit_s: float,
) -> tuple[highspy.Highs, dict[tuple[int, int], highspy.highs_var], bool]:
    """Arma el MIP. Devuelve el modelo, las variables de flujo y si tiene binarias.

    Args:
        pairs: (donante, receptor) -> distancia en km.
        effects: gains y damages de cada sitio.
        move_cost: costo de cada movimiento.
        time_limit_s: tiempo máximo del resolver, en segundos.
    """
    gains, damages = effects.gains, effects.damages
    model = highspy.Highs()
    model.silent()
    model.setOptionValue("time_limit", time_limit_s)

    # addVariables: ub = cota superior (la inferior es 0), obj = coeficiente en
    # el objetivo.
    # flow[d, r]: contenedores de d a r.
    flow = model.addVariables(
        list(pairs),
        ub={(d, r): min(len(damages[d]), len(gains[r])) for d, r in pairs},
        obj={pair: move_cost + _DISTANCE_TIEBREAK * km for pair, km in pairs.items()},
    )
    used_donors = sorted({d for d, _ in pairs})
    used_receivers = sorted({r for _, r in pairs})
    # leaves[d][k] = 1 si sale el k-ésimo contenedor de d; suma lo que empeora d.
    # Son c - 1 tramos: d nunca queda en 0.
    leaves = {
        d: model.addVariables(len(damages[d]), ub=1, obj=damages[d])
        for d in used_donors
    }
    # arrives[r][k] = 1 si llega el k-ésimo contenedor a r; resta lo que mejora r.
    # Son a lo sumo U tramos: r no supera su capacidad.
    arrives = {
        r: model.addVariables(len(gains[r]), ub=1, obj=[-g for g in gains[r]])
        for r in used_receivers
    }

    # Balance (qsum = suma): lo que sale de d por sus pares son sus tramos
    # leaves usados; lo que llega a r, sus tramos arrives usados.
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for d, r in pairs:
        outgoing[d].append(flow[d, r])
        incoming[r].append(flow[d, r])
    for d in used_donors:
        model.addConstr(model.qsum(outgoing[d]) == model.qsum(leaves[d]))
    for r in used_receivers:
        model.addConstr(model.qsum(incoming[r]) == model.qsum(arrives[r]))

    # Sin relevos: receives (0 o 1) = 1 recibe y no dona; 0 dona y no recibe.
    both = sorted(set(used_donors) & set(used_receivers))
    for site in both:
        receives = model.addBinary()
        model.addConstr(model.qsum(arrives[site]) <= len(gains[site]) * receives)
        model.addConstr(model.qsum(leaves[site]) <= len(damages[site]) * (1 - receives))

    model.setMinimize()
    return model, flow, bool(both)


# ---------------------------------------------------------------------------
# 4. Resolución
# ---------------------------------------------------------------------------


def _solve(
    model: highspy.Highs,
    flow: dict[tuple[int, int], highspy.highs_var],
    has_roles: bool,
) -> tuple[dict[tuple[int, int], float], bool, float | None]:
    """Corre HiGHS. Devuelve el flujo de cada par, si es óptimo y el gap."""
    model.run()
    status = model.getModelStatus()
    info = model.getInfo()
    if info.primal_solution_status != highspy.kSolutionStatusFeasible:
        raise OverloadMipError(model.modelStatusToString(status))
    optimal = status == highspy.HighsModelStatus.kOptimal
    # Sin binarias es un LP y HiGHS no informa gap.
    mip_gap = info.mip_gap if has_roles else 0.0
    return model.vals(flow), optimal, mip_gap


# ---------------------------------------------------------------------------
# 5. De flujos a movimientos de un contenedor
# ---------------------------------------------------------------------------


def _to_moves(
    sites: list[SiteState],
    pairs: Pairs,
    flows: dict[tuple[int, int], float],
    effects: _MarginalEffects,
    critical_level: float,
    overflow_weight: float,
) -> list[PlannedMove]:
    """Un movimiento por contenedor, los más convenientes primero.

    La mejora de cada uno se calcula con el costo real (no la envolvente),
    aplicándolos en orden. Como ningún sitio recibe y dona, la suma de las
    mejoras es la reducción total de la sobrecarga.
    """
    chosen = sorted(
        (pair for pair in pairs for _ in range(round(flows[pair]))),
        key=lambda pair: (
            effects.damages[pair[0]][0] - effects.gains[pair[1]][0],
            pairs[pair],
        ),
    )
    count = [site.containers for site in sites]
    moves: list[PlannedMove] = []
    for donor, receiver in chosen:
        donor_site, receiver_site = sites[donor], sites[receiver]
        relief = _relief(
            receiver_site, count[receiver], critical_level, overflow_weight
        )
        damage = _relief(donor_site, count[donor] - 1, critical_level, overflow_weight)
        count[donor] -= 1
        count[receiver] += 1
        moves.append(
            PlannedMove(
                donor_site_id=donor_site.site_id,
                receiver_site_id=receiver_site.site_id,
                distance_km=round(pairs[donor, receiver], 4),
                overload_reduction=round(relief - damage, 4),
            )
        )
    return moves

"""Redistribución de contenedores con el modelo de sobrecarga.

Lee de la base los sitios que pueden participar de un plan, arma su perfil de
sobrecarga a partir de la historia y los agrupa por tipo de residuo y de
contenedor: solo se mueven contenedores entre sitios compatibles.

La elección de movimientos es una estrategia: el greedy
(redistribution_overload_planner.py, algorithm="overload") o el MIP
(redistribution_overload_mip.py, algorithm="overload_mip"). Ver
redistribution_overload.py para el modelo.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.data_level import DataLevel
from app.models.map.optimization import RedistributionPlan as RedistributionPlanModel
from app.models.map.site import Site
from app.schemas.map.optimization import (
    OptimizationConfig,
    RedistributionMove,
    RedistributionPlanRead,
)
from app.services.map.redistribution_overload import (
    DEFAULT_CRITICAL_LEVEL,
    DEFAULT_OVERFLOW_WEIGHT,
    build_overload_profile_from_levels,
)
from app.services.map.redistribution_overload_mip import (
    OverloadMipError,
    plan_overload_moves_mip,
)
from app.services.map.redistribution_overload_planner import (
    PlannedMove,
    SiteState,
    plan_overload_moves,
)
from app.services.map.reference_time_service import get_reference_time
from app.services.map.site_capacity_service import get_site_capacity_service
from app.services.site_projection.history import (
    HourlySiteLevels,
    fetch_hourly_site_levels,
)

logger = logging.getLogger(__name__)

# Días de historia que se usan si la configuración no indica otra ventana.
DEFAULT_WINDOW_DAYS = 28
SERIES_INTERVAL_MINUTES = 60

# Sitios compatibles: mismo tipo de residuo y de contenedor.
CompatibilityGroup = tuple[int | None, int | None]


@dataclass(frozen=True)
class OverloadCandidate:
    state: SiteState
    name: str
    group: CompatibilityGroup
    # Nivel medio del sitio en la ventana; se usa para mostrar el impacto de
    # cada movimiento (nivel antes/después).
    average_level: float


async def load_overload_candidates(
    db: AsyncSession,
    window_days: int | None = None,
    critical_level: float = DEFAULT_CRITICAL_LEVEL,
    apply_capacity_constraints: bool = True,
) -> list[OverloadCandidate]:
    """Sitios que pueden recibir o donar contenedores, con su perfil de sobrecarga.

    Receptores posibles: sitios que pasaron el umbral crítico en la ventana.
    Donantes posibles: sitios con 2 o más contenedores. El resto no puede
    participar de ningún movimiento, así que no se carga su historia.
    """
    window_days = window_days or DEFAULT_WINDOW_DAYS
    reference_time = await get_reference_time(db)
    window_start = reference_time - timedelta(days=window_days)

    site_rows = (
        await db.execute(
            select(
                Site.id,
                Site.name,
                Site.latitude,
                Site.longitude,
                Site.waste_type_id,
                func.count(Container.id).label("containers"),
                func.min(Container.container_type_id).label("container_type_id"),
            )
            .join(
                Container,
                (Container.site_id == Site.id) & Container.deleted_at.is_(None),
            )
            .where(Site.deleted_at.is_(None))
            .group_by(Site.id)
        )
    ).all()

    reached_critical = set(
        (
            await db.scalars(
                select(Container.site_id)
                .join(DataLevel, DataLevel.container_id == Container.id)
                .where(
                    Container.deleted_at.is_(None),
                    DataLevel.reading_date >= window_start,
                    DataLevel.reading_date <= reference_time,
                    DataLevel.container_current_level >= critical_level,
                )
                .distinct()
            )
        ).all()
    )
    rows = [
        row for row in site_rows if row.id in reached_critical or row.containers >= 2
    ]
    if not rows:
        return []

    series = await fetch_hourly_site_levels(
        db,
        site_ids=[row.id for row in rows],
        lookback_days=window_days,
        end_time=reference_time,
    )
    # Ya se leyó todo: se termina la transacción para devolver la conexión al
    # pool mientras dura el cálculo (puede ser más de un minuto).
    await db.commit()
    # Armar los perfiles y calcular la capacidad es cálculo en Python: corre en
    # un thread para no frenar el resto del backend.
    return await asyncio.to_thread(
        _build_candidates, rows, series, apply_capacity_constraints
    )


def _build_candidates(
    rows: list,
    series: dict[int, HourlySiteLevels],
    apply_capacity_constraints: bool,
) -> list[OverloadCandidate]:
    capacity_service = get_site_capacity_service()

    candidates: list[OverloadCandidate] = []
    for row in rows:
        site_series = series.get(row.id)
        if site_series is None:
            continue  # sin historia no se puede estimar qué le pasaría
        profile = build_overload_profile_from_levels(
            site_series.levels,
            site_series.collected,
            row.containers,
            SERIES_INTERVAL_MINUTES,
        )
        if profile is None:
            continue
        max_containers = None
        if apply_capacity_constraints:
            max_containers, _ = capacity_service.evaluate_site(
                row.latitude, row.longitude, row.containers
            )
        candidates.append(
            OverloadCandidate(
                state=SiteState(
                    site_id=row.id,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    containers=row.containers,
                    profile=profile,
                    max_containers=max_containers,
                ),
                name=row.name,
                group=(row.waste_type_id, row.container_type_id),
                average_level=sum(site_series.levels) / len(site_series.levels),
            )
        )
    return candidates


def group_by_compatibility(
    candidates: list[OverloadCandidate],
) -> dict[CompatibilityGroup, list[OverloadCandidate]]:
    groups: dict[CompatibilityGroup, list[OverloadCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.group].append(candidate)
    return dict(groups)


class OverloadPlanner(Protocol):
    """Elige los movimientos dentro de un grupo de sitios compatibles.

    `move_cost` va en las unidades de OverloadProfile.cost (horas de la ventana).
    """

    def __call__(
        self, sites: list[SiteState], *, max_distance_km: float, move_cost: float
    ) -> list[PlannedMove]: ...


def _plan_mip_or_greedy(
    sites: list[SiteState], *, max_distance_km: float, move_cost: float
) -> list[PlannedMove]:
    """El MIP; si no encuentra ninguna solución (por ejemplo, por tiempo), el greedy."""
    try:
        return plan_overload_moves_mip(
            sites, max_distance_km=max_distance_km, move_cost=move_cost
        )
    except OverloadMipError as error:
        logger.warning("MIP de sobrecarga sin solución (%s): se usa el greedy.", error)
        return plan_overload_moves(
            sites, max_distance_km=max_distance_km, move_cost=move_cost
        )


PLANNERS: dict[str, OverloadPlanner] = {
    "overload": plan_overload_moves,
    "overload_mip": _plan_mip_or_greedy,
}


async def generate_overload_plan(
    db: AsyncSession,
    config: OptimizationConfig,
    user_id: int | None = None,
) -> RedistributionPlanRead:
    """Genera y guarda un plan con el modelo de sobrecarga, sin activarlo."""
    from app.services.map.optimization_service import _plan_model_to_read

    plan_model = await build_overload_plan(db, config, user_id)
    db.add(plan_model)
    await db.commit()
    await db.refresh(plan_model)
    return _plan_model_to_read(plan_model)


async def build_overload_plan(
    db: AsyncSession,
    config: OptimizationConfig,
    user_id: int | None = None,
) -> RedistributionPlanModel:
    """Calcula un plan con el modelo de sobrecarga, sin guardarlo.

    El plan tiene el mismo formato que los del modelo por score, así que el
    what-if y el frontend lo usan igual.
    """
    candidates = await load_overload_candidates(
        db,
        window_days=config.window_days,
        apply_capacity_constraints=config.apply_capacity_constraints,
    )
    # El costo por movimiento llega en h/semana; los perfiles cuentan horas de la ventana.
    window_days = config.window_days or DEFAULT_WINDOW_DAYS
    move_cost = config.move_cost_hours_per_week * window_days / 7
    planned = await asyncio.to_thread(
        _plan_by_group,
        candidates,
        PLANNERS[config.algorithm],
        config.max_distance_km,
        move_cost,
    )

    by_site = {candidate.state.site_id: candidate for candidate in candidates}
    moves = await _assign_containers(db, planned, by_site)
    final_counts = {site_id: c.state.containers for site_id, c in by_site.items()}
    for move in moves:
        final_counts[move.from_site_id] -= 1
        final_counts[move.to_site_id] += 1

    return RedistributionPlanModel(
        status="draft",
        config=config.model_dump(),
        moves=[move.model_dump() for move in moves],
        summary=_summary(moves, candidates, final_counts),
        metrics_snapshot={"candidate_sites": len(candidates)},
        created_by=user_id,
    )


def _plan_by_group(
    candidates: list[OverloadCandidate],
    planner: OverloadPlanner,
    max_distance_km: float,
    move_cost: float,
) -> list[PlannedMove]:
    """Corre el planificador dentro de cada grupo de sitios compatibles."""
    planned: list[PlannedMove] = []
    for group in group_by_compatibility(candidates).values():
        planned.extend(
            planner(
                [candidate.state for candidate in group],
                max_distance_km=max_distance_km,
                move_cost=move_cost,
            )
        )
    return planned


async def _assign_containers(
    db: AsyncSession,
    planned: list[PlannedMove],
    by_site: dict[int, OverloadCandidate],
) -> list[RedistributionMove]:
    """Elige un contenedor concreto para cada movimiento y calcula su impacto.

    Del donante se mueve primero el contenedor menos lleno. El nivel antes y
    después de cada movimiento es acumulado: si un donante cede 3 contenedores,
    cada movimiento parte de cómo quedó el sitio después del anterior.
    """
    donor_ids = {move.donor_site_id for move in planned}
    pools: dict[int, deque[int]] = defaultdict(deque)
    if donor_ids:
        rows = await db.execute(
            select(Container.id, Container.site_id)
            .where(Container.site_id.in_(donor_ids), Container.deleted_at.is_(None))
            .order_by(Container.current_level.asc(), Container.id)
        )
        for container_id, site_id in rows.all():
            pools[site_id].append(container_id)

    counts = {site_id: c.state.containers for site_id, c in by_site.items()}
    moves: list[RedistributionMove] = []
    for planned_move in planned:
        donor = by_site[planned_move.donor_site_id]
        receiver = by_site[planned_move.receiver_site_id]
        donor_id, receiver_id = donor.state.site_id, receiver.state.site_id

        donor_before = _level_with(donor, counts[donor_id])
        receiver_before = _level_with(receiver, counts[receiver_id])
        counts[donor_id] -= 1
        counts[receiver_id] += 1
        donor_after = _level_with(donor, counts[donor_id])
        receiver_after = _level_with(receiver, counts[receiver_id])

        moves.append(
            RedistributionMove(
                container_id=pools[donor_id].popleft(),
                from_site_id=donor_id,
                from_site_name=donor.name,
                to_site_id=receiver_id,
                to_site_name=receiver.name,
                distance_km=planned_move.distance_km,
                from_lat=round(donor.state.latitude, 6),
                from_lng=round(donor.state.longitude, 6),
                to_lat=round(receiver.state.latitude, 6),
                to_lng=round(receiver.state.longitude, 6),
                donor_fill_before=donor_before,
                donor_fill_after=donor_after,
                receiver_fill_before=receiver_before,
                receiver_fill_after=receiver_after,
                net_decongestion_pct=round(
                    (receiver_after - receiver_before) + (donor_after - donor_before),
                    1,
                ),
                overload_reduction=planned_move.overload_reduction,
            )
        )
    return moves


def _level_with(candidate: OverloadCandidate, containers: int) -> float:
    """Nivel medio del sitio si tuviera `containers` (la basura se reparte igual)."""
    level = candidate.average_level * candidate.state.containers / containers
    return round(min(100.0, level), 1)


def _summary(
    moves: list[RedistributionMove],
    candidates: list[OverloadCandidate],
    final_counts: dict[int, int],
) -> dict:
    """Resumen del plan sobre los sitios candidatos (los únicos que pueden cambiar)."""
    critical_before = critical_after = overflow_before = overflow_after = 0.0
    for candidate in candidates:
        state = candidate.state
        before = state.profile.hours(state.containers)
        after = state.profile.hours(final_counts[state.site_id])
        critical_before += before.critical
        critical_after += after.critical
        overflow_before += before.overflow
        overflow_after += after.overflow
    cost_before = critical_before + DEFAULT_OVERFLOW_WEIGHT * overflow_before
    cost_after = critical_after + DEFAULT_OVERFLOW_WEIGHT * overflow_after

    original_mean, original_std = _weighted_level_stats(
        [(c.state.containers, _level_with(c, c.state.containers)) for c in candidates]
    )
    optimized_mean, optimized_std = _weighted_level_stats(
        [
            (
                final_counts[c.state.site_id],
                _level_with(c, final_counts[c.state.site_id]),
            )
            for c in candidates
        ]
    )
    distances = [move.distance_km for move in moves]
    return {
        "total_containers_moved": len(moves),
        "sites_emptied": 0,  # ningún sitio queda con 0 contenedores
        "sites_receiving": len({move.to_site_id for move in moves}),
        "sites_donating": len({move.from_site_id for move in moves}),
        "avg_distance_km": round(sum(distances) / len(distances), 4) if moves else 0.0,
        # Reducción relativa de la sobrecarga total de los candidatos.
        "expected_improvement": (
            round((cost_before - cost_after) / cost_before, 4) if cost_before else 0.0
        ),
        "original_mean_fill": original_mean,
        "original_std_fill": original_std,
        "optimized_mean_fill": optimized_mean,
        "optimized_std_fill": optimized_std,
        "critical_hours_before": round(critical_before, 1),
        "critical_hours_after": round(critical_after, 1),
        "overflow_hours_before": round(overflow_before, 1),
        "overflow_hours_after": round(overflow_after, 1),
    }


def _weighted_level_stats(
    weighted_levels: list[tuple[int, float]],
) -> tuple[float, float]:
    """Media y desvío del nivel, ponderados por cantidad de contenedores."""
    total = sum(weight for weight, _ in weighted_levels)
    if total == 0:
        return 0.0, 0.0
    mean = sum(weight * level for weight, level in weighted_levels) / total
    variance = (
        sum(weight * (level - mean) ** 2 for weight, level in weighted_levels) / total
    )
    return round(mean, 2), round(math.sqrt(variance), 2)

"""
Servicio de optimización de distribución de contenedores.

Implementa el algoritmo Balanced Load Redistribution (BLR):
1. Calcula métricas de utilización por sitio desde data_level
2. Clasifica sitios en categorías (critical/high/normal/low/idle)
3. Genera plan de redistribución con programación lineal o greedy
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.data_level import DataLevel
from app.models.map.optimization import RedistributionPlan as RedistributionPlanModel
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.services.map.site_capacity_service import get_site_capacity_service
from app.schemas.map.optimization import (
    OptimizationConfig,
    OptimizationMetricsResponse,
    RedistributionMove,
    RedistributionPlanRead,
    SiteUtilizationMetric,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Categorías de utilización
# ---------------------------------------------------------------------------

CATEGORY_THRESHOLDS = [
    ("critical", 0.85),
    ("high", 0.65),
    ("normal", 0.35),
    ("low", 0.15),
]


def _classify(score: float) -> str:
    for category, threshold in CATEGORY_THRESHOLDS:
        if score >= threshold:
            return category
    return "idle"


# ---------------------------------------------------------------------------
# Distancia Haversine
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en km entre dos coordenadas usando la fórmula de Haversine."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Fase 1: Cálculo de métricas de utilización por sitio
# ---------------------------------------------------------------------------


async def compute_site_utilization_metrics(
    db: AsyncSession,
    config: OptimizationConfig,
) -> OptimizationMetricsResponse:
    """
    Calcula métricas de utilización para cada sitio basándose en el historial
    de data_level.
    """
    # Determinar ventana temporal
    window_end = datetime.now(UTC)
    window_start: datetime | None = None
    if config.window_days is not None:
        window_start = window_end - timedelta(days=config.window_days)

    # Query: métricas agregadas por sitio desde data_level
    filters = [
        DataLevel.container_current_level.is_not(None),
        DataLevel.site_id.is_not(None),
    ]
    if window_start is not None:
        filters.append(DataLevel.reading_date >= window_start)

    # Subquery de métricas por contenedor agregadas al nivel de data_level.site_id
    # Notar: data_level.site_id es string, Container.site_id es int
    metrics_stmt = (
        select(
            DataLevel.site_id.label("site_id_str"),
            func.round(
                cast(func.avg(DataLevel.container_current_level), Numeric), 2
            ).label("avg_fill"),
            func.round(
                cast(
                    func.avg(
                        case(
                            (DataLevel.container_current_level >= 80, 1.0),
                            else_=0.0,
                        )
                    ),
                    Numeric,
                ),
                4,
            ).label("peak_rate"),
            func.sum(
                case(
                    (DataLevel.container_current_level >= 100, 1),
                    else_=0,
                )
            ).label("overflow_count"),
            func.count(DataLevel.id).label("reading_count"),
        )
        .where(*filters)
        .group_by(DataLevel.site_id)
    )

    metrics_result = await db.execute(metrics_stmt)
    metrics_by_site: dict[int, dict] = {}
    for row in metrics_result.all():
        try:
            sid = int(row.site_id_str)
        except (TypeError, ValueError):
            continue
        metrics_by_site[sid] = {
            "avg_fill": float(row.avg_fill or 0),
            "peak_rate": float(row.peak_rate or 0),
            "overflow_count": int(row.overflow_count or 0),
            "reading_count": int(row.reading_count or 0),
        }

    # Cargar información de sitios con sus contenedores
    site_stmt = (
        select(
            Site.id,
            Site.name,
            Site.latitude,
            Site.longitude,
            Site.waste_type_id,
            WasteType.name.label("waste_type_name"),
            func.count(Container.id).label("container_count"),
            func.min(Container.container_type_id).label("container_type_id"),
            func.coalesce(
                func.round(cast(func.avg(Container.current_level), Numeric)), 0
            ).label("current_avg_level"),
        )
        .outerjoin(Container, Container.site_id == Site.id)
        .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
        .where(Site.deleted_at.is_(None))
        .group_by(Site.id, WasteType.name)
    )
    site_result = await db.execute(site_stmt)
    site_rows = site_result.all()

    # Buscar nombres de container_type para los IDs encontrados
    ct_ids = {row.container_type_id for row in site_rows if row.container_type_id}
    ct_names: dict[int, str] = {}
    if ct_ids:
        ct_result = await db.execute(
            select(ContainerType.id, ContainerType.name).where(
                ContainerType.id.in_(ct_ids)
            )
        )
        ct_names = {r.id: r.name for r in ct_result.all()}

    # Construir métricas por sitio
    site_metrics: list[SiteUtilizationMetric] = []
    category_counts: dict[str, int] = defaultdict(int)
    capacity_service = get_site_capacity_service()

    for row in site_rows:
        sid = row.id
        container_count = int(row.container_count or 0)
        if container_count == 0:
            continue

        dl_metrics = metrics_by_site.get(sid, {})
        avg_fill = dl_metrics.get("avg_fill", float(row.current_avg_level or 0))
        peak_rate = dl_metrics.get("peak_rate", 0.0)
        overflow_count = dl_metrics.get("overflow_count", 0)

        # Normalizar avg_fill a [0, 1]
        avg_fill_norm = min(avg_fill / 100.0, 1.0)

        # Normalizar overflow_frequency (cap a 50 eventos)
        overflow_norm = min(overflow_count / 50.0, 1.0)

        # Calcular utilization_score compuesto
        utilization_score = avg_fill_norm * 0.4 + peak_rate * 0.3 + overflow_norm * 0.3
        utilization_score = round(min(max(utilization_score, 0.0), 1.0), 4)

        category = _classify(utilization_score)
        category_counts[category] += 1

        max_cap, puede_ingresar = capacity_service.evaluate_site(
            row.latitude, row.longitude, container_count
        )

        site_metrics.append(
            SiteUtilizationMetric(
                site_id=sid,
                site_name=row.name,
                latitude=row.latitude,
                longitude=row.longitude,
                container_count=container_count,
                max_containers=max_cap,
                puede_ingresar=max_cap > container_count,
                waste_type_id=row.waste_type_id,
                waste_type_name=row.waste_type_name,
                container_type_id=row.container_type_id,
                container_type_name=ct_names.get(row.container_type_id),
                avg_fill_level=round(avg_fill, 2),
                peak_fill_rate=round(peak_rate, 4),
                overflow_frequency=overflow_count,
                time_to_full_hours=None,  # Requiere análisis temporal más complejo
                utilization_score=utilization_score,
                category=category,
            )
        )

    tot_containers = sum(s.container_count for s in site_metrics)
    if tot_containers > 0:
        mean_fill = (
            sum(s.container_count * s.avg_fill_level for s in site_metrics)
            / tot_containers
        )
        variance = (
            sum(
                s.container_count * ((s.avg_fill_level - mean_fill) ** 2)
                for s in site_metrics
            )
            / tot_containers
        )
        std_fill = math.sqrt(max(0.0, variance))
    else:
        mean_fill = 0.0
        std_fill = 0.0

    return OptimizationMetricsResponse(
        total_sites=len(site_metrics),
        total_containers=tot_containers,
        mean_fill_level=round(mean_fill, 2),
        std_fill_level=round(std_fill, 2),
        critical_count=category_counts.get("critical", 0),
        high_count=category_counts.get("high", 0),
        normal_count=category_counts.get("normal", 0),
        low_count=category_counts.get("low", 0),
        idle_count=category_counts.get("idle", 0),
        window_start=window_start,
        window_end=window_end,
        sites=site_metrics,
    )


# ---------------------------------------------------------------------------
# Fase 3: Generación del plan de redistribución
# ---------------------------------------------------------------------------


async def generate_redistribution_plan(
    db: AsyncSession,
    config: OptimizationConfig,
    user_id: int | None = None,
) -> RedistributionPlanRead:
    """
    Genera un plan de redistribución de contenedores basado en las métricas
    de utilización. Usa el algoritmo especificado en config (LP o greedy).
    """
    # 1. Calcular métricas
    metrics_response = await compute_site_utilization_metrics(db, config)
    metrics = metrics_response.sites

    # 2. Separar donantes y receptores
    donors = [m for m in metrics if m.utilization_score < config.min_utilization_donor]
    receivers = [
        m for m in metrics if m.utilization_score >= config.min_utilization_receiver
    ]

    if not donors or not receivers:
        plan_model = RedistributionPlanModel(
            status="draft",
            config=config.model_dump(),
            moves=[],
            summary={
                "total_containers_moved": 0,
                "sites_emptied": 0,
                "sites_receiving": 0,
                "sites_donating": 0,
                "avg_distance_km": 0.0,
                "expected_improvement": 0.0,
                "original_mean_fill": metrics_response.mean_fill_level,
                "original_std_fill": metrics_response.std_fill_level,
                "optimized_mean_fill": metrics_response.mean_fill_level,
                "optimized_std_fill": metrics_response.std_fill_level,
            },
            metrics_snapshot={
                "total_sites": metrics_response.total_sites,
                "critical_count": metrics_response.critical_count,
                "high_count": metrics_response.high_count,
            },
            created_by=user_id,
        )
        db.add(plan_model)
        await db.commit()
        await db.refresh(plan_model)

        return _plan_model_to_read(plan_model)

    # 3. Cargar contenedores de los sitios donantes para asignar movimientos concretos
    donor_site_ids = [d.site_id for d in donors]
    container_stmt = (
        select(
            Container.id,
            Container.site_id,
            Container.container_type_id,
        )
        .where(
            Container.site_id.in_(donor_site_ids),
            Container.deleted_at.is_(None),
        )
        .order_by(Container.current_level.asc())  # Mover primero los menos llenos
    )
    container_result = await db.execute(container_stmt)
    containers_by_site: dict[int, list[int]] = defaultdict(list)
    container_type_map: dict[int, int | None] = {}
    for c_row in container_result.all():
        containers_by_site[c_row.site_id].append(c_row.id)
        container_type_map[c_row.id] = c_row.container_type_id

    # Construir lookup de info de sitios
    site_info = {m.site_id: m for m in metrics}

    # 4. Calcular supply y demand
    # Criterio seguro para donantes:
    # - Un sitio debe conservar al menos 1 contenedor (no se vacían esquinas activas).
    # - El nivel de llenado proyectado tras donar no debe superar un umbral seguro (por defecto 60% o target+10%).
    # Esto previene que sitios normales (40% de llenado) donen la mitad de su capacidad y salten al 80%
    # (lo cual aumentaba la dispersión y el desvío estándar de la red).
    max_donor_fill = min(75.0, max(50.0, config.target_utilization * 100.0 + 10.0))

    supply: dict[int, int] = {}
    for d in donors:
        if d.container_count <= 1:
            continue
        min_needed = max(
            1,
            math.ceil(d.container_count * d.avg_fill_level / max_donor_fill),
        )
        can_donate = max(0, d.container_count - min_needed)
        if can_donate > 0:
            supply[d.site_id] = can_donate

    demand: dict[int, int] = {}
    for r in receivers:
        if r.container_count <= 0:
            continue
        needed = max(
            1,
            math.ceil(
                r.container_count
                * (r.utilization_score / config.target_utilization - 1)
            ),
        )
        if getattr(config, "apply_capacity_constraints", True):
            max_cap = getattr(r, "max_containers", 2)
            cupo_libre = max(0, max_cap - r.container_count)
            if cupo_libre <= 0:
                continue
            d = min(needed, cupo_libre)
        else:
            d = needed

        if d > 0:
            demand[r.site_id] = d

    # 5. Resolver con el algoritmo elegido
    if config.algorithm == "lp":
        moves = _solve_lp(
            donors,
            receivers,
            supply,
            demand,
            config,
            containers_by_site,
            container_type_map,
            site_info,
        )
    else:
        moves = _solve_greedy(
            donors,
            receivers,
            supply,
            demand,
            config,
            containers_by_site,
            container_type_map,
            site_info,
        )

    # 6. Calcular resumen
    total_moved = len(moves)
    donating_sites = len({m.from_site_id for m in moves})
    receiving_sites = len({m.to_site_id for m in moves})

    # Sitios vaciados: sitios donantes que cedieron todos sus contenedores
    emptied_sites = 0
    containers_donated: dict[int, int] = defaultdict(int)
    for m in moves:
        containers_donated[m.from_site_id] += 1
    for site_id, donated in containers_donated.items():
        info = site_info.get(site_id)
        if info and donated >= info.container_count:
            emptied_sites += 1

    avg_dist = (
        sum(m.distance_km for m in moves) / total_moved if total_moved > 0 else 0.0
    )

    # Mejora esperada: reducción relativa del score promedio de receptores
    receiver_avg_score = (
        sum(r.utilization_score for r in receivers) / len(receivers)
        if receivers
        else 0.0
    )
    expected_improvement = round(
        min(receiver_avg_score * (total_moved / max(sum(demand.values()), 1)), 1.0), 4
    )

    # Métricas de llenado original vs optimizado
    tot_containers = sum(s.container_count for s in metrics)
    if tot_containers > 0:
        orig_mean = (
            sum(s.container_count * s.avg_fill_level for s in metrics) / tot_containers
        )
        orig_var = (
            sum(
                s.container_count * ((s.avg_fill_level - orig_mean) ** 2)
                for s in metrics
            )
            / tot_containers
        )
        orig_std = math.sqrt(max(0.0, orig_var))
    else:
        orig_mean = 0.0
        orig_std = 0.0

    containers_received: dict[int, int] = defaultdict(int)
    for m in moves:
        containers_received[m.to_site_id] += 1

    opt_container_weights: list[tuple[int, float]] = []
    opt_weighted_sum = 0.0
    for s in metrics:
        c_orig = s.container_count
        donated = containers_donated.get(s.site_id, 0)
        received = containers_received.get(s.site_id, 0)
        c_new = c_orig - donated + received
        if c_new > 0:
            new_level = min(100.0, max(0.0, (s.avg_fill_level * c_orig) / c_new))
            opt_container_weights.append((c_new, new_level))
            opt_weighted_sum += c_new * new_level

    tot_opt_containers = sum(count for count, _ in opt_container_weights)
    if tot_opt_containers > 0:
        opt_mean = opt_weighted_sum / tot_opt_containers
        opt_var = (
            sum(count * ((lvl - opt_mean) ** 2) for count, lvl in opt_container_weights)
            / tot_opt_containers
        )
        opt_std = math.sqrt(max(0.0, opt_var))
    else:
        opt_mean = orig_mean
        opt_std = orig_std

    summary = {
        "total_containers_moved": total_moved,
        "sites_emptied": emptied_sites,
        "sites_receiving": receiving_sites,
        "sites_donating": donating_sites,
        "avg_distance_km": round(avg_dist, 4),
        "expected_improvement": expected_improvement,
        "original_mean_fill": round(orig_mean, 2),
        "original_std_fill": round(orig_std, 2),
        "optimized_mean_fill": round(opt_mean, 2),
        "optimized_std_fill": round(opt_std, 2),
    }

    # 7. Persistir el plan
    plan_model = RedistributionPlanModel(
        status="draft",
        config=config.model_dump(),
        moves=[m.model_dump() for m in moves],
        summary=summary,
        metrics_snapshot={
            "total_sites": metrics_response.total_sites,
            "critical_count": metrics_response.critical_count,
            "high_count": metrics_response.high_count,
            "normal_count": metrics_response.normal_count,
            "low_count": metrics_response.low_count,
            "idle_count": metrics_response.idle_count,
        },
        created_by=user_id,
    )
    db.add(plan_model)
    await db.commit()
    await db.refresh(plan_model)

    return _plan_model_to_read(plan_model)


async def get_plan_by_id(
    db: AsyncSession, plan_id: int
) -> RedistributionPlanRead | None:
    """Retorna un plan de redistribución por su ID."""
    plan = await db.get(RedistributionPlanModel, plan_id)
    if plan is None:
        return None
    return _plan_model_to_read(plan)


# ---------------------------------------------------------------------------
# Algoritmo Greedy
# ---------------------------------------------------------------------------


def _solve_greedy(
    donors: list[SiteUtilizationMetric],
    receivers: list[SiteUtilizationMetric],
    supply: dict[int, int],
    demand: dict[int, int],
    config: OptimizationConfig,
    containers_by_site: dict[int, list[int]],
    container_type_map: dict[int, int | None],
    site_info: dict[int, SiteUtilizationMetric],
) -> list[RedistributionMove]:
    """
    Algoritmo greedy: ordena receptores por score desc (más urgentes primero),
    para cada receptor busca el donante compatible más cercano con oferta
    disponible.
    """
    moves: list[RedistributionMove] = []
    remaining_supply = dict(supply)
    remaining_demand = dict(demand)

    # Ordenar receptores por urgencia (score más alto primero)
    sorted_receivers = sorted(
        receivers, key=lambda r: r.utilization_score, reverse=True
    )

    # Mantener lista mutable de contenedores disponibles por sitio
    available_containers: dict[int, list[int]] = {
        sid: list(cids) for sid, cids in containers_by_site.items()
    }

    for receiver in sorted_receivers:
        r_demand = remaining_demand.get(receiver.site_id, 0)
        if r_demand <= 0:
            continue

        # Buscar donantes compatibles ordenados por distancia
        compatible_donors = []
        for donor in donors:
            if remaining_supply.get(donor.site_id, 0) <= 0:
                continue
            # Verificar compatibilidad de tipo
            if (
                receiver.waste_type_id != donor.waste_type_id
                or receiver.container_type_id != donor.container_type_id
            ):
                continue
            dist = haversine_distance(
                donor.latitude,
                donor.longitude,
                receiver.latitude,
                receiver.longitude,
            )
            if dist > config.max_distance_km:
                continue
            compatible_donors.append((donor, dist))

        # Ordenar por distancia (más cercano primero)
        compatible_donors.sort(key=lambda x: x[1])

        for donor, dist in compatible_donors:
            if r_demand <= 0:
                break
            available = available_containers.get(donor.site_id, [])
            can_give = min(
                remaining_supply.get(donor.site_id, 0),
                r_demand,
                len(available),
            )
            for _ in range(can_give):
                if not available:
                    break
                container_id = available.pop(0)
                moves.append(
                    RedistributionMove(
                        container_id=container_id,
                        from_site_id=donor.site_id,
                        from_site_name=donor.site_name,
                        to_site_id=receiver.site_id,
                        to_site_name=receiver.site_name,
                        from_lat=round(donor.latitude, 6),
                        from_lng=round(donor.longitude, 6),
                        to_lat=round(receiver.latitude, 6),
                        to_lng=round(receiver.longitude, 6),
                        distance_km=round(dist, 4),
                    )
                )
                remaining_supply[donor.site_id] -= 1
                r_demand -= 1

        remaining_demand[receiver.site_id] = r_demand

    return moves


# ---------------------------------------------------------------------------
# Algoritmo LP (Programación Lineal)
# ---------------------------------------------------------------------------


def _solve_lp(
    donors: list[SiteUtilizationMetric],
    receivers: list[SiteUtilizationMetric],
    supply: dict[int, int],
    demand: dict[int, int],
    config: OptimizationConfig,
    containers_by_site: dict[int, list[int]],
    container_type_map: dict[int, int | None],
    site_info: dict[int, SiteUtilizationMetric],
) -> list[RedistributionMove]:
    """
    Resuelve el problema de transporte con programación lineal.
    Fallback a greedy si scipy no está disponible.
    """
    try:
        from scipy.optimize import linprog
        from scipy.sparse import coo_matrix
    except ImportError:
        logger.warning("scipy no disponible, usando algoritmo greedy como fallback.")
        return _solve_greedy(
            donors,
            receivers,
            supply,
            demand,
            config,
            containers_by_site,
            container_type_map,
            site_info,
        )

    # Filtrar solo donantes con oferta disponible y receptores con demanda activa
    active_donors = [d for d in donors if supply.get(d.site_id, 0) > 0]
    active_receivers = [r for r in receivers if demand.get(r.site_id, 0) > 0]

    if not active_donors or not active_receivers:
        return []

    # Construir pares (donor_idx, receiver_idx) factibles
    pairs: list[tuple[int, int, float]] = []
    for i, donor in enumerate(active_donors):
        for j, receiver in enumerate(active_receivers):
            # Compatibilidad de tipo
            if (
                receiver.waste_type_id != donor.waste_type_id
                or receiver.container_type_id != donor.container_type_id
            ):
                continue
            dist = haversine_distance(
                donor.latitude,
                donor.longitude,
                receiver.latitude,
                receiver.longitude,
            )
            if dist <= config.max_distance_km:
                pairs.append((i, j, dist))

    if not pairs:
        return []

    n_pairs = len(pairs)
    n_donors = len(active_donors)
    n_receivers = len(active_receivers)

    # Función objetivo:
    # Maximizar traslados a receptores urgentes minimizando la distancia recorrida:
    # min Σ (dist_ij - M * score_j) * x_ij
    # M es un factor de beneficio preponderante (M >> dist_max) para asegurar que satisfacer
    # la demanda de un receptor crítico siempre tiene prioridad sobre no mover contenedores.
    M = 1000.0
    c = [dist - M * active_receivers[rj].utilization_score for _, rj, dist in pairs]

    # Matriz dispersa de restricciones (A_ub @ x <= b_ub)
    # 1. Supply constraints: para cada donante i: Σ x_ij <= supply_i
    # 2. Demand constraints: para cada receptor j: Σ x_ij <= demand_j
    row_ind: list[int] = []
    col_ind: list[int] = []
    data: list[float] = []

    for k, (di, rj, _) in enumerate(pairs):
        # Donante di
        row_ind.append(di)
        col_ind.append(k)
        data.append(1.0)
        # Receptor rj
        row_ind.append(n_donors + rj)
        col_ind.append(k)
        data.append(1.0)

    A_ub = coo_matrix(
        (data, (row_ind, col_ind)),
        shape=(n_donors + n_receivers, n_pairs),
    ).tocsr()

    b_ub = [float(supply[d.site_id]) for d in active_donors] + [
        float(demand[r.site_id]) for r in active_receivers
    ]

    # Bounds: 0 <= x <= min(supply_i, demand_j)
    bounds = [
        (
            0,
            min(
                supply[active_donors[di].site_id],
                demand[active_receivers[rj].site_id],
            ),
        )
        for di, rj, _ in pairs
    ]

    # Resolver con HiGHS (integrality=1 para resolución entera rápida)
    result = linprog(
        c,
        A_ub=A_ub,
        b_ub=b_ub,
        bounds=bounds,
        method="highs",
        integrality=1,
    )

    if not result.success:
        logger.warning(
            "LP no convergió (status=%s): %s. Usando greedy.",
            result.status,
            result.message,
        )
        return _solve_greedy(
            donors,
            receivers,
            supply,
            demand,
            config,
            containers_by_site,
            container_type_map,
            site_info,
        )

    # Convertir solución LP en movimientos concretos
    moves: list[RedistributionMove] = []
    available_containers: dict[int, list[int]] = {
        sid: list(cids) for sid, cids in containers_by_site.items()
    }

    for k, (di, rj, dist) in enumerate(pairs):
        n_move = int(round(result.x[k]))
        if n_move <= 0:
            continue
        donor = active_donors[di]
        receiver = active_receivers[rj]
        available = available_containers.get(donor.site_id, [])
        for _ in range(min(n_move, len(available))):
            container_id = available.pop(0)
            moves.append(
                RedistributionMove(
                    container_id=container_id,
                    from_site_id=donor.site_id,
                    from_site_name=donor.site_name,
                    to_site_id=receiver.site_id,
                    to_site_name=receiver.site_name,
                    from_lat=round(donor.latitude, 6),
                    from_lng=round(donor.longitude, 6),
                    to_lat=round(receiver.latitude, 6),
                    to_lng=round(receiver.longitude, 6),
                    distance_km=round(dist, 4),
                )
            )

    return moves


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan_model_to_read(plan: RedistributionPlanModel) -> RedistributionPlanRead:
    """Convierte el modelo SQLAlchemy a schema Pydantic."""
    summary = plan.summary or {}
    moves_data = plan.moves or []
    return RedistributionPlanRead(
        id=plan.id,
        status=plan.status,
        config=OptimizationConfig(**plan.config),
        moves=[RedistributionMove(**m) for m in moves_data],
        total_containers_moved=summary.get("total_containers_moved", 0),
        sites_emptied=summary.get("sites_emptied", 0),
        sites_receiving=summary.get("sites_receiving", 0),
        sites_donating=summary.get("sites_donating", 0),
        avg_distance_km=summary.get("avg_distance_km", 0.0),
        expected_improvement=summary.get("expected_improvement", 0.0),
        original_mean_fill=summary.get("original_mean_fill", 0.0),
        original_std_fill=summary.get("original_std_fill", 0.0),
        optimized_mean_fill=summary.get("optimized_mean_fill", 0.0),
        optimized_std_fill=summary.get("optimized_std_fill", 0.0),
        created_at=plan.created_at,
        simulation_id=plan.simulation_id,
    )

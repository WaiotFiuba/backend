from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.thresholds import get_level_thresholds
from app.models.map.container import Container
from app.models.map.optimization import RedistributionPlan
from app.models.map.site import Site
from app.schemas.map.kpis import (
    NetworkKpiSummaryResponse,
    WhatIfKpiComparisonResponse,
)


def _category_count_columns(avg_level) -> list:
    """Conteo de sitios por categoría de nivel según los umbrales centralizados."""
    t = get_level_thresholds()
    return [
        func.coalesce(func.sum(case((avg_level >= t.critical, 1), else_=0)), 0).label(
            "critical_count"
        ),
        func.coalesce(
            func.sum(
                case(((avg_level >= t.high) & (avg_level < t.critical), 1), else_=0)
            ),
            0,
        ).label("high_count"),
        func.coalesce(
            func.sum(
                case(((avg_level >= t.normal) & (avg_level < t.high), 1), else_=0)
            ),
            0,
        ).label("normal_count"),
        func.coalesce(
            func.sum(case(((avg_level > 0) & (avg_level < t.normal), 1), else_=0)), 0
        ).label("low_count"),
        func.coalesce(func.sum(case((avg_level == 0, 1), else_=0)), 0).label(
            "idle_count"
        ),
    ]


def real_containers():
    """Contenedores activos de la red real, con su sitio y su nivel."""
    return select(
        Container.id.label("id"),
        Container.site_id.label("site_id"),
        Container.current_level.label("level"),
    ).where(Container.deleted_at.is_(None))


async def get_real_network_kpis(db: AsyncSession) -> NetworkKpiSummaryResponse:
    """
    Calcula los KPIs operacionales de toda la red a nivel Sitio.
    Agrupa los contenedores por sitio para obtener el nivel promedio por sitio,
    y luego agrega las métricas sobre el universo de sitios activos.
    """
    containers = real_containers().subquery("containers")
    return await _network_kpis(db, containers, is_whatif=False, plan_id=None)


async def get_whatif_network_kpis(
    db: AsyncSession, plan_id: int | None = None
) -> NetworkKpiSummaryResponse:
    """
    Calcula los KPIs operacionales de la red What-If simulada a nivel Sitio.
    Si plan_id es None, busca el plan con estado 'active_whatif'; si no hay
    ninguno, el what-if es la red real. Usa los mismos contenedores que el mapa
    what-if (whatif_containers).
    """
    from app.services.map.optimization_whatif_service import whatif_containers

    resolved_plan_id = plan_id
    if resolved_plan_id is None:
        active_plan_stmt = (
            select(RedistributionPlan.id)
            .where(RedistributionPlan.status == "active_whatif")
            .order_by(RedistributionPlan.id.desc())
            .limit(1)
        )
        resolved_plan_id = await db.scalar(active_plan_stmt)

    if resolved_plan_id is None:
        real = await get_real_network_kpis(db)
        return real.model_copy(update={"is_whatif": True, "plan_id": None})

    containers = whatif_containers(resolved_plan_id).subquery("containers")
    return await _network_kpis(db, containers, is_whatif=True, plan_id=resolved_plan_id)


async def _network_kpis(
    db: AsyncSession, containers, *, is_whatif: bool, plan_id: int | None
) -> NetworkKpiSummaryResponse:
    """KPIs de la red a partir de un conjunto de contenedores (id, site_id, level)."""
    site_levels_cte = _site_levels(containers)

    agg_stmt = select(
        func.count(site_levels_cte.c.site_id).label("total_sites"),
        func.coalesce(func.sum(site_levels_cte.c.container_count), 0).label(
            "total_containers"
        ),
        func.coalesce(func.avg(site_levels_cte.c.avg_level), 0.0).label(
            "mean_fill_level"
        ),
        func.coalesce(
            func.avg(site_levels_cte.c.avg_level * site_levels_cte.c.avg_level), 0.0
        ).label("mean_sq"),
        *_category_count_columns(site_levels_cte.c.avg_level),
    )

    row = (await db.execute(agg_stmt)).one()
    total_sites = int(row.total_sites or 0)
    total_containers = int(row.total_containers or 0)
    mean_fill = float(row.mean_fill_level or 0.0)
    mean_sq = float(row.mean_sq or 0.0)

    # Varianza algebraica Var(X) = E[X^2] - (E[X])^2
    variance = max(0.0, mean_sq - (mean_fill**2))
    std_fill = math.sqrt(variance)

    return NetworkKpiSummaryResponse(
        total_sites=total_sites,
        total_containers=total_containers,
        mean_fill_level=round(mean_fill, 2),
        std_fill_level=round(std_fill, 2),
        critical_count=int(row.critical_count or 0),
        high_count=int(row.high_count or 0),
        normal_count=int(row.normal_count or 0),
        low_count=int(row.low_count or 0),
        idle_count=int(row.idle_count or 0),
        is_whatif=is_whatif,
        plan_id=plan_id,
        updated_at=datetime.now(timezone.utc),
    )


def _site_levels(containers):
    """Nivel promedio de cada sitio activo, con los contenedores dados."""
    return (
        select(
            Site.id.label("site_id"),
            func.coalesce(func.avg(containers.c.level), 0.0).label("avg_level"),
            func.count(containers.c.id).label("container_count"),
        )
        .outerjoin(containers, containers.c.site_id == Site.id)
        .where(Site.deleted_at.is_(None))
        .group_by(Site.id)
        .cte("site_levels")
    )


async def count_critical_sites_and_full_containers(
    db: AsyncSession, containers
) -> tuple[int, int]:
    """Sitios críticos (la misma cuenta que los KPIs) y contenedores al 100 %."""
    thresholds = get_level_thresholds()
    site_levels = _site_levels(containers)
    critical = (
        select(func.count())
        .select_from(site_levels)
        .where(site_levels.c.avg_level >= thresholds.critical)
        .scalar_subquery()
    )
    full = (
        select(func.count())
        .select_from(containers)
        .where(containers.c.level >= thresholds.full)
        .scalar_subquery()
    )
    row = (
        await db.execute(select(critical.label("critical"), full.label("full")))
    ).one()
    return int(row.critical), int(row.full)


async def get_kpis_comparison(
    db: AsyncSession, plan_id: int | None = None
) -> WhatIfKpiComparisonResponse:
    """
    Retorna la comparativa ejecutiva instantánea entre la línea base real y el plan What-If.
    """
    target_plan: RedistributionPlan | None = None
    if plan_id is not None:
        target_plan = await db.get(RedistributionPlan, plan_id)
    else:
        active_plan_stmt = (
            select(RedistributionPlan)
            .where(RedistributionPlan.status == "active_whatif")
            .order_by(RedistributionPlan.id.desc())
            .limit(1)
        )
        target_plan = (await db.execute(active_plan_stmt)).scalar_one_or_none()

    if target_plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró un plan de redistribución para comparar.",
        )

    baseline = await get_real_network_kpis(db)
    whatif = await get_whatif_network_kpis(db, plan_id=target_plan.id)

    std_diff = baseline.std_fill_level - whatif.std_fill_level
    reduction_pct = (
        round((std_diff / baseline.std_fill_level) * 100, 2)
        if baseline.std_fill_level > 0
        else 0.0
    )
    critical_reduction = max(0, baseline.critical_count - whatif.critical_count)
    is_improved = std_diff > 0.05

    return WhatIfKpiComparisonResponse(
        plan_id=target_plan.id,
        plan_status=target_plan.status,
        baseline=baseline,
        whatif=whatif,
        std_reduction_pct=reduction_pct,
        critical_sites_reduction=critical_reduction,
        is_improved=is_improved,
    )

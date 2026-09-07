"""
Servicio What-If para simulación en paralelo de distribución optimizada.

Mantiene niveles virtuales de contenedores bajo el plan de redistribución
optimizado, permitiendo comparar la evolución real vs optimizada durante
una simulación del digital twin.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.optimization import (
    OptimizationWhatIfLevel,
    RedistributionPlan as RedistributionPlanModel,
)
from app.models.map.site import Site
from app.schemas.map.optimization import (
    RedistributionPlanRead,
    WhatIfComparisonResponse,
    WhatIfSiteComparison,
)
from app.services.map.optimization_service import _plan_model_to_read

logger = logging.getLogger(__name__)

# Caché en memoria del mapping optimizado para performance en el hot path
_active_plan_id: int | None = None
_optimized_mapping: dict[int, int | None] = {}  # container_id -> optimized_site_id
_virtual_levels: dict[int, int] = {}  # container_id -> virtual_level


async def activate_whatif(db: AsyncSession, plan_id: int) -> RedistributionPlanRead:
    """
    Activa el modo what-if para un plan de redistribución.
    Carga el mapping virtual y prepara la tabla de niveles.
    """
    global _active_plan_id, _optimized_mapping, _virtual_levels

    plan = await db.get(RedistributionPlanModel, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan de redistribución no encontrado.",
        )
    if plan.status == "active_whatif":
        return _plan_model_to_read(plan)

    # Desactivar any plan what-if previo
    await db.execute(
        update(RedistributionPlanModel)
        .where(RedistributionPlanModel.status == "active_whatif")
        .values(status="completed")
    )

    # Limpiar niveles virtuales previos
    await db.execute(delete(OptimizationWhatIfLevel))

    # Construir mapping de movimientos: container_id -> new_site_id
    moves = plan.moves or []
    move_mapping: dict[int, int] = {}
    for m in moves:
        move_mapping[m["container_id"]] = m["to_site_id"]

    # Cargar todos los contenedores activos
    containers = (
        await db.execute(
            select(Container.id, Container.site_id, Container.current_level).where(
                Container.deleted_at.is_(None)
            )
        )
    ).all()

    # Crear registros de niveles virtuales para todos los contenedores
    whatif_records = []
    for c in containers:
        optimized_site = move_mapping.get(c.id, c.site_id)
        whatif_records.append(
            OptimizationWhatIfLevel(
                plan_id=plan_id,
                container_id=c.id,
                original_site_id=c.site_id,
                optimized_site_id=optimized_site,
                virtual_level=c.current_level,
                last_reading=None,
            )
        )

    # Insertar en lotes
    BATCH = 2000
    for i in range(0, len(whatif_records), BATCH):
        db.add_all(whatif_records[i : i + BATCH])
        await db.flush()

    # Actualizar estado del plan
    plan.status = "active_whatif"
    await db.commit()

    # Cargar en caché
    _active_plan_id = plan_id
    _optimized_mapping = {c.id: move_mapping.get(c.id, c.site_id) for c in containers}
    _virtual_levels = {c.id: c.current_level for c in containers}

    logger.info(
        "What-if activado para plan %s con %d contenedores (%d movimientos).",
        plan_id,
        len(whatif_records),
        len(move_mapping),
    )

    return _plan_model_to_read(plan)


async def deactivate_whatif(db: AsyncSession, plan_id: int) -> RedistributionPlanRead:
    """Desactiva el modo what-if y limpia niveles virtuales."""
    global _active_plan_id, _optimized_mapping, _virtual_levels

    plan = await db.get(RedistributionPlanModel, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan de redistribución no encontrado.",
        )

    await db.execute(
        delete(OptimizationWhatIfLevel).where(
            OptimizationWhatIfLevel.plan_id == plan_id
        )
    )
    plan.status = "completed"
    await db.commit()

    if _active_plan_id == plan_id:
        _active_plan_id = None
        _optimized_mapping = {}
        _virtual_levels = {}

    logger.info("What-if desactivado para plan %s.", plan_id)
    return _plan_model_to_read(plan)


def is_whatif_active() -> bool:
    """Retorna True si hay un plan what-if activo en memoria."""
    return _active_plan_id is not None


def get_optimized_site_id(container_id: int) -> int | None:
    """Retorna el site_id optimizado para un contenedor (desde caché)."""
    return _optimized_mapping.get(container_id)


async def update_virtual_levels_batch(
    db: AsyncSession,
    updates: list[tuple[int, int]],  # (container_id, new_level)
) -> None:
    """
    Actualiza los niveles virtuales de múltiples contenedores.
    Se llama desde el ingest service después de procesar telemetría real.
    """
    if not _active_plan_id or not updates:
        return

    # Actualizar caché en memoria
    for container_id, new_level in updates:
        _virtual_levels[container_id] = new_level

    # Actualizar en DB por lotes usando raw SQL para performance
    try:
        conn = await db.connection()
        raw_conn = await conn.get_raw_connection()
        asyncpg_conn = getattr(raw_conn, "driver_connection", raw_conn)

        if hasattr(asyncpg_conn, "executemany"):
            await asyncpg_conn.executemany(
                """
                UPDATE optimization_whatif_levels
                SET virtual_level = $1, last_reading = NOW()
                WHERE plan_id = $2 AND container_id = $3
                """,
                [(level, _active_plan_id, cid) for cid, level in updates],
            )
        else:
            # Fallback SQLAlchemy
            for cid, level in updates:
                await db.execute(
                    update(OptimizationWhatIfLevel)
                    .where(
                        OptimizationWhatIfLevel.plan_id == _active_plan_id,
                        OptimizationWhatIfLevel.container_id == cid,
                    )
                    .values(virtual_level=level)
                )
    except Exception:
        logger.warning(
            "Error actualizando niveles virtuales, usando fallback.", exc_info=True
        )
        for cid, level in updates:
            await db.execute(
                update(OptimizationWhatIfLevel)
                .where(
                    OptimizationWhatIfLevel.plan_id == _active_plan_id,
                    OptimizationWhatIfLevel.container_id == cid,
                )
                .values(virtual_level=level)
            )


async def get_comparison_metrics(
    db: AsyncSession, plan_id: int
) -> WhatIfComparisonResponse:
    """
    Retorna comparación lado a lado de niveles reales vs optimizados
    por sitio. Agrupa los niveles virtuales por optimized_site_id.
    """
    plan = await db.get(RedistributionPlanModel, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan de redistribución no encontrado.",
        )

    # Niveles reales por sitio (desde containers)
    real_stmt = (
        select(
            Container.site_id,
            func.round(func.avg(Container.current_level)).label("avg_level"),
            func.count(Container.id).label("container_count"),
        )
        .where(Container.site_id.is_not(None), Container.deleted_at.is_(None))
        .group_by(Container.site_id)
    )
    real_result = await db.execute(real_stmt)
    real_by_site: dict[int, dict] = {}
    for r in real_result.all():
        real_by_site[r.site_id] = {
            "avg_level": float(r.avg_level or 0),
            "count": int(r.container_count),
        }

    # Niveles virtuales por optimized_site_id (desde whatif_levels)
    virtual_stmt = (
        select(
            OptimizationWhatIfLevel.optimized_site_id,
            func.round(func.avg(OptimizationWhatIfLevel.virtual_level)).label(
                "avg_level"
            ),
            func.count(OptimizationWhatIfLevel.container_id).label("container_count"),
        )
        .where(
            OptimizationWhatIfLevel.plan_id == plan_id,
            OptimizationWhatIfLevel.optimized_site_id.is_not(None),
        )
        .group_by(OptimizationWhatIfLevel.optimized_site_id)
    )
    virtual_result = await db.execute(virtual_stmt)
    virtual_by_site: dict[int, dict] = {}
    for v in virtual_result.all():
        virtual_by_site[v.optimized_site_id] = {
            "avg_level": float(v.avg_level or 0),
            "count": int(v.container_count),
        }

    # Cargar info de sitios
    all_site_ids = set(real_by_site.keys()) | set(virtual_by_site.keys())
    site_info: dict[int, dict] = {}
    if all_site_ids:
        site_rows = await db.execute(
            select(Site.id, Site.name, Site.latitude, Site.longitude).where(
                Site.id.in_(all_site_ids)
            )
        )
        for sr in site_rows.all():
            site_info[sr.id] = {
                "name": sr.name,
                "lat": sr.latitude,
                "lng": sr.longitude,
            }

    # Construir comparación
    comparisons: list[WhatIfSiteComparison] = []
    total_improved = 0
    total_worsened = 0
    real_levels_sum = 0.0
    opt_levels_sum = 0.0
    site_count = 0

    for sid in all_site_ids:
        info = site_info.get(sid, {"name": f"Site {sid}", "lat": 0, "lng": 0})
        real = real_by_site.get(sid, {"avg_level": 0.0, "count": 0})
        virtual = virtual_by_site.get(sid, {"avg_level": 0.0, "count": 0})

        real_avg = real["avg_level"]
        opt_avg = virtual["avg_level"]
        improvement = round(opt_avg - real_avg, 2)

        if improvement < -1:  # Mejoró (nivel más bajo = mejor)
            total_improved += 1
        elif improvement > 1:
            total_worsened += 1

        real_levels_sum += real_avg
        opt_levels_sum += opt_avg
        site_count += 1

        comparisons.append(
            WhatIfSiteComparison(
                site_id=sid,
                site_name=info["name"],
                latitude=info["lat"],
                longitude=info["lng"],
                real_avg_level=real_avg,
                optimized_avg_level=opt_avg,
                real_container_count=real["count"],
                optimized_container_count=virtual["count"],
                improvement_pct=improvement,
            )
        )

    # Ordenar por mayor mejora primero
    comparisons.sort(key=lambda c: c.improvement_pct)

    return WhatIfComparisonResponse(
        plan_id=plan_id,
        plan_status=plan.status,
        real_global_avg=round(real_levels_sum / max(site_count, 1), 2),
        optimized_global_avg=round(opt_levels_sum / max(site_count, 1), 2),
        sites=comparisons,
        total_sites_improved=total_improved,
        total_sites_worsened=total_worsened,
    )

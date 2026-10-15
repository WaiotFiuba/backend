"""
Servicio What-If para simulación en paralelo de distribución optimizada.

Mantiene niveles virtuales de contenedores bajo el plan de redistribución
optimizado, permitiendo comparar la evolución real vs optimizada durante
una simulación del digital twin.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import (
    BigInteger,
    bindparam,
    delete,
    func,
    insert,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.optimization import (
    OptimizationWhatIfLevel,
    RedistributionPlan as RedistributionPlanModel,
)
from app.models.map.site import Site
from app.schemas.map.optimization import (
    OptimizationConfig,
    RedistributionPlanRead,
    WhatIfComparisonResponse,
    WhatIfSiteComparison,
)
from app.services.map.optimization_service import (
    _plan_model_to_read,
    build_redistribution_plan,
)

logger = logging.getLogger(__name__)

# Clave del lock de Postgres que serializa la activación de planes.
_ACTIVATION_LOCK_KEY = 0x57484946  # "WHIF"


async def get_active_plan_id(db: AsyncSession) -> int | None:
    """Id del plan what-if activo, leído de la base (sobrevive a reinicios)."""
    return await db.scalar(
        select(RedistributionPlanModel.id)
        .where(RedistributionPlanModel.status == "active_whatif")
        .order_by(RedistributionPlanModel.id.desc())
        .limit(1)
    )


async def create_and_activate_plan(
    db: AsyncSession,
    config: OptimizationConfig,
    user_id: int | None = None,
) -> RedistributionPlanRead:
    """Genera un plan y lo deja como único plan activo, en una sola transacción.

    El cálculo (que puede tardar) se hace antes de tomar el lock; guardar,
    reemplazar el plan anterior y activar el nuevo es todo o nada.
    """
    plan = await build_redistribution_plan(db, config, user_id)
    await _lock_activation(db)
    db.add(plan)
    await db.flush()
    await _activate(db, plan)
    await db.commit()
    await db.refresh(plan)
    return _plan_model_to_read(plan)


async def activate_whatif(db: AsyncSession, plan_id: int) -> RedistributionPlanRead:
    """Deja un plan existente como único plan activo."""
    await _lock_activation(db)
    plan = await db.get(RedistributionPlanModel, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan de redistribución no encontrado.",
        )
    if plan.status != "active_whatif":
        await _activate(db, plan)
    await db.commit()
    return _plan_model_to_read(plan)


async def _lock_activation(db: AsyncSession) -> None:
    """Serializa las activaciones: dos pedidos a la vez no se pisan.

    El lock dura hasta el fin de la transacción. En SQLite (tests) no hace falta:
    las escrituras ya se serializan.
    """
    connection = await db.connection()
    if connection.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _ACTIVATION_LOCK_KEY},
        )


async def _activate(db: AsyncSession, plan: RedistributionPlanModel) -> None:
    """Deja `plan` como único activo y arma la foto inicial de niveles virtuales.

    No hace commit: corre dentro de la transacción de quien llama. Todo se hace
    en la base, sin traer los contenedores a Python.
    """
    await db.execute(
        update(RedistributionPlanModel)
        .where(
            RedistributionPlanModel.status == "active_whatif",
            RedistributionPlanModel.id != plan.id,
        )
        .values(status="completed")
    )
    # Hay un solo plan activo, así que solo se conservan sus niveles virtuales.
    await db.execute(delete(OptimizationWhatIfLevel))

    # Cada contenedor arranca en su sitio y con su nivel real.
    await db.execute(
        insert(OptimizationWhatIfLevel).from_select(
            [
                "plan_id",
                "container_id",
                "original_site_id",
                "optimized_site_id",
                "virtual_level",
                "last_reading",
            ],
            select(
                literal(plan.id, BigInteger),
                Container.id,
                Container.site_id,
                Container.site_id,
                Container.current_level,
                Container.last_reading,
            ).where(Container.deleted_at.is_(None)),
        )
    )
    # Los que el plan mueve van al sitio receptor y llegan vacíos.
    moves = plan.moves or []
    if moves:
        levels = OptimizationWhatIfLevel.__table__
        await db.execute(
            update(levels)
            .where(
                levels.c.plan_id == bindparam("p_plan"),
                levels.c.container_id == bindparam("p_container"),
            )
            .values(optimized_site_id=bindparam("p_site"), virtual_level=0.0),
            [
                {
                    "p_plan": plan.id,
                    "p_container": move["container_id"],
                    "p_site": move["to_site_id"],
                }
                for move in moves
            ],
        )

    plan.status = "active_whatif"
    await db.flush()
    logger.info(
        "What-if activado para el plan %s (%d movimientos).", plan.id, len(moves)
    )


async def deactivate_whatif(db: AsyncSession, plan_id: int) -> RedistributionPlanRead:
    """Desactiva el modo what-if y limpia niveles virtuales."""
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

    logger.info("What-if desactivado para plan %s.", plan_id)
    return _plan_model_to_read(plan)


async def get_active_plan(db: AsyncSession) -> RedistributionPlanRead | None:
    """Plan what-if activo, o None. Nunca crea un plan: sin plan, el what-if es la red real."""
    plan_id = await get_active_plan_id(db)
    if plan_id is None:
        return None
    return _plan_model_to_read(await db.get(RedistributionPlanModel, plan_id))


async def update_virtual_levels_batch(
    db: AsyncSession,
    updates: list[tuple[int, int]],  # (container_id, new_level)
) -> None:
    """
    Actualiza los niveles virtuales de múltiples contenedores.
    Se llama desde el ingest service después de procesar telemetría real.

    Usa staging table + bulk UPDATE para rendimiento óptimo (1 COPY + 1 UPDATE
    en vez de N UPDATEs individuales).
    """
    if not updates:
        return
    plan_id = await get_active_plan_id(db)
    if plan_id is None:
        return

    # Bulk update via staging table (fast path para asyncpg/PostgreSQL)
    try:
        conn = await db.connection()
        raw_conn = await conn.get_raw_connection()
        asyncpg_conn = getattr(raw_conn, "driver_connection", raw_conn)

        if hasattr(asyncpg_conn, "copy_records_to_table"):
            await asyncpg_conn.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS _tmp_whatif_updates (
                    container_id bigint,
                    virtual_level integer
                );
                TRUNCATE _tmp_whatif_updates;
                """
            )
            await asyncpg_conn.copy_records_to_table(
                "_tmp_whatif_updates",
                records=updates,
                columns=["container_id", "virtual_level"],
            )
            await asyncpg_conn.execute(
                """
                UPDATE optimization_whatif_levels AS w
                SET virtual_level = t.virtual_level,
                    last_reading = NOW()
                FROM _tmp_whatif_updates AS t
                WHERE w.plan_id = $1 AND w.container_id = t.container_id;
                """,
                plan_id,
            )
            return
    except Exception:
        logger.warning(
            "Error en bulk update de niveles virtuales, usando fallback.", exc_info=True
        )

    # Fallback SQLAlchemy (para SQLite u otros backends)
    for cid, level in updates:
        await db.execute(
            update(OptimizationWhatIfLevel)
            .where(
                OptimizationWhatIfLevel.plan_id == plan_id,
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

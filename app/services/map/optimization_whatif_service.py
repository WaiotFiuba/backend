"""
Servicio What-If para simulación en paralelo de distribución optimizada.

Mantiene niveles virtuales de contenedores bajo el plan de redistribución
optimizado, permitiendo comparar la evolución real vs optimizada durante
una simulación del digital twin.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import (
    BigInteger,
    any_,
    bindparam,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.optimization import (
    OptimizationWhatIfLevel,
    OptimizationWhatIfTotals,
    RedistributionPlan as RedistributionPlanModel,
)
from app.schemas.map.optimization import (
    OptimizationConfig,
    RedistributionPlanRead,
    WhatIfComparisonResponse,
    WhatIfMeasure,
)
from app.services.map.kpi_service import (
    count_critical_sites_and_full_containers,
    real_containers,
)
from app.services.map.optimization_service import (
    _plan_model_to_read,
    build_redistribution_plan,
)
from app.services.map.whatif_allocation import (
    Reading,
    VirtualContainer,
    apply_readings,
    as_utc,
)

logger = logging.getLogger(__name__)

# Como mucho un conteo de la red cada este intervalo (en el tiempo de las lecturas).
TRACKING_INTERVAL = timedelta(minutes=5)

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


def whatif_containers(plan_id: int, *columns):
    """Contenedores activos de la red what-if, con su sitio y su nivel con el plan.

    Es la única definición que usan el mapa, los KPIs y la comparación, así que
    siempre ven los mismos contenedores. Los que no tienen fila virtual (por
    ejemplo, agregados después de activar el plan) quedan en su sitio y con su
    nivel real. `columns` suma columnas de Container al select.
    """
    levels = OptimizationWhatIfLevel
    return (
        select(
            Container.id.label("id"),
            func.coalesce(levels.optimized_site_id, Container.site_id).label("site_id"),
            func.coalesce(levels.virtual_level, Container.current_level).label("level"),
            *columns,
        )
        .outerjoin(
            levels,
            (levels.container_id == Container.id) & (levels.plan_id == plan_id),
        )
        .where(Container.deleted_at.is_(None))
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


async def _lock_activation(db: AsyncSession, shared: bool = False) -> None:
    """Serializa las activaciones: dos pedidos a la vez no se pisan.

    Activar o desactivar toma el lock exclusivo. La ingesta lo toma compartido:
    varios lotes pueden aplicarse a la vez, pero nunca durante una activación,
    así que cada lote se aplica entero sobre el plan anterior o sobre el nuevo.
    El lock dura hasta el fin de la transacción.
    """
    connection = await db.connection()
    if connection.dialect.name == "postgresql":
        function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        await db.execute(
            text(f"SELECT {function}(:key)"), {"key": _ACTIVATION_LOCK_KEY}
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
                "virtual_updated_at",
            ],
            # El nivel virtual arranca igual al real, así que su fecha también:
            # si no, la primera hora de un contenedor lleno no se contaría como
            # desborde del lado virtual y sí del real.
            select(
                literal(plan.id, BigInteger),
                Container.id,
                Container.site_id,
                Container.site_id,
                Container.current_level,
                Container.last_reading,
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

    # Los promedios del plan arrancan de cero, como sus niveles virtuales.
    await db.execute(
        delete(OptimizationWhatIfTotals).where(
            OptimizationWhatIfTotals.plan_id == plan.id
        )
    )
    db.add(OptimizationWhatIfTotals(plan_id=plan.id))

    plan.status = "active_whatif"
    await db.flush()
    logger.info(
        "What-if activado para el plan %s (%d movimientos).", plan.id, len(moves)
    )


async def deactivate_whatif(db: AsyncSession, plan_id: int) -> RedistributionPlanRead:
    """Desactiva el modo what-if y limpia niveles virtuales."""
    await _lock_activation(db)
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


async def reset_virtual_levels(db: AsyncSession) -> None:
    """Reinicia los niveles virtuales del plan activo (decisión E).

    Se usa al iniciar una simulación: los contenedores reales vuelven a 0%, así
    que los virtuales también, y las horas desbordadas se cuentan de nuevo. El
    plan sigue activo con su distribución. No hace commit.
    """
    await _lock_activation(db)
    await db.execute(
        update(OptimizationWhatIfLevel).values(
            virtual_level=0.0,
            last_reading=None,
            virtual_updated_at=None,
            rate_ewma=None,
            overflow_hours=0.0,
            real_overflow_hours=0.0,
        )
    )
    await db.execute(
        update(OptimizationWhatIfTotals)
        .where(
            OptimizationWhatIfTotals.plan_id.in_(
                select(RedistributionPlanModel.id).where(
                    RedistributionPlanModel.status == "active_whatif"
                )
            )
        )
        .values(
            observed_hours=0.0,
            real_critical_site_hours=0.0,
            critical_site_hours=0.0,
            real_full_container_hours=0.0,
            full_container_hours=0.0,
            last_observed_at=None,
            last_real_critical_sites=None,
            last_critical_sites=None,
            last_real_full_containers=None,
            last_full_containers=None,
        )
    )


async def get_active_plan(db: AsyncSession) -> RedistributionPlanRead | None:
    """Plan what-if activo, o None. Nunca crea un plan: sin plan, el what-if es la red real."""
    plan_id = await get_active_plan_id(db)
    if plan_id is None:
        return None
    return _plan_model_to_read(await db.get(RedistributionPlanModel, plan_id))


async def apply_readings_to_whatif(
    db: AsyncSession,
    readings: list[Reading],
    site_by_container: dict[int, int | None],
) -> None:
    """Aplica un lote de lecturas reales al what-if del plan activo.

    Corre dentro de la transacción de la ingesta y no hace commit. Carga los
    contenedores virtuales de los sitios tocados, aplica la regla de reparto
    de whatif_allocation y guarda solo las filas que cambiaron.
    """
    if not readings:
        return
    await _lock_activation(db, shared=True)
    plan_id = await get_active_plan_id(db)
    if plan_id is None:
        return

    sites = {site_by_container.get(r.container_id) for r in readings} - {None}
    containers = await _load_virtual_containers(db, plan_id, sites)
    # Contenedores agregados después de activar el plan: arrancan en su sitio,
    # con su nivel real.
    for reading in readings:
        site = site_by_container.get(reading.container_id)
        if reading.container_id not in containers and site is not None:
            containers[reading.container_id] = VirtualContainer(
                container_id=reading.container_id,
                original_site_id=site,
                optimized_site_id=site,
                level=float(reading.level_before or 0),
                last_reading=None,
                virtual_updated_at=None,
            )

    changed = apply_readings(readings, containers)
    # Los sensores también cambian: última lectura, velocidad y horas reales.
    changed |= {r.container_id for r in readings if r.container_id in containers}
    await _save_virtual_containers(
        db, plan_id, [containers[container_id] for container_id in changed]
    )
    await _track_network(db, plan_id, max(r.reading_date for r in readings))


async def _track_network(db: AsyncSession, plan_id: int, now: datetime) -> None:
    """Acumula sitios críticos y contenedores al 100 %, real y con el plan.

    Cada conteo rige hasta el siguiente: se suma el conteo anterior por el tiempo
    transcurrido (en el tiempo de las lecturas, así funciona igual con el
    simulador acelerado). Como mucho se cuenta una vez cada TRACKING_INTERVAL,
    para que muchos lotes chicos no repitan la consulta sobre toda la red.
    """
    totals = await db.scalar(
        select(OptimizationWhatIfTotals)
        .where(OptimizationWhatIfTotals.plan_id == plan_id)
        .with_for_update()
    )
    if totals is None:  # plan activado antes de que existieran los acumulados
        totals = OptimizationWhatIfTotals(plan_id=plan_id)
        db.add(totals)
    last = as_utc(totals.last_observed_at)
    if last is not None and now - last < TRACKING_INTERVAL:
        return
    if last is not None:
        hours = (now - last).total_seconds() / 3600
        totals.observed_hours += hours
        totals.real_critical_site_hours += totals.last_real_critical_sites * hours
        totals.critical_site_hours += totals.last_critical_sites * hours
        totals.real_full_container_hours += totals.last_real_full_containers * hours
        totals.full_container_hours += totals.last_full_containers * hours

    real = await count_critical_sites_and_full_containers(
        db, real_containers().subquery("containers")
    )
    virtual = await count_critical_sites_and_full_containers(
        db, whatif_containers(plan_id).subquery("containers")
    )
    totals.last_real_critical_sites, totals.last_real_full_containers = real
    totals.last_critical_sites, totals.last_full_containers = virtual
    totals.last_observed_at = now
    await db.flush()


async def _load_virtual_containers(
    db: AsyncSession, plan_id: int, sites: set[int]
) -> dict[int, VirtualContainer]:
    """Contenedores virtuales que están o estaban en `sites`: se quedan, se van o llegan."""
    if not sites:
        return {}
    levels = OptimizationWhatIfLevel
    connection = await db.connection()
    if connection.dialect.name == "postgresql":
        # Un solo parámetro con la lista: un IN con miles de sitios superaría el
        # límite de parámetros por consulta.
        site_list = literal(sorted(sites), ARRAY(BigInteger))
        in_sites = or_(
            levels.original_site_id == any_(site_list),
            levels.optimized_site_id == any_(site_list),
        )
    else:
        in_sites = or_(
            levels.original_site_id.in_(sites), levels.optimized_site_id.in_(sites)
        )
    # Columnas sueltas y no objetos ORM: con miles de filas por lote es más liviano.
    rows = await db.execute(
        select(
            levels.container_id,
            levels.original_site_id,
            levels.optimized_site_id,
            levels.virtual_level,
            levels.last_reading,
            levels.virtual_updated_at,
            levels.rate_ewma,
            levels.overflow_hours,
            levels.real_overflow_hours,
        ).where(levels.plan_id == plan_id, in_sites)
    )
    return {
        row.container_id: VirtualContainer(
            container_id=row.container_id,
            original_site_id=row.original_site_id,
            optimized_site_id=row.optimized_site_id,
            level=float(row.virtual_level),
            last_reading=as_utc(row.last_reading),
            virtual_updated_at=as_utc(row.virtual_updated_at),
            rate_ewma=row.rate_ewma,
            overflow_hours=row.overflow_hours,
            real_overflow_hours=row.real_overflow_hours,
        )
        for row in rows
    }


_SAVED_COLUMNS = (
    "container_id",
    "original_site_id",
    "optimized_site_id",
    "virtual_level",
    "last_reading",
    "virtual_updated_at",
    "rate_ewma",
    "overflow_hours",
    "real_overflow_hours",
)
# Columnas que se actualizan si la fila ya existe (el sitio no cambia).
_UPDATED_COLUMNS = _SAVED_COLUMNS[3:]
# Filas por sentencia en el camino de SQLite: queda lejos del límite de parámetros.
_SQLITE_CHUNK = 500


async def _save_virtual_containers(
    db: AsyncSession, plan_id: int, containers: list[VirtualContainer]
) -> None:
    """Guarda los contenedores virtuales (inserta los nuevos, actualiza el resto)."""
    if not containers:
        return
    records = [
        (
            c.container_id,
            c.original_site_id,
            c.optimized_site_id,
            c.level,
            c.last_reading,
            c.virtual_updated_at,
            c.rate_ewma,
            c.overflow_hours,
            c.real_overflow_hours,
        )
        for c in containers
    ]
    connection = await db.connection()
    if connection.dialect.name == "postgresql":
        await _save_with_copy(connection, plan_id, records)
    else:
        await _save_with_upsert(db, plan_id, records)


async def _save_with_copy(connection, plan_id: int, records: list[tuple]) -> None:
    """Postgres: COPY a una tabla temporal y un solo INSERT ... ON CONFLICT.

    Es el mismo camino que usa la ingesta para los contenedores: con miles de
    filas por lote es varias veces más rápido que un INSERT con todos los valores.
    """
    raw = await connection.get_raw_connection()
    asyncpg_connection = getattr(raw, "driver_connection", raw)
    await asyncpg_connection.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS _tmp_whatif_levels (
            container_id bigint,
            original_site_id bigint,
            optimized_site_id bigint,
            virtual_level double precision,
            last_reading timestamptz,
            virtual_updated_at timestamptz,
            rate_ewma double precision,
            overflow_hours double precision,
            real_overflow_hours double precision
        );
        TRUNCATE _tmp_whatif_levels;
        """
    )
    await asyncpg_connection.copy_records_to_table(
        "_tmp_whatif_levels", records=records, columns=list(_SAVED_COLUMNS)
    )
    columns = ", ".join(_SAVED_COLUMNS)
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in _UPDATED_COLUMNS)
    await asyncpg_connection.execute(
        f"""
        INSERT INTO optimization_whatif_levels (plan_id, {columns})
        SELECT $1, {columns} FROM _tmp_whatif_levels
        ON CONFLICT (plan_id, container_id) DO UPDATE SET {updates}
        """,
        plan_id,
    )


async def _save_with_upsert(
    db: AsyncSession, plan_id: int, records: list[tuple]
) -> None:
    """SQLite (tests): INSERT ... ON CONFLICT por bloques."""
    table = OptimizationWhatIfLevel.__table__
    values = [
        {"plan_id": plan_id, **dict(zip(_SAVED_COLUMNS, record, strict=True))}
        for record in records
    ]
    for start in range(0, len(values), _SQLITE_CHUNK):
        statement = sqlite_insert(table).values(values[start : start + _SQLITE_CHUNK])
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.plan_id, table.c.container_id],
                set_={
                    column: statement.excluded[column] for column in _UPDATED_COLUMNS
                },
            )
        )


async def get_comparison_metrics(
    db: AsyncSession, plan_id: int
) -> WhatIfComparisonResponse:
    """Compara la red con el plan contra la red real.

    - Estimado al generar el plan: la dispersión del nivel entre sitios.
    - Medido desde la activación: sitios críticos y contenedores al 100 % en
      promedio (los acumula la ingesta) y horas de contenedores al 100 %.
    - Ahora: sitios críticos y contenedores al 100 % en este momento.

    Las horas al 100 % y los valores de ahora necesitan los niveles virtuales,
    que solo existen mientras el plan está activo.
    """
    plan = await db.get(RedistributionPlanModel, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan de redistribución no encontrado.",
        )

    summary = plan.summary or {}
    dispersion = None
    if summary.get("original_std_fill") is not None:
        dispersion = WhatIfMeasure(
            real=summary["original_std_fill"],
            with_plan=summary.get("optimized_std_fill", summary["original_std_fill"]),
        )

    totals = await db.get(OptimizationWhatIfTotals, plan_id)
    observed = totals.observed_hours if totals is not None else 0.0
    avg_critical = avg_full = None
    if observed > 0:
        avg_critical = WhatIfMeasure(
            real=round(totals.real_critical_site_hours / observed, 1),
            with_plan=round(totals.critical_site_hours / observed, 1),
        )
        avg_full = WhatIfMeasure(
            real=round(totals.real_full_container_hours / observed, 1),
            with_plan=round(totals.full_container_hours / observed, 1),
        )

    hours = critical_now = full_now = None
    if plan.status == "active_whatif":
        levels = OptimizationWhatIfLevel
        real_hours, plan_hours = (
            await db.execute(
                select(
                    func.coalesce(func.sum(levels.real_overflow_hours), 0.0),
                    func.coalesce(func.sum(levels.overflow_hours), 0.0),
                ).where(levels.plan_id == plan_id)
            )
        ).one()
        hours = WhatIfMeasure(real=round(real_hours, 1), with_plan=round(plan_hours, 1))
        real_now = await count_critical_sites_and_full_containers(
            db, real_containers().subquery("containers")
        )
        plan_now = await count_critical_sites_and_full_containers(
            db, whatif_containers(plan_id).subquery("containers")
        )
        critical_now = WhatIfMeasure(real=real_now[0], with_plan=plan_now[0])
        full_now = WhatIfMeasure(real=real_now[1], with_plan=plan_now[1])

    return WhatIfComparisonResponse(
        plan_id=plan.id,
        plan_status=plan.status,
        dispersion=dispersion,
        observed_hours=round(observed, 2),
        avg_critical_sites=avg_critical,
        avg_full_containers=avg_full,
        full_container_hours=hours,
        critical_sites_now=critical_now,
        full_containers_now=full_now,
    )

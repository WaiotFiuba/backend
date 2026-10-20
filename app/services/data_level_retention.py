"""Ventana de retención de data_level: se conservan las lecturas de los últimos
N períodos (períodos × contenedores filas, las de id más alto)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.map_database import MapSessionLocal
from app.models.map.container import Container
from app.models.map.data_level import DataLevel
from app.services.data_level_partitions import (
    day_of,
    drop_partitions,
    is_partitioned,
    lock_partitions,
)

logger = logging.getLogger(__name__)

PURGE_BATCH_ROWS = 50_000
PROJECTION_LOOKBACK_DAYS = 14
CHECK_INTERVAL_SECONDS = 600

_purge_lock = asyncio.Lock()
_last_check: float | None = None


async def purge_readings_up_to(
    db: AsyncSession,
    first_id: int,
    last_id: int,
    batch_rows: int = PURGE_BATCH_ROWS,
) -> int:
    deleted = 0
    for start in range(first_id, last_id + 1, batch_rows):
        end = min(start + batch_rows - 1, last_id)
        result = await db.execute(
            delete(DataLevel).where(DataLevel.id >= start, DataLevel.id <= end)
        )
        await db.commit()
        deleted += result.rowcount
    return deleted


async def purge_if_due(
    session_factory: Callable[[], AsyncSession] = MapSessionLocal,
    retention_periods: int | None = None,
    container_count: int | None = None,
) -> int | None:
    """Borra lo que queda fuera de la ventana cuando sobra al menos un período."""
    periods = (
        get_settings().data_level_retention_periods
        if retention_periods is None
        else retention_periods
    )
    if periods <= 0:
        return None

    async with _purge_lock, session_factory() as db:
        if container_count is None:
            container_count = await db.scalar(
                select(func.count())
                .select_from(Container)
                .where(Container.deleted_at.is_(None))
            )
        if not container_count:
            return None

        connection = await db.connection()
        if await is_partitioned(connection):
            await lock_partitions(connection)
        oldest_id, newest_id = (
            await db.execute(select(func.min(DataLevel.id), func.max(DataLevel.id)))
        ).one()
        if newest_id is None:
            return None
        last_id_to_delete = newest_id - periods * container_count
        if last_id_to_delete - oldest_id + 1 < container_count:
            return None

        started = time.perf_counter()
        deleted = await _drop_old_partitions(db, last_id_to_delete)
        if deleted is None:
            deleted = await purge_readings_up_to(db, oldest_id, last_id_to_delete)
        logger.debug(
            "Retencion de data_level: %s lecturas borradas en %.2fs.",
            deleted,
            time.perf_counter() - started,
        )
        await _check_window(db, periods, container_count)
        return deleted


async def _drop_old_partitions(db: AsyncSession, last_id_to_delete: int) -> int | None:
    """Con data_level particionada, borra las particiones de los días enteros que
    quedan fuera de la ventana. Devuelve cuántas borró, o None si no está particionada.

    Se conserva al menos la ventana: la partición del día en que empieza queda entera.
    """
    if not await is_partitioned(await db.connection()):
        return None
    first_kept = await db.scalar(
        select(DataLevel.reading_date)
        .where(DataLevel.id > last_id_to_delete)
        .order_by(DataLevel.id)
        .limit(1)
    )
    if first_kept is None:
        return 0
    dropped = await drop_partitions(db, before=day_of(first_kept))
    await db.commit()
    return dropped


async def _check_window(db: AsyncSession, periods: int, container_count: int) -> None:
    global _last_check
    now = time.monotonic()
    if _last_check is not None and now - _last_check < CHECK_INTERVAL_SECONDS:
        return
    _last_check = now

    oldest, newest = (
        await db.execute(
            select(func.min(DataLevel.reading_date), func.max(DataLevel.reading_date))
        )
    ).one()
    if oldest is None:
        return
    covered_days = (newest - oldest).total_seconds() / 86400
    logger.info(
        "Retencion de data_level: ventana de %s periodos x %s contenedores, "
        "cubre %.1f dias.",
        periods,
        container_count,
        covered_days,
    )
    if newest - oldest < timedelta(days=PROJECTION_LOOKBACK_DAYS):
        logger.warning(
            "La ventana de data_level cubre %.1f dias y la proyeccion mira %s. "
            "Subir DATA_LEVEL_RETENTION_PERIODS o usar ticks mas largos.",
            covered_days,
            PROJECTION_LOOKBACK_DAYS,
        )

    connection = await db.connection()
    if connection.dialect.name != "postgresql":
        return
    # Con data_level particionada, la tabla madre no guarda filas: se suman las particiones.
    size_bytes = await db.scalar(
        text(
            "SELECT pg_total_relation_size('data_level') + coalesce(("
            "SELECT sum(pg_total_relation_size(inhrelid)) FROM pg_inherits "
            "WHERE inhparent = 'data_level'::regclass), 0)"
        )
    )
    size_gb = (size_bytes or 0) / 1024**3
    warning_gb = get_settings().data_level_size_warning_gb
    if size_gb >= warning_gb:
        logger.warning(
            "data_level ocupa %.1f GB (umbral de aviso: %.0f GB). Revisar "
            "DATA_LEVEL_RETENTION_PERIODS.",
            size_gb,
            warning_gb,
        )
    else:
        logger.info("data_level ocupa %.1f GB.", size_gb)


async def purge_in_background() -> None:
    try:
        await purge_if_due()
    except DBAPIError:
        # Casi siempre el lock_timeout de lock_partitions: se reintenta con el
        # próximo lote de la ingesta.
        logger.warning(
            "No se pudo aplicar la retencion de data_level; se reintenta.",
            exc_info=True,
        )
    except Exception:
        logger.exception("No se pudo aplicar la retencion de data_level.")

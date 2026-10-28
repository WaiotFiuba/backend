"""Compacta las tablas que la ingesta reescribe en cada lectura.

Cada lectura actualiza la fila de su contenedor y la de su nivel what-if. Mientras
corre una consulta larga, Postgres no puede limpiar las versiones viejas y la tabla
crece; el autovacuum después libera ese espacio pero no achica el archivo. Cuando
una pasa de MAX_BYTES se reescribe con VACUUM FULL.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.map_database import ingest_engine

logger = logging.getLogger(__name__)

TABLES = ("containers", "optimization_whatif_levels")
# Sanas ocupan unos 15 MB con 32.000 contenedores.
MAX_BYTES = 128 * 1024**2
CHECK_INTERVAL_SECONDS = 60
_LOCK_TIMEOUT = "5s"

_compact_lock = asyncio.Lock()
_last_check: float | None = None


async def compact_if_bloated(engine: AsyncEngine = ingest_engine) -> list[str]:
    """Compacta las tablas de TABLES que superen MAX_BYTES. Devuelve cuáles compactó."""
    global _last_check
    now = time.monotonic()
    if _compact_lock.locked() or (
        _last_check is not None and now - _last_check < CHECK_INTERVAL_SECONDS
    ):
        return []

    async with _compact_lock, engine.connect() as connection:
        _last_check = now
        if connection.dialect.name != "postgresql":
            return []
        # VACUUM no puede correr dentro de una transacción.
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text(f"SET lock_timeout = '{_LOCK_TIMEOUT}'"))
        compacted = []
        try:
            for table in TABLES:
                size = await connection.scalar(
                    text("SELECT pg_total_relation_size(to_regclass(:table))"),
                    {"table": table},
                )
                if not size or size < MAX_BYTES:
                    continue
                started = time.perf_counter()
                await connection.execute(text(f"VACUUM (FULL, ANALYZE) {table}"))
                compacted.append(table)
                logger.info(
                    "%s compactada: ocupaba %.0f MB (%.1fs).",
                    table,
                    size / 1024**2,
                    time.perf_counter() - started,
                )
        finally:
            await connection.execute(text("RESET lock_timeout"))
        return compacted


async def compact_in_background() -> None:
    try:
        await compact_if_bloated()
    except DBAPIError:
        logger.warning(
            "No se pudo compactar las tablas de la ingesta; se reintenta.",
            exc_info=True,
        )
    except Exception:
        logger.exception("No se pudo compactar las tablas de la ingesta.")

"""Particiones diarias de data_level (solo Postgres).

data_level se particiona por día de reading_date: cada lote de la ingesta
inserta solo en la partición del día, cuyos índices son chicos y entran en
memoria. Sin particiones, los índices crecen con toda la historia y cada lote
toca miles de páginas dispersas, así que la ingesta se vuelve más lenta cuanto
más dura la simulación.

Las particiones se crean a demanda (antes de insertar) y se borran enteras al
iniciar una simulación o al aplicar la retención. Si la base no es Postgres o
data_level no está particionada, todas las funciones no hacen nada.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

logger = logging.getLogger(__name__)

PARENT_TABLE = "data_level"
PARTITION_PREFIX = "data_level_p"
_NAME_PATTERN = re.compile(rf"^{PARTITION_PREFIX}(\d{{8}})$")
# Clave del advisory lock que serializa la creación y el borrado de particiones.
_PARTITION_LOCK_KEY = 0x44415441  # "DATA"
# Crear una partición toma un lock exclusivo sobre data_level: si una consulta
# larga lo retiene, se desiste y se reintenta en el próximo lote.
_LOCK_TIMEOUT = "5s"
# Días que se crean por adelantado. A 1000x un día simulado son 86 s reales:
# con 7 días hay ~10 min de margen si una consulta larga traba la creación.
DAYS_AHEAD = 7

# Días con partición ya creada, para no consultar el catálogo en cada lote.
_known_days: set[date] = set()
# Si data_level está particionada (None: todavía no se consultó). Cambia solo con
# una migración, que corre al arrancar la API.
_partitioned: bool | None = None


def partition_name(day: date) -> str:
    return f"{PARTITION_PREFIX}{day:%Y%m%d}"


def day_of(value: datetime) -> date:
    """Día UTC de una fecha (las fechas sin zona se toman como UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Rango de la partición de un día: [00:00 UTC, 00:00 UTC del día siguiente)."""
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def day_from_name(name: str) -> date | None:
    match = _NAME_PATTERN.match(name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d").date()


async def is_partitioned(connection: AsyncConnection) -> bool:
    if connection.dialect.name != "postgresql":
        return False
    result = await connection.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_partitioned_table "
            "WHERE partrelid = to_regclass(:table))"
        ),
        {"table": PARENT_TABLE},
    )
    return bool(result.scalar())


async def ensure_partitions(db: AsyncSession, dates: Iterable[datetime]) -> int:
    """Crea las particiones que falten para las fechas dadas. Devuelve cuántas creó.

    También crea las de los DAYS_AHEAD días siguientes: así la partición de un
    día nuevo existe antes de que llegue su primera lectura. Con las anteriores
    ya creadas, en cada día nuevo crea una sola.

    Corre en una conexión y una transacción propias y cortas, no en la de `db`:
    así el lock exclusivo que necesita crear una partición no queda tomado
    mientras dura el lote de la ingesta. Si no se puede crear (por ejemplo, otra
    consulta retiene data_level más que el lock_timeout), se registra un aviso y
    se reintenta en el próximo lote.
    """
    global _partitioned
    wanted = set()
    for value in dates:
        day = day_of(value)
        wanted.update(day + timedelta(days=d) for d in range(DAYS_AHEAD + 1))
    missing = wanted - _known_days
    if not missing or _partitioned is False:
        return 0
    engine = db.bind
    if engine is None or engine.dialect.name != "postgresql":
        return 0
    try:
        async with engine.begin() as connection:
            _partitioned = await is_partitioned(connection)
            if not _partitioned:
                return 0
            await lock_partitions(connection)
            existing = await _existing_days(connection)
            created = 0
            for day in sorted(missing - existing):
                start, end = day_bounds(day)
                await connection.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {partition_name(day)} "
                        f"PARTITION OF {PARENT_TABLE} "
                        f"FOR VALUES FROM ('{start.isoformat()}') "
                        f"TO ('{end.isoformat()}')"
                    )
                )
                created += 1
    except DBAPIError:
        logger.warning("No se pudieron crear particiones de data_level.", exc_info=True)
        return 0
    _known_days.update(missing)
    if created:
        logger.info("Particiones de data_level creadas: %d.", created)
    return created


async def lock_partitions(connection: AsyncConnection) -> None:
    """Lock de creación y borrado de particiones, con lock_timeout para toda la
    transacción: si una consulta larga retiene data_level, se desiste en lugar
    de frenar la ingesta. Se toma antes de leer data_level, así crear y borrar
    piden los locks en el mismo orden y no se bloquean entre sí."""
    await connection.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PARTITION_LOCK_KEY}
    )


async def drop_partitions(db: AsyncSession, before: date | None = None) -> int:
    """Borra las particiones de días anteriores a `before` (todas si es None).

    Corre en la transacción de `db`. Devuelve cuántas borró, o -1 si data_level no
    está particionada (y el que llama tiene que borrar las filas de otra forma).
    """
    connection = await db.connection()
    if not await is_partitioned(connection):
        return -1
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PARTITION_LOCK_KEY}
    )
    dropped = 0
    for day in sorted(await _existing_days(connection)):
        if before is not None and day >= before:
            continue
        await connection.execute(text(f"DROP TABLE IF EXISTS {partition_name(day)}"))
        _known_days.discard(day)
        dropped += 1
    return dropped


def forget_known_partitions() -> None:
    """Olvida la caché (por ejemplo, si otra conexión borró particiones)."""
    global _partitioned
    _known_days.clear()
    _partitioned = None


async def _existing_days(connection: AsyncConnection) -> set[date]:
    result = await connection.execute(
        text(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = to_regclass(:table)"
        ),
        {"table": PARENT_TABLE},
    )
    return {day for (name,) in result.all() if (day := day_from_name(name)) is not None}

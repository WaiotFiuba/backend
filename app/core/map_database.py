from typing import Annotated

from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import get_settings

settings = get_settings()
map_engine = create_async_engine(
    settings.map_database_url,
    echo=False,
    future=True,
    pool_size=settings.map_db_pool_size,
    max_overflow=settings.map_db_max_overflow,
    pool_pre_ping=True,
)
MapSessionLocal = async_sessionmaker(map_engine, expire_on_commit=False)
# Pool aparte para la telemetría
ingest_engine = create_async_engine(
    settings.map_database_url,
    echo=False,
    future=True,
    pool_size=settings.ingest_db_pool_size,
    max_overflow=settings.ingest_db_max_overflow,
    pool_pre_ping=True,
)
IngestSessionLocal = async_sessionmaker(ingest_engine, expire_on_commit=False)
MapBase = declarative_base()


async def get_map_db() -> AsyncSession:
    async with MapSessionLocal() as session:
        yield session


async def get_ingest_db() -> AsyncSession:
    async with IngestSessionLocal() as session:
        yield session


# Tope para los pedidos que el front repite cada pocos segundos: una consulta
# trabada se corta en lugar de seguir corriendo cuando ya nadie la espera.
QUICK_STATEMENT_TIMEOUT = "20s"


def _set_quick_timeout(session, transaction, connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            f"SET LOCAL statement_timeout = '{QUICK_STATEMENT_TIMEOUT}'"
        )


async def get_map_db_quick(
    db: Annotated[AsyncSession, Depends(get_map_db)],
) -> AsyncSession:
    # Al abrir cada transacción, no antes: pedir la conexión acá la retendría
    # mientras la autenticación espera la suya.
    event.listen(db.sync_session, "after_begin", _set_quick_timeout)
    return db

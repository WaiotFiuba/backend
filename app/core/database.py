"""Sesión de la base para los routers de autenticación y usuarios.

Usuarios, mapa y simulación viven en la misma base (MAP_DATABASE_URL). Base es
MapBase, así las migraciones de Alembic cubren también la tabla users.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import MapBase, MapSessionLocal

Base = MapBase


async def get_db() -> AsyncSession:
    async with MapSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

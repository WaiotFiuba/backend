from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import get_settings

settings = get_settings()
map_engine = create_async_engine(
    settings.map_database_url, 
    echo=False, 
    future=True,
    pool_size=30,
    max_overflow=20,
)
MapSessionLocal = async_sessionmaker(map_engine, expire_on_commit=False)
MapBase = declarative_base()


async def get_map_db() -> AsyncSession:
    async with MapSessionLocal() as session:
        yield session


async def init_map_db() -> None:
    import app.models.map  # noqa: F401

    async with map_engine.begin() as conn:
        await conn.run_sync(MapBase.metadata.create_all)

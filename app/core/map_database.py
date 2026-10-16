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
MapBase = declarative_base()


async def get_map_db() -> AsyncSession:
    async with MapSessionLocal() as session:
        yield session

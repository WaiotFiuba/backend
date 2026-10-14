import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.database import get_db
from app.core.map_database import MapBase, get_map_db
from app.core.security import create_access_token
from app.main import app
from app.models.map.simulation import SimulationSession
from app.models.user import User

POOL_SIZE = 2


@pytest_asyncio.fixture
async def small_pool_sessions(tmp_path):
    # Pool chico y timeout corto: si un pedido retiene la conexión de la
    # autenticación mientras pide la del mapa, el bloqueo aparece enseguida.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}",
        poolclass=AsyncAdaptedQueuePool,
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_timeout=2,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: MapBase.metadata.create_all(
                sync_conn, tables=[User.__table__, SimulationSession.__table__]
            )
        )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        session.add(
            User(
                id=1,
                name="Operador",
                language="es",
                email="operador@example.com",
                password="x",
                available=True,
                role_id=1,
            )
        )
        await session.commit()

    async def override_get_db():
        async with session_maker() as session:
            yield session
            await session.commit()

    async def override_get_map_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_map_db] = override_get_map_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_map_db, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_authenticated_requests_do_not_exhaust_the_pool(
    small_pool_sessions,
):
    headers = {"Authorization": f"Bearer {create_access_token('1', 1)}"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.get("/digital-twin/simulations/active", headers=headers)
                for _ in range(POOL_SIZE * 3)
            )
        )

    # Sin simulación activa la respuesta es 404; un 500 sería el pool agotado.
    assert [r.status_code for r in responses] == [404] * (POOL_SIZE * 3)

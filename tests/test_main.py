import pytest_asyncio
from httpx import AsyncClient, ASGILifecycleLoop
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.main import app  # Ajustá el import a tu app FastAPI
from app.core.map_database import get_map_db
from app.models.map.base import MapBase  # Tu Base de SQLAlchemy
from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.waste_type import WasteType

# Cambiá esto por tu URL de test o una DB temporal de pruebas en tu Docker
TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/test_map_db"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Crea la estructura de tablas limpia antes de la suite de tests"""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Aseguramos la extensión PostGIS en la db de test
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        await conn.run_sync(MapBase.metadata.drop_all)
        await conn.run_sync(MapBase.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    """Provee una sesión de base de datos limpia con rollback automático al terminar el test"""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    TestingSessionLocal = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    async with TestingSessionLocal() as session:
        yield session
        # Hacemos rollback para que los inserts de un test no ensucien al siguiente
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session):
    """Cliente HTTP asincrónico para pegarle a los endpoints de FastAPI"""

    async def _override_get_map_db():
        yield db_session

    app.dependency_overrides[get_map_db] = _override_get_map_db
    async with AsyncClient(
        transport=ASGILifecycleLoop(app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seed_data(db_session):
    """Inyecta un set mínimo de datos controlados en la zona del Obelisco"""
    wt = WasteType(name="RSU Fracción Húmeda", color="#2C2C2C")
    ct = ContainerType(name="RSU Fracción Húmeda - Carga Lateral")
    ct.waste_types.append(wt)
    db_session.add_all([wt, ct])
    await db_session.flush()

    # Contenedor 1: Justo en el Obelisco (-34.6037, -58.3815)
    c1 = Container(
        id=1,
        site_id="OBELISCO_1",
        latitude=-34.6037,
        longitude=-58.3815,
        geom="SRID=4326;POINT(-58.3815 -34.6037)",
        current_level=20,
        available=True,
        container_type_id=ct.id,
    )
    # Contenedor 2: Cerca del Obelisco
    c2 = Container(
        id=2,
        site_id="OBELISCO_2",
        latitude=-34.6039,
        longitude=-58.3810,
        geom="SRID=4326;POINT(-58.3810 -34.6039)",
        current_level=85,
        available=True,
        container_type_id=ct.id,
    )
    # Contenedor 3: Lejos (Zoná de Belgrano, fuera del BBox de prueba)
    c3 = Container(
        id=3,
        site_id="BELGRANO_3",
        latitude=-34.5620,
        longitude=-58.4560,
        geom="SRID=4326;POINT(-58.4560 -34.5620)",
        current_level=10,
        available=True,
        container_type_id=ct.id,
    )

    db_session.add_all([c1, c2, c3])
    await db_session.commit()
    return [c1, c2, c3]

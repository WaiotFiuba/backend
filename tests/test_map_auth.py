import pytest
from httpx import ASGITransport, AsyncClient

from app.core.map_database import get_map_db
from app.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.real_auth]

PROTECTED_ENDPOINTS = [
    "/map/containers/types",
    "/map/kpis",
    "/map/optimization/metrics",
    "/map/site-projections/models",
    "/map/sites/1",
    "/map/zone-profiles/radios",
]


async def _get(path: str):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


@pytest.mark.parametrize("path", PROTECTED_ENDPOINTS)
async def test_map_endpoints_require_authentication(path):
    res = await _get(path)

    assert res.status_code == 401


async def test_map_config_is_public():
    class _EmptyResult:
        def one(self):
            return (None,) * 7

    class _EmptySession:
        async def execute(self, *_args, **_kwargs):
            return _EmptyResult()

    async def override_get_map_db():
        yield _EmptySession()

    app.dependency_overrides[get_map_db] = override_get_map_db
    try:
        res = await _get("/map/config")
    finally:
        app.dependency_overrides.pop(get_map_db, None)

    assert res.status_code == 200
    assert res.json()["thresholds"]["critical"] == 80

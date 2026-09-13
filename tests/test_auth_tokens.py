import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
from jose import jwt

from app.core.config import get_settings
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    ALGORITHM,
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.deps import get_current_user
from app.routers.auth import refresh_access_token
from app.schemas.user import RefreshTokenRequest
from app.services.user_service import user_service


def _decode(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])


def test_access_token_includes_access_type():
    payload = _decode(create_access_token("123", 2))

    assert payload["sub"] == "123"
    assert payload["token_type"] == ACCESS_TOKEN_TYPE


def test_refresh_token_includes_refresh_type_and_longer_expiry():
    access_payload = _decode(create_access_token("123", 2))
    refresh_payload = _decode(create_refresh_token("123", 2))

    assert refresh_payload["token_type"] == REFRESH_TOKEN_TYPE
    assert refresh_payload["exp"] > access_payload["exp"]


def test_get_current_user_rejects_refresh_tokens(monkeypatch):
    async def fake_get_user_by_id(db, user_id):
        raise AssertionError("should not query db for refresh tokens")

    monkeypatch.setattr(user_service, "get_user_by_id", fake_get_user_by_id)

    async def run_check():
        await get_current_user(token=create_refresh_token("1", 1), db=object())

    try:
        asyncio.run(run_check())
    except HTTPException as exc_info:
        assert exc_info.status_code == 401
    else:
        raise AssertionError("expected HTTPException")


def test_refresh_access_token_issues_new_access_token(monkeypatch):
    user = SimpleNamespace(id=10, role_id=3, available=True)

    async def fake_get_user_by_id(db, user_id):
        assert user_id == 10
        return user

    monkeypatch.setattr(user_service, "get_user_by_id", fake_get_user_by_id)

    async def run_refresh():
        return await refresh_access_token(
            RefreshTokenRequest(refresh_token=create_refresh_token("10", 3)),
            db=object(),
        )

    result = asyncio.run(run_refresh())

    payload = decode_token(result.access_token)

    assert payload["sub"] == "10"
    assert payload["token_type"] == ACCESS_TOKEN_TYPE

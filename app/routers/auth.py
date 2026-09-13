from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.schemas.user import RefreshTokenRequest, Token, TokenPair, UserCreate, UserRead
from app.services.user_service import user_service


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    try:
        user = await user_service.create_user(db, user_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user


@router.post("/login", response_model=TokenPair)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    user = await user_service.authenticate_user(
        db, form_data.username, form_data.password
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(subject=str(user.id), role_id=user.role_id)
    refresh_token = create_refresh_token(subject=str(user.id), role_id=user.role_id)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=Token)
async def refresh_access_token(
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> Token:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token_payload = decode_token(payload.refresh_token)
        if token_payload.get("token_type") != REFRESH_TOKEN_TYPE:
            raise credentials_exception
        user_id = token_payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except (JWTError, ValueError, TypeError) as exc:
        raise credentials_exception from exc

    user = await user_service.get_user_by_id(db, int(user_id))
    if user is None or not user.available:
        raise credentials_exception

    access_token = create_access_token(subject=str(user.id), role_id=user.role_id)
    return Token(access_token=access_token)

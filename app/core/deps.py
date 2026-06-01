from typing import Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.roles import RoleId
from app.core.security import ACCESS_TOKEN_TYPE, decode_token
from app.models.user import User
from app.services.user_service import user_service


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        token_type = payload.get("token_type")
        if user_id is None:
            raise credentials_exception
        if token_type not in (None, ACCESS_TOKEN_TYPE):
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = await user_service.get_user_by_id(db, int(user_id))
    if user is None or not user.available:
        raise credentials_exception
    return user


def require_roles(roles: Iterable[RoleId]) -> Depends:
    async def _role_guard(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role_id not in [int(role) for role in roles]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
        return current_user

    return Depends(_role_guard)

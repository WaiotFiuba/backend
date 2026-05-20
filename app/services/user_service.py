from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.schemas.user import UserCreate


class UserService:
    async def get_user_by_email(self, db: AsyncSession, email: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalars().first()

    async def get_user_by_id(self, db: AsyncSession, user_id: int) -> Optional[User]:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()

    async def create_user(self, db: AsyncSession, user_in: UserCreate) -> User:
        existing = await self.get_user_by_email(db, user_in.email)
        if existing:
            raise ValueError("Email already registered")

        user = User(
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            role=user_in.role.value,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    async def authenticate_user(
        self, db: AsyncSession, email: str, password: str
    ) -> Optional[User]:
        user = await self.get_user_by_email(db, email)
        if user is None:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user


user_service = UserService()

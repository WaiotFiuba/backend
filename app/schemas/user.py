from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.roles import Role


class UserBase(BaseModel):
    email: EmailStr


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=72)
    role: Role = Role.user

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError(
                "password must be 72 bytes or fewer (bcrypt limit)"
            )
        return value


class UserRead(UserBase):
    id: int
    role: Role
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

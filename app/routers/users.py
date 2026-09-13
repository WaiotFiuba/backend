from fastapi import APIRouter, Depends

from app.core.deps import get_current_user, require_roles
from app.core.roles import RoleId
from app.models.user import User
from app.schemas.user import UserRead


router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: User = Depends(get_current_user)) -> UserRead:
    return current_user


@router.get("/admin-only")
async def admin_only(
    current_user: User = require_roles([RoleId.superadmin]),
) -> dict:
    return {"message": f"Hello {current_user.email}, you are an admin."}

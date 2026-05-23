from fastapi import APIRouter
from app.routers.map import containers

router = APIRouter(prefix="/map", tags=["map"])
router.include_router(containers.router)

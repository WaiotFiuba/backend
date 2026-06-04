from fastapi import APIRouter

from app.routers.map.containers import router as containers_router

router = APIRouter(prefix="/map", tags=["map"])

router.include_router(containers_router)

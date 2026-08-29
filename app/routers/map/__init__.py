from fastapi import APIRouter

from app.routers.map.containers import router as containers_router
from app.routers.map.sites import router as sites_router

router = APIRouter(prefix="/map", tags=["map"])

router.include_router(containers_router)
router.include_router(sites_router)

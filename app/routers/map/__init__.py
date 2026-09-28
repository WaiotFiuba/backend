from fastapi import APIRouter

from app.routers.map.config import router as config_router
from app.routers.map.containers import router as containers_router
from app.routers.map.optimization import router as optimization_router
from app.routers.map.site_projections import router as site_projections_router
from app.routers.map.sites import router as sites_router
from app.routers.map.zone_profiles import router as zone_profiles_router

router = APIRouter(prefix="/map", tags=["map"])

router.include_router(config_router)
router.include_router(containers_router)
router.include_router(optimization_router)
router.include_router(site_projections_router)
router.include_router(sites_router)
router.include_router(zone_profiles_router)

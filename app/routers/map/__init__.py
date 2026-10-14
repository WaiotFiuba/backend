from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.routers.map.config import router as config_router
from app.routers.map.containers import router as containers_router
from app.routers.map.kpis import router as kpis_router
from app.routers.map.optimization import router as optimization_router
from app.routers.map.site_projections import router as site_projections_router
from app.routers.map.sites import router as sites_router
from app.routers.map.zone_profiles import router as zone_profiles_router

router = APIRouter(prefix="/map", tags=["map"])

# /map/config es público: el frontend lo pide al iniciar, antes del login, y no
# expone datos sensibles (ciudad, centro del mapa y umbrales).
router.include_router(config_router)

# El resto del mapa requiere un usuario autenticado.
_authenticated = [Depends(get_current_user)]
router.include_router(containers_router, dependencies=_authenticated)
router.include_router(kpis_router, dependencies=_authenticated)
router.include_router(optimization_router, dependencies=_authenticated)
router.include_router(site_projections_router, dependencies=_authenticated)
router.include_router(sites_router, dependencies=_authenticated)
router.include_router(zone_profiles_router, dependencies=_authenticated)

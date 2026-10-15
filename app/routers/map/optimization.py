from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.map_database import get_map_db
from app.models.user import User
from app.schemas.map.optimization import (
    OptimizationConfig,
    OptimizationMetricsResponse,
    RedistributionPlanRead,
    WhatIfComparisonResponse,
)
from app.services.map.optimization_service import (
    compute_site_utilization_metrics,
    get_plan_by_id,
)
from app.services.map.optimization_whatif_service import (
    activate_whatif,
    create_and_activate_plan,
    deactivate_whatif,
    get_comparison_metrics,
    get_active_plan,
)

router = APIRouter(prefix="/optimization", tags=["optimization"])

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]
# El router de /map ya exige el usuario; acá se lo recibe para registrar quién
# genera cada plan (FastAPI reutiliza el mismo usuario, sin otra consulta).
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.get(
    "/metrics",
    response_model=OptimizationMetricsResponse,
    summary="Obtener métricas de utilización de todos los sitios",
)
async def get_optimization_metrics(
    db: MapDbDep,
    window_days: int | None = Query(
        None,
        ge=1,
        le=365,
        description="Días de historial a analizar. None = todo disponible.",
    ),
) -> OptimizationMetricsResponse:
    config = OptimizationConfig(window_days=window_days)
    return await compute_site_utilization_metrics(db, config)


@router.post(
    "/plan",
    response_model=RedistributionPlanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Generar un plan de redistribución de contenedores y activarlo como What-If",
)
async def create_redistribution_plan(
    config: OptimizationConfig,
    db: MapDbDep,
    current_user: CurrentUserDep,
) -> RedistributionPlanRead:
    return await create_and_activate_plan(db, config, user_id=current_user.id)


@router.get(
    "/plan/active",
    response_model=RedistributionPlanRead | None,
    summary="Obtener el plan de redistribución What-If activo (null si no hay)",
)
async def get_active_whatif_plan(
    db: MapDbDep,
) -> RedistributionPlanRead | None:
    return await get_active_plan(db)


@router.get(
    "/plan/{plan_id}",
    response_model=RedistributionPlanRead,
    summary="Obtener un plan de redistribución por ID",
)
async def get_redistribution_plan(
    plan_id: int,
    db: MapDbDep,
) -> RedistributionPlanRead:
    plan = await get_plan_by_id(db, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan de redistribución no encontrado.",
        )
    return plan


@router.post(
    "/plan/{plan_id}/whatif",
    response_model=RedistributionPlanRead,
    summary="Activar modo what-if para un plan de redistribución",
)
async def activate_plan_whatif(
    plan_id: int,
    db: MapDbDep,
) -> RedistributionPlanRead:
    return await activate_whatif(db, plan_id)


@router.delete(
    "/plan/{plan_id}/whatif",
    response_model=RedistributionPlanRead,
    summary="Desactivar modo what-if para un plan de redistribución",
)
async def deactivate_plan_whatif(
    plan_id: int,
    db: MapDbDep,
) -> RedistributionPlanRead:
    return await deactivate_whatif(db, plan_id)


@router.get(
    "/plan/{plan_id}/comparison",
    response_model=WhatIfComparisonResponse,
    summary="Obtener comparación real vs optimizado para un plan",
)
async def get_plan_comparison(
    plan_id: int,
    db: MapDbDep,
) -> WhatIfComparisonResponse:
    return await get_comparison_metrics(db, plan_id)

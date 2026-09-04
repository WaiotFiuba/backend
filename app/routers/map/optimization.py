from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.schemas.map.optimization import (
    OptimizationConfig,
    OptimizationMetricsResponse,
    RedistributionPlanRead,
    WhatIfComparisonResponse,
)
from app.services.map.optimization_service import (
    compute_site_utilization_metrics,
    generate_redistribution_plan,
    get_plan_by_id,
)
from app.services.map.optimization_whatif_service import (
    activate_whatif,
    deactivate_whatif,
    get_comparison_metrics,
)

router = APIRouter(prefix="/optimization", tags=["optimization"])

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]


@router.get(
    "/metrics",
    response_model=OptimizationMetricsResponse,
    summary="Obtener métricas de utilización de todos los sitios",
)
async def get_optimization_metrics(
    db: MapDbDep,
    window_days: int | None = Query(
        None, description="Días de historial a analizar. None = todo disponible."
    ),
) -> OptimizationMetricsResponse:
    config = OptimizationConfig(window_days=window_days)
    return await compute_site_utilization_metrics(db, config)


@router.post(
    "/plan",
    response_model=RedistributionPlanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Generar un plan de redistribución de contenedores",
)
async def create_redistribution_plan(
    config: OptimizationConfig,
    db: MapDbDep,
) -> RedistributionPlanRead:
    return await generate_redistribution_plan(db, config)


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

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.schemas.map.kpis import (
    NetworkKpiSummaryResponse,
    WhatIfKpiComparisonResponse,
)
from app.services.map.kpi_service import (
    get_kpis_comparison,
    get_real_network_kpis,
    get_whatif_network_kpis,
)

router = APIRouter(prefix="/kpis", tags=["kpis"])

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]


@router.get(
    "",
    response_model=NetworkKpiSummaryResponse,
    summary="Obtener métricas y KPIs operacionales agregadas a nivel sitio",
)
async def get_network_kpis(
    db: MapDbDep,
    whatif: bool = Query(
        False, description="Si es True, calcula métricas sobre la red What-If simulada"
    ),
    plan_id: int | None = Query(
        None,
        description="ID del plan de redistribución (opcional si whatif=True)",
    ),
) -> NetworkKpiSummaryResponse:
    """Retorna las métricas y KPIs de la red (Red Real o Red What-If)."""
    if whatif:
        return await get_whatif_network_kpis(db, plan_id=plan_id)
    return await get_real_network_kpis(db)


@router.get(
    "/comparison",
    response_model=WhatIfKpiComparisonResponse,
    summary="Obtener comparativa ejecutiva instantánea entre red real y plan What-If",
)
async def get_whatif_comparison(
    db: MapDbDep,
    plan_id: int | None = Query(
        None, description="ID del plan a comparar. None = usa el plan activo"
    ),
) -> WhatIfKpiComparisonResponse:
    """Retorna la comparativa entre la línea base real y el plan What-If."""
    return await get_kpis_comparison(db, plan_id=plan_id)

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.schemas.map.site_projection import (
    SiteProjectionEvaluationReportResponse,
    SiteProjectionEvaluationRequest,
    SiteProjectionEvaluationResponse,
    SiteProjectionModel,
    SiteProjectionModelsResponse,
    SiteProjectionRunRequest,
    SiteProjectionRunResponse,
)
from app.services.map.site_projection_evaluation_service import (
    create_site_projection_evaluation,
    get_site_projection_evaluation,
    get_site_projection_evaluation_report,
)
from app.services.map.site_projection_models import (
    DEFAULT_MODEL_KEY,
    list_projection_models,
)
from app.services.map.site_projection_service import (
    create_site_projection_run,
    get_site_projection_run,
)

router = APIRouter(prefix="/site-projections", tags=["site-projections"])

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]


@router.get(
    "/models",
    response_model=SiteProjectionModelsResponse,
    summary="Listar modelos de proyeccion de niveles de sitios",
    description=(
        "Devuelve los modelos disponibles para proyectar el nivel de llenado de "
        "sitios. En la v1 se expone baseline_operational como contrato inicial; "
        "la implementacion completa se agregara en las siguientes tareas."
    ),
)
async def get_site_projection_models() -> SiteProjectionModelsResponse:
    models = [
        SiteProjectionModel.model_validate(model) for model in list_projection_models()
    ]
    return SiteProjectionModelsResponse(
        default_model_key=DEFAULT_MODEL_KEY,
        models=models,
    )


@router.post(
    "",
    response_model=SiteProjectionRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear corrida de proyeccion para multiples sitios",
    description=(
        "Proyecta una lista de sitios, persiste la corrida y guarda todos los "
        "puntos proyectados para auditoria y consulta posterior."
    ),
)
async def create_site_projection(
    db: MapDbDep,
    request: SiteProjectionRunRequest,
) -> SiteProjectionRunResponse:
    return await create_site_projection_run(db=db, request=request)


@router.post(
    "/evaluations",
    response_model=SiteProjectionEvaluationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ejecutar backtesting de un modelo de proyeccion",
    description=(
        "Corta el historico en un instante pasado, proyecta el horizonte pedido "
        "y compara contra observaciones reales posteriores. Persiste metricas "
        "globales y por sitio para auditoria."
    ),
)
async def create_site_projection_evaluation_endpoint(
    db: MapDbDep,
    request: SiteProjectionEvaluationRequest,
) -> SiteProjectionEvaluationResponse:
    return await create_site_projection_evaluation(db=db, request=request)


@router.get(
    "/evaluations/{evaluation_id}",
    response_model=SiteProjectionEvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Recuperar una evaluacion de modelo persistida",
)
async def get_site_projection_evaluation_endpoint(
    db: MapDbDep,
    evaluation_id: Annotated[int, Path(ge=1)],
) -> SiteProjectionEvaluationResponse:
    return await get_site_projection_evaluation(
        db=db,
        evaluation_id=evaluation_id,
    )


@router.get(
    "/evaluations/{evaluation_id}/report",
    response_model=SiteProjectionEvaluationReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Obtener reporte resumido de metricas de una evaluacion",
    description=(
        "Agrupa las metricas persistidas en globales, metricas por sitio y "
        "ranking de sitios mejor y peor predichos."
    ),
)
async def get_site_projection_evaluation_report_endpoint(
    db: MapDbDep,
    evaluation_id: Annotated[int, Path(ge=1)],
) -> SiteProjectionEvaluationReportResponse:
    return await get_site_projection_evaluation_report(
        db=db,
        evaluation_id=evaluation_id,
    )


@router.get(
    "/{run_id}",
    response_model=SiteProjectionRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Recuperar una corrida de proyeccion persistida",
)
async def get_site_projection(
    db: MapDbDep,
    run_id: Annotated[int, Path(ge=1)],
) -> SiteProjectionRunResponse:
    return await get_site_projection_run(db=db, run_id=run_id)

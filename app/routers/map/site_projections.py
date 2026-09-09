from __future__ import annotations

from fastapi import APIRouter

from app.schemas.map.site_projection import (
    SiteProjectionModel,
    SiteProjectionModelsResponse,
)
from app.services.map.site_projection_models import (
    DEFAULT_MODEL_KEY,
    list_projection_models,
)

router = APIRouter(prefix="/site-projections", tags=["site-projections"])


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
        SiteProjectionModel.model_validate(model)
        for model in list_projection_models()
    ]
    return SiteProjectionModelsResponse(
        default_model_key=DEFAULT_MODEL_KEY,
        models=models,
    )

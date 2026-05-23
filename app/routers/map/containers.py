from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.map_database import get_map_db
from app.schemas.map.container import (
    ContainersMapOutputSchema,
    ContainerCluster,
    ContainerDetailOutputSchema,
)
from app.services.map.container_service import (
    get_all_containers,
    get_containers_clustered,
    get_container_by_id,
)

router = APIRouter(prefix="/containers", tags=["containers"])


@router.get(
    "/",
    response_model=List[ContainersMapOutputSchema],
    status_code=status.HTTP_200_OK,
    summary="Obtener todos los contenedores para el mapa",
)
async def read_all_containers(
    db: AsyncSession = Depends(get_map_db),
) -> list[ContainersMapOutputSchema]:
    return await get_all_containers(db)


@router.get(
    "/bbox", response_model=list[ContainerCluster] | list[ContainersMapOutputSchema]
)
async def get_containers_by_bbox(
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lng_min: float = Query(...),
    lng_max: float = Query(...),
    zoom: int = Query(..., ge=0, le=22),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_map_db),
) -> list[ContainerCluster] | list[ContainersMapOutputSchema]:
    return await get_containers_clustered(
        db=db,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        zoom=zoom,
        limit=limit,
    )


@router.get(
    "/{container_id}",
    response_model=ContainerDetailOutputSchema,
    status_code=status.HTTP_200_OK,
    summary="Obtener el detalle de un contenedor específico",
    responses={
        200: {"description": "Detalle del contenedor encontrado con éxito."},
        404: {"description": "El contenedor solicitado no existe en el sistema."},
    },
)
async def read_container_detail(
    container_id: int,
    db: AsyncSession = Depends(get_map_db),
) -> ContainerDetailOutputSchema:
    return await get_container_by_id(db, container_id)

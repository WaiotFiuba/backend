from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.map_database import get_map_db
from app.schemas.map.container import (
    ContainersMapOutputSchema,
    ContainerCluster,
    ContainerDetailOutputSchema,
    ContainerCreateSchema,
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
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_map_db),
) -> list[ContainersMapOutputSchema]:
    return await get_all_containers(db, limit=limit, offset=offset)


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


@router.post(
    "/generate",
    status_code=status.HTTP_201_CREATED,
    summary="Generar o resembrar contenedores de mapa",
)
async def generate_containers(
    force: bool = Query(False, description="Borrar datos existentes antes de re-sembrar"),
    db: AsyncSession = Depends(get_map_db),
):
    #Borra la BDD actual, decomentar si se usa una bdd local. COMENTAR SI SE ESTA APUNTANDO A PRODUCCION
    # if force:
    #     from sqlalchemy import delete
    #     from app.models.map.data_level import DataLevel
    #     from app.models.map.container import Container
    #     await db.execute(delete(DataLevel))
    #     await db.execute(delete(Container))
    #     await db.commit()

    from app.core.map_migrations import seed_map_data
    await seed_map_data()
    return {"message": "Contenedores generados exitosamente"}


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Crear un nuevo contenedor",
)
async def create_container(
    payload: ContainerCreateSchema,
):
    print(f"\n[DEBUG] Contenedor recibido en backend-api (no guardado en BD): {payload.model_dump()}\n")
    return {"message": "Contenedor recibido (no guardado en base de datos)"}


@router.delete(
    "/{container_id}",
    status_code=status.HTTP_200_OK,
    summary="Eliminar un contenedor",
)
async def delete_container(
    container_id: int,
):
    print(f"\n[DEBUG] Petición de eliminación de contenedor en backend-api (no borrado en BD) para ID: {container_id}\n")
    return {"message": f"Contenedor {container_id} recibido para eliminación (no guardado en BD)"}

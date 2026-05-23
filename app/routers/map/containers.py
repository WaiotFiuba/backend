from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.map_database import get_map_db
from app.schemas.map.container import ContainersMapOutputSchema
from app.services.map.container_service import get_all_containers

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.container_type import ContainerType, container_type_waste_types
from app.models.map.waste_type import WasteType
from app.schemas.map.container import ContainersMapOutputSchema


def _zoom_to_precision(zoom: int) -> int | None:
    if zoom < 11:
        return 2  # 10km por celda
    elif zoom < 13:
        return 3  # 1km por celda
    elif zoom < 15:
        return 4  # 100m por celda
    else:
        return None  # puntos exactos


def _row_to_container(row) -> ContainersMapOutputSchema:
    return ContainersMapOutputSchema(
        id=row["id"],
        site_id=row["site_id"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        current_level=row["current_level"],
        available=row["available"],
        # Construimos el objeto anidado para ContainerType y su lista de WasteTypes
        container_type={
            "name": row["container_type"],
            "waste_types": [
                {"name": row["waste_type_name"], "color": row["waste_type_color"]}
            ]
            if row["waste_type_name"]
            else [],
        },
    )


def _base_select():
    return (
        select(
            Container.id,
            Container.site_id,
            Container.latitude,
            Container.longitude,
            Container.current_level,
            Container.available,
            ContainerType.name.label("container_type"),
            WasteType.name.label("waste_type_name"),
            WasteType.color.label("waste_type_color"),
        )
        .outerjoin(ContainerType, Container.container_type_id == ContainerType.id)
        .outerjoin(
            container_type_waste_types,
            ContainerType.id == container_type_waste_types.c.container_type_id,
        )
        .outerjoin(
            WasteType, container_type_waste_types.c.waste_type_id == WasteType.id
        )
    )


async def get_all_containers(
    db: AsyncSession,
) -> list[ContainersMapOutputSchema]:
    result = await db.execute(_base_select())
    rows = result.mappings().all()
    return [_row_to_container(row) for row in rows]

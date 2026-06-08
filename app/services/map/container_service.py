from fastapi import HTTPException, status

from sqlalchemy import select, text
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from geoalchemy2 import functions as geo_funcs

from app.models.map.container import Container
from app.models.map.container_type import ContainerType, container_type_waste_types
from app.models.map.waste_type import WasteType
from app.models.map.neighborhood_demographic import NeighborhoodDemographic
from app.schemas.map.container import ContainersMapOutputSchema, ContainerCluster


def _zoom_to_grid_size(zoom: int) -> float | None:
    """
    Retorna el tamaño de la celda en grados para ST_SnapToGrid.
    Ajustamos los rangos para la escala de Buenos Aires.
    """
    if zoom < 11:
        return 0.05  # ~5km por celda
    elif zoom < 13:
        return 0.01  # ~1km por celda
    elif zoom < 15:
        return 0.002  # ~200m por celda
    else:
        return None  # puntos exactos


def _row_to_container(row) -> ContainersMapOutputSchema:
    return ContainersMapOutputSchema(
        id=row["id"],
        site_id=row["site_id"],
        site_name=row["site_name"],
        device_imei=row["device_imei"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        current_level=row["current_level"],
        available=row["available"],
        zone=row["zone"],
        density_factor=row["density_factor"] or 1.0,
        # Construimos el objeto anidado para ContainerType y su lista de WasteTypes
        container_type={
            "name": row["container_type"] or "",
            "height_cm": row["height_cm"],
            "volume_m3": row["volume_m3"],
            "overflow_zone_cm": row["overflow_zone_cm"],
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
            Container.site_name,
            Container.device_imei,
            Container.latitude,
            Container.longitude,
            Container.current_level,
            Container.available,
            ContainerType.name.label("container_type"),
            ContainerType.height_cm,
            ContainerType.volume_m3,
            ContainerType.overflow_zone_cm,
            WasteType.name.label("waste_type_name"),
            WasteType.color.label("waste_type_color"),
            NeighborhoodDemographic.neighborhood.label("zone"),
            NeighborhoodDemographic.density_factor,
        )
        .outerjoin(ContainerType, Container.container_type_id == ContainerType.id)
        .outerjoin(
            container_type_waste_types,
            ContainerType.id == container_type_waste_types.c.container_type_id,
        )
        .outerjoin(
            WasteType, container_type_waste_types.c.waste_type_id == WasteType.id
        )
        .outerjoin(
            NeighborhoodDemographic,
            geo_funcs.ST_Covers(NeighborhoodDemographic.geom, Container.geom),
        )
    )


async def get_all_containers(
    db: AsyncSession,
    limit: int = 500,
    offset: int = 0,
) -> list[ContainersMapOutputSchema]:
    result = await db.execute(
        _base_select()
        .order_by(Container.id)
        .limit(limit)
        .offset(offset)
    )
    rows = result.mappings().all()
    return [_row_to_container(row) for row in rows]


async def get_containers_in_bbox(
    db: AsyncSession,
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
    limit: int = 500,
) -> list[ContainersMapOutputSchema]:
    # Usamos GeoAlchemy2 para armar el filtro espacial usando el índice GIST de la columna 'geom'
    # Pasamos las coordenadas en el orden correcto de PostGIS: (LngMin, LatMin, LngMax, LatMax, SRID)
    bbox_filter = geo_funcs.ST_Within(
        Container.geom,
        geo_funcs.ST_MakeEnvelope(lng_min, lat_min, lng_max, lat_max, 4326),
    )

    query = _base_select().where(bbox_filter).limit(limit)

    result = await db.execute(query)
    rows = result.mappings().all()
    return [_row_to_container(row) for row in rows]


async def get_containers_clustered(
    db: AsyncSession,
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
    zoom: int,
    limit: int = 500,
) -> list[ContainerCluster] | list[ContainersMapOutputSchema]:
    grid_size = _zoom_to_grid_size(zoom)

    # Si el zoom es muy cercano, delegamos al BBox de puntos exactos
    if grid_size is None:
        return await get_containers_in_bbox(
            db, lat_min, lat_max, lng_min, lng_max, limit
        )

    # Query optimizada con ST_SnapToGrid y ST_Centroid para obtener el centro real del cluster
    query = text("""
        SELECT
            ST_Y(ST_Centroid(ST_Collect(geom))) AS cluster_lat,
            ST_X(ST_Centroid(ST_Collect(geom))) AS cluster_lng,
            COUNT(*) AS total
        FROM containers
        WHERE geom && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)
        GROUP BY ST_SnapToGrid(geom, :grid_size)
        LIMIT :limit;
    """)

    result = await db.execute(
        query,
        {
            "grid_size": grid_size,
            "lat_min": lat_min,
            "lat_max": lat_max,
            "lng_min": lng_min,
            "lng_max": lng_max,
            "limit": limit,
        },
    )

    rows = result.mappings().all()
    return [
        ContainerCluster(
            latitude=row["cluster_lat"],
            longitude=row["cluster_lng"],
            total=row["total"],
        )
        for row in rows
    ]


async def get_container_by_id(db: AsyncSession, container_id: int) -> Container:
    stmt = (
        select(Container)
        .where(Container.id == container_id)
        .options(
            joinedload(Container.container_type).selectinload(ContainerType.waste_types)
        )
    )

    result = await db.execute(stmt)
    container = result.scalar_one_or_none()

    if not container:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contenedor con ID {container_id} no fue encontrado.",
        )

    return container

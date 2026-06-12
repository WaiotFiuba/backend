from fastapi import HTTPException, status

from sqlalchemy import func, select, text
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from geoalchemy2 import functions as geo_funcs

from app.models.map.container import Container
from app.models.map.container_type import ContainerType, container_type_waste_types
from app.models.map.waste_type import WasteType
from app.models.map.neighborhood_demographic import NeighborhoodDemographic
from app.schemas.map.container import (
    ContainerChanges,
    ContainerCluster,
    ContainerMapSnapshot,
    ContainersMapOutputSchema,
)


def _zoom_to_grid_size(zoom: int) -> float | None:
    """
    Retorna el tamaño de la celda en grados para ST_SnapToGrid.
    Ajustamos los rangos para la escala de Buenos Aires.
    """
    if zoom < 13:
        return 0.1  # ~10km
    elif zoom < 14:
        return 0.05  # ~5km
    elif zoom < 15:
        return 0.02  # ~2km
    elif zoom < 16:
        return 0.01  # ~1km
    elif zoom < 17:
        return 0.005  # ~500m
    elif zoom < 18:
        return 0.002  # ~200m
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
        last_reading=row["last_reading"],
        updated_at=row["updated_at"],
        zone=row["zone"],
        density_factor=row["density_factor"] or 1.0,
        # Construimos el objeto anidado para ContainerType y su lista de WasteTypes
        container_type={
            "id": row["container_type_id"],
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
            Container.last_reading,
            Container.updated_at,
            ContainerType.id.label("container_type_id"),
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


def _bbox_filter(
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
):
    return geo_funcs.ST_Within(
        Container.geom,
        geo_funcs.ST_MakeEnvelope(lng_min, lat_min, lng_max, lat_max, 4326),
    )


async def _latest_change_cursor(db: AsyncSession) -> int:
    return await db.scalar(select(func.coalesce(func.max(Container.change_version), 0)))


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
    query = (
        _base_select()
        .where(_bbox_filter(lat_min, lat_max, lng_min, lng_max))
        .order_by(Container.id)
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.mappings().all()
    return [_row_to_container(row) for row in rows]


async def get_container_map_snapshot(
    db: AsyncSession,
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
    zoom: int,
    limit: int = 500,
) -> ContainerMapSnapshot:
    cursor = await _latest_change_cursor(db)
    items = await get_containers_clustered(
        db,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        zoom=zoom,
        limit=limit,
    )
    if items and isinstance(items[0], ContainerCluster):
        return ContainerMapSnapshot(cursor=cursor, containers=[], clusters=items)
    return ContainerMapSnapshot(cursor=cursor, containers=items, clusters=[])


async def get_container_changes(
    db: AsyncSession,
    after: int,
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
    limit: int = 2000,
) -> ContainerChanges:
    latest_cursor = await _latest_change_cursor(db)
    if latest_cursor <= after:
        return ContainerChanges(cursor=latest_cursor, containers=[])

    changed_rows = (
        await db.execute(
            select(Container.id, Container.change_version)
            .where(
                Container.change_version > after,
                Container.change_version <= latest_cursor,
                _bbox_filter(lat_min, lat_max, lng_min, lng_max),
            )
            .order_by(Container.change_version)
            .limit(limit)
        )
    ).all()
    if not changed_rows:
        return ContainerChanges(cursor=latest_cursor, containers=[])

    cursor = changed_rows[-1].change_version if len(changed_rows) == limit else latest_cursor
    changed_ids = {row.id for row in changed_rows}
    rows = (
        await db.execute(
            _base_select()
            .where(Container.id.in_(changed_ids))
            .order_by(Container.id)
        )
    ).mappings().all()
    return ContainerChanges(
        cursor=cursor,
        containers=[_row_to_container(row) for row in rows],
    )


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

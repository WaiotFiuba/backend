from __future__ import annotations

from typing import Literal
from fastapi import HTTPException, status
from sqlalchemy import case, cast, func, select, Numeric
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.schemas.map.site import (
    SiteChanges,
    SiteCluster,
    SiteContainerSummary,
    SiteMapOutputSchema,
    SiteMapSnapshot,
)
from app.services.map.container_service import _zoom_to_grid_size


def _level_expr(level_aggregation: Literal["avg", "max"] = "avg"):
    if level_aggregation == "max":
        return func.coalesce(func.max(Container.current_level), 0)
    return func.coalesce(
        func.round(cast(func.avg(Container.current_level), Numeric)), 0
    )


async def get_latest_cursor(db: AsyncSession) -> int:
    return int(
        await db.scalar(select(func.coalesce(func.max(Container.change_version), 0)))
        or 0
    )


async def get_sites_clustered(
    db: AsyncSession,
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
    zoom: int,
    level_aggregation: Literal["avg", "max"] = "avg",
    limit: int | None = None,
    offset: int | None = None,
) -> list[SiteCluster] | list[SiteMapOutputSchema]:
    grid_size = _zoom_to_grid_size(zoom)

    # Nivel de zoom alto (>= 18): Retornar sitios individuales
    if grid_size is None:
        stmt = (
            select(
                Site.id,
                Site.name,
                Site.address,
                Site.latitude,
                Site.longitude,
                Site.load_side_category,
                Site.waste_type_id,
                Site.updated_at,
                WasteType.name.label("waste_type_name"),
                WasteType.color.label("waste_type_color"),
                _level_expr(level_aggregation).label("current_level"),
                func.count(Container.id).label("container_count"),
                func.max(Container.last_reading).label("last_reading"),
                func.max(Container.last_pickup).label("last_pickup"),
                func.coalesce(func.bool_or(Container.available), True).label(
                    "available"
                ),
            )
            .outerjoin(Container, Container.site_id == Site.id)
            .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
            .where(
                Site.latitude >= lat_min,
                Site.latitude <= lat_max,
                Site.longitude >= lng_min,
                Site.longitude <= lng_max,
                Site.deleted_at.is_(None),
            )
            .group_by(Site.id, WasteType.name, WasteType.color)
            .order_by(Site.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset is not None:
            stmt = stmt.offset(offset)

        result = await db.execute(stmt)
        rows = result.all()

        site_ids = [row.id for row in rows]
        containers_by_site: dict[int, list[SiteContainerSummary]] = {}
        if site_ids:
            c_stmt = (
                select(
                    Container.id,
                    Container.site_id,
                    Container.serie_id,
                    Container.current_level,
                    Container.device_imei,
                    Container.available,
                    Container.last_reading,
                    ContainerType.name.label("container_type"),
                    ContainerType.height_cm,
                    ContainerType.volume_m3,
                )
                .outerjoin(
                    ContainerType, Container.container_type_id == ContainerType.id
                )
                .where(Container.site_id.in_(site_ids))
            )
            c_result = await db.execute(c_stmt)
            for c_row in c_result.all():
                containers_by_site.setdefault(c_row.site_id, []).append(
                    SiteContainerSummary(
                        id=c_row.id,
                        serie_id=c_row.serie_id,
                        current_level=c_row.current_level,
                        device_imei=c_row.device_imei,
                        available=c_row.available,
                        container_type=c_row.container_type,
                        height_cm=c_row.height_cm,
                        volume_m3=c_row.volume_m3,
                        last_reading=c_row.last_reading,
                    )
                )

        output: list[SiteMapOutputSchema] = []
        for r in rows:
            output.append(
                SiteMapOutputSchema(
                    id=r.id,
                    name=r.name,
                    address=r.address,
                    latitude=r.latitude,
                    longitude=r.longitude,
                    current_level=int(r.current_level),
                    available=bool(r.available),
                    load_side_category=r.load_side_category,
                    waste_type_id=r.waste_type_id,
                    waste_type_name=r.waste_type_name,
                    waste_type_color=r.waste_type_color,
                    container_count=int(r.container_count),
                    last_reading=r.last_reading,
                    last_pickup=r.last_pickup,
                    updated_at=r.updated_at,
                    containers=containers_by_site.get(r.id, []),
                )
            )
        return output

    # Nivel de zoom bajo (< 16): Clusters espaciales agregados
    # Agrupamos por grilla espacial pero calculamos el CENTROIDE REAL de los sitios
    grid_lat = func.round(cast(Site.latitude / grid_size, Numeric), 0) * grid_size
    grid_lng = func.round(cast(Site.longitude / grid_size, Numeric), 0) * grid_size

    stmt_cluster = (
        select(
            func.avg(Site.latitude).label("cluster_lat"),
            func.avg(Site.longitude).label("cluster_lng"),
            func.count(func.distinct(Site.id)).label("site_count"),
            func.coalesce(
                func.round(cast(func.avg(Container.current_level), Numeric), 0), 0
            ).label("avg_level"),
            func.coalesce(func.max(Container.current_level), 0).label("max_level"),
            func.coalesce(
                func.sum(case((Container.available.is_(True), 1), else_=0)), 0
            ).label("available_count"),
        )
        .outerjoin(Container, Container.site_id == Site.id)
        .where(
            Site.latitude >= lat_min,
            Site.latitude <= lat_max,
            Site.longitude >= lng_min,
            Site.longitude <= lng_max,
            Site.deleted_at.is_(None),
        )
        .group_by(grid_lat, grid_lng)
    )
    if limit is not None:
        stmt_cluster = stmt_cluster.limit(limit)
    if offset is not None:
        stmt_cluster = stmt_cluster.offset(offset)

    res_cluster = await db.execute(stmt_cluster)
    cluster_rows = res_cluster.all()

    return [
        SiteCluster(
            cluster_id=f"site_cluster_{float(r.cluster_lat):.5f}_{float(r.cluster_lng):.5f}",
            latitude=float(r.cluster_lat),
            longitude=float(r.cluster_lng),
            count=int(r.site_count),
            avg_level=float(r.avg_level),
            max_level=float(r.max_level),
            available_count=int(r.available_count),
        )
        for r in cluster_rows
    ]


async def get_site_map_snapshot(
    db: AsyncSession,
    level_aggregation: Literal["avg", "max"] = "avg",
) -> SiteMapSnapshot:
    latest_cursor = await get_latest_cursor(db)

    stmt = (
        select(
            Site.id,
            Site.name,
            Site.address,
            Site.latitude,
            Site.longitude,
            Site.load_side_category,
            Site.waste_type_id,
            Site.updated_at,
            WasteType.name.label("waste_type_name"),
            WasteType.color.label("waste_type_color"),
            _level_expr(level_aggregation).label("current_level"),
            func.count(Container.id).label("container_count"),
            func.max(Container.last_reading).label("last_reading"),
            func.max(Container.last_pickup).label("last_pickup"),
            func.coalesce(func.bool_or(Container.available), True).label("available"),
        )
        .outerjoin(Container, Container.site_id == Site.id)
        .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
        .where(Site.deleted_at.is_(None))
        .group_by(Site.id, WasteType.name, WasteType.color)
        .order_by(Site.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    sites = [
        SiteMapOutputSchema(
            id=r.id,
            name=r.name,
            address=r.address,
            latitude=r.latitude,
            longitude=r.longitude,
            current_level=int(r.current_level),
            available=bool(r.available),
            load_side_category=r.load_side_category,
            waste_type_id=r.waste_type_id,
            waste_type_name=r.waste_type_name,
            waste_type_color=r.waste_type_color,
            container_count=int(r.container_count),
            last_reading=r.last_reading,
            last_pickup=r.last_pickup,
            updated_at=r.updated_at,
            containers=[],
        )
        for r in rows
    ]

    return SiteMapSnapshot(sites=sites, latest_cursor=latest_cursor, total=len(sites))


async def get_site_changes(
    db: AsyncSession,
    after: int,
    level_aggregation: Literal["avg", "max"] = "avg",
    limit: int = 5000,
) -> SiteChanges:
    latest_cursor = await get_latest_cursor(db)
    if after >= latest_cursor:
        return SiteChanges(sites=[], latest_cursor=latest_cursor, has_more=False)

    # Identificar sitios cuyos contenedores han cambiado
    stmt_changed_sites = (
        select(Container.site_id)
        .where(
            Container.change_version > after,
            Container.change_version <= latest_cursor,
            Container.site_id.is_not(None),
        )
        .distinct()
        .limit(limit)
    )
    res_sites = await db.execute(stmt_changed_sites)
    site_ids = [s for s in res_sites.scalars().all() if s is not None]

    if not site_ids:
        return SiteChanges(sites=[], latest_cursor=latest_cursor, has_more=False)

    stmt = (
        select(
            Site.id,
            Site.name,
            Site.address,
            Site.latitude,
            Site.longitude,
            Site.load_side_category,
            Site.waste_type_id,
            Site.updated_at,
            WasteType.name.label("waste_type_name"),
            WasteType.color.label("waste_type_color"),
            _level_expr(level_aggregation).label("current_level"),
            func.count(Container.id).label("container_count"),
            func.max(Container.last_reading).label("last_reading"),
            func.max(Container.last_pickup).label("last_pickup"),
            func.coalesce(func.bool_or(Container.available), True).label("available"),
        )
        .outerjoin(Container, Container.site_id == Site.id)
        .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
        .where(Site.id.in_(site_ids), Site.deleted_at.is_(None))
        .group_by(Site.id, WasteType.name, WasteType.color)
        .order_by(Site.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Obtener contenedores asociados a estos sitios
    c_stmt = (
        select(
            Container.id,
            Container.site_id,
            Container.serie_id,
            Container.current_level,
            Container.device_imei,
            Container.available,
            Container.last_reading,
            ContainerType.name.label("container_type"),
            ContainerType.height_cm,
            ContainerType.volume_m3,
        )
        .outerjoin(ContainerType, Container.container_type_id == ContainerType.id)
        .where(Container.site_id.in_(site_ids))
    )
    c_result = await db.execute(c_stmt)
    containers_by_site: dict[int, list[SiteContainerSummary]] = {}
    for c_row in c_result.all():
        containers_by_site.setdefault(c_row.site_id, []).append(
            SiteContainerSummary(
                id=c_row.id,
                serie_id=c_row.serie_id,
                current_level=c_row.current_level,
                device_imei=c_row.device_imei,
                available=c_row.available,
                container_type=c_row.container_type,
                height_cm=c_row.height_cm,
                volume_m3=c_row.volume_m3,
                last_reading=c_row.last_reading,
            )
        )

    sites = [
        SiteMapOutputSchema(
            id=r.id,
            name=r.name,
            address=r.address,
            latitude=r.latitude,
            longitude=r.longitude,
            current_level=int(r.current_level),
            available=bool(r.available),
            load_side_category=r.load_side_category,
            waste_type_id=r.waste_type_id,
            waste_type_name=r.waste_type_name,
            waste_type_color=r.waste_type_color,
            container_count=int(r.container_count),
            last_reading=r.last_reading,
            last_pickup=r.last_pickup,
            updated_at=r.updated_at,
            containers=containers_by_site.get(r.id, []),
        )
        for r in rows
    ]

    return SiteChanges(
        sites=sites,
        latest_cursor=latest_cursor,
        has_more=len(site_ids) == limit,
    )


async def get_site_by_id(
    db: AsyncSession,
    site_id: int,
    level_aggregation: Literal["avg", "max"] = "avg",
) -> SiteMapOutputSchema:
    stmt = (
        select(
            Site.id,
            Site.name,
            Site.description,
            Site.address,
            Site.latitude,
            Site.longitude,
            Site.load_side_category,
            Site.waste_type_id,
            Site.updated_at,
            WasteType.name.label("waste_type_name"),
            WasteType.color.label("waste_type_color"),
            _level_expr(level_aggregation).label("current_level"),
            func.count(Container.id).label("container_count"),
            func.max(Container.last_reading).label("last_reading"),
            func.max(Container.last_pickup).label("last_pickup"),
            func.coalesce(func.bool_or(Container.available), True).label("available"),
        )
        .outerjoin(Container, Container.site_id == Site.id)
        .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
        .where(Site.id == site_id, Site.deleted_at.is_(None))
        .group_by(Site.id, WasteType.name, WasteType.color)
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sitio no encontrado."
        )

    c_stmt = (
        select(
            Container.id,
            Container.site_id,
            Container.serie_id,
            Container.current_level,
            Container.device_imei,
            Container.available,
            Container.last_reading,
            ContainerType.name.label("container_type"),
            ContainerType.height_cm,
            ContainerType.volume_m3,
        )
        .outerjoin(ContainerType, Container.container_type_id == ContainerType.id)
        .where(Container.site_id == site_id)
    )
    c_result = await db.execute(c_stmt)
    containers = [
        SiteContainerSummary(
            id=c_row.id,
            serie_id=c_row.serie_id,
            current_level=c_row.current_level,
            device_imei=c_row.device_imei,
            available=c_row.available,
            container_type=c_row.container_type,
            height_cm=c_row.height_cm,
            volume_m3=c_row.volume_m3,
            last_reading=c_row.last_reading,
        )
        for c_row in c_result.all()
    ]

    return SiteMapOutputSchema(
        id=row.id,
        name=row.name,
        address=row.address,
        latitude=row.latitude,
        longitude=row.longitude,
        current_level=int(row.current_level),
        available=bool(row.available),
        load_side_category=row.load_side_category,
        waste_type_id=row.waste_type_id,
        waste_type_name=row.waste_type_name,
        waste_type_color=row.waste_type_color,
        container_count=int(row.container_count),
        last_reading=row.last_reading,
        last_pickup=row.last_pickup,
        updated_at=row.updated_at,
        containers=containers,
    )

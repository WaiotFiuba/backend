from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.digital_twin.synthetic_data.simulation.scenario import scenario_from_mapping
from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.data_level import DataLevel
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.schemas.map.site import (
    SiteChanges,
    SiteCluster,
    SiteContainerSummary,
    SiteLevelHistory,
    SiteLevelHistoryPoint,
    SiteMapOutputSchema,
    SiteMapSnapshot,
)
from app.services.map.container_service import _zoom_to_grid_size
from app.services.simulation_control_service import get_active_simulation_session


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
    site_id: int | str,
    level_aggregation: Literal["avg", "max"] = "avg",
) -> SiteMapOutputSchema:
    # Si site_id es un string con prefijo tipo 'contenedores_negros|27097', extraer el ID raw
    raw_id_str = str(site_id).split("|")[-1].strip()
    try:
        numeric_site_id = int(raw_id_str)
    except ValueError:
        numeric_site_id = None

    if numeric_site_id is not None:
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
                func.coalesce(func.bool_or(Container.available), True).label(
                    "available"
                ),
            )
            .outerjoin(Container, Container.site_id == Site.id)
            .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
            .where(Site.id == numeric_site_id, Site.deleted_at.is_(None))
            .group_by(Site.id, WasteType.name, WasteType.color)
        )
        result = await db.execute(stmt)
        row = result.first()
        if row:
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
                .where(Container.site_id == numeric_site_id)
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

    # Fallback: Buscar contenedor individual por ID o serie_id
    from sqlalchemy.orm import joinedload

    cond = Container.serie_id == raw_id_str
    if numeric_site_id is not None:
        cond = (Container.id == numeric_site_id) | cond

    c_query = (
        select(Container)
        .options(
            joinedload(Container.container_type).selectinload(ContainerType.waste_types)
        )
        .where(cond)
        .limit(1)
    )
    c_res = await db.execute(c_query)
    single_c = c_res.scalar_one_or_none()
    if not single_c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sitio no encontrado."
        )

    ctype = single_c.container_type
    wtype = ctype.waste_types[0] if ctype and ctype.waste_types else None

    summary = SiteContainerSummary(
        id=single_c.id,
        serie_id=single_c.serie_id,
        current_level=single_c.current_level,
        device_imei=single_c.device_imei,
        available=single_c.available,
        container_type=ctype.name if ctype else None,
        height_cm=ctype.height_cm if ctype else None,
        volume_m3=ctype.volume_m3 if ctype else None,
        last_reading=single_c.last_reading,
    )

    return SiteMapOutputSchema(
        id=single_c.id,
        name=single_c.site_name or single_c.description or f"Sitio #{single_c.id}",
        address=single_c.address,
        latitude=single_c.latitude,
        longitude=single_c.longitude,
        current_level=int(single_c.current_level),
        available=bool(single_c.available),
        load_side_category="Lateral",
        waste_type_id=wtype.id if wtype else None,
        waste_type_name=wtype.name if wtype else "RSU Fracción Húmeda",
        waste_type_color=wtype.color if wtype else "#4B5563",
        container_count=1,
        last_reading=single_c.last_reading,
        last_pickup=single_c.last_pickup,
        updated_at=single_c.updated_at,
        containers=[summary],
    )


async def get_site_level_history(
    db: AsyncSession,
    site_id: int,
    limit: int = 168,
) -> SiteLevelHistory:
    exists_stmt = select(Site.id).where(Site.id == site_id, Site.deleted_at.is_(None))
    exists = await db.scalar(exists_stmt)
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sitio no encontrado."
        )

    active_session = await get_active_simulation_session(db)
    if active_session is None or active_session.simulated_time is None:
        return SiteLevelHistory(site_id=site_id)

    config = scenario_from_mapping(active_session.scenario)
    window_start = _as_utc(config.start)
    window_end = _as_utc(active_session.simulated_time)
    if window_end < window_start:
        return SiteLevelHistory(
            site_id=site_id,
            simulation_id=active_session.id,
            window_start=window_start,
            window_end=window_end,
        )

    stmt = (
        select(
            DataLevel.reading_date.label("timestamp"),
            func.coalesce(
                func.round(
                    cast(func.avg(DataLevel.container_current_level), Numeric), 2
                ),
                0,
            ).label("avg_level"),
            func.coalesce(func.max(DataLevel.container_current_level), 0).label(
                "max_level"
            ),
            func.coalesce(func.min(DataLevel.container_current_level), 0).label(
                "min_level"
            ),
            func.count(DataLevel.id).label("measurement_count"),
        )
        .join(Container, DataLevel.container_id == Container.id)
        .where(
            Container.site_id == site_id,
            DataLevel.container_current_level.is_not(None),
            DataLevel.reading_date >= window_start,
            DataLevel.reading_date <= window_end,
        )
        .group_by(DataLevel.reading_date)
        .order_by(DataLevel.reading_date.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(reversed(result.all()))

    return SiteLevelHistory(
        site_id=site_id,
        simulation_id=active_session.id,
        window_start=window_start,
        window_end=window_end,
        points=[
            SiteLevelHistoryPoint(
                timestamp=row.timestamp,
                avg_level=float(row.avg_level),
                max_level=int(row.max_level),
                min_level=int(row.min_level),
                measurement_count=int(row.measurement_count),
            )
            for row in rows
        ],
    )

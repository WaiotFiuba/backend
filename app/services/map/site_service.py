from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import Numeric, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from app.services.map.container_service import _zoom_to_grid_size
from app.services.simulation_session_service import get_active_simulation_session


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _level_expr(
    level_aggregation: Literal["avg", "max"] = "avg",
    c_level=None,
):
    if c_level is None:
        c_level = Container.current_level
    if level_aggregation == "max":
        return func.coalesce(func.max(c_level), 0)
    return func.coalesce(func.round(cast(func.avg(c_level), Numeric)), 0)


def _build_site_container_summary(c_row) -> SiteContainerSummary:
    return SiteContainerSummary(
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


def _build_site_map_output(
    row, containers: list[SiteContainerSummary] | None = None
) -> SiteMapOutputSchema:
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
        containers=containers if containers is not None else [],
    )


def _active_containers_join(target_c, c_site_id):
    """Condición de join sitio-contenedor; excluye contenedores dados de baja."""
    condition = c_site_id == Site.id
    if target_c is Container:
        condition = condition & Container.deleted_at.is_(None)
    return condition


def _containers_query(target_c=Container, site_ids: list[int] | None = None):
    cols = target_c.c if hasattr(target_c, "c") else Container
    stmt = (
        select(
            cols.id.label("id"),
            cols.site_id.label("site_id"),
            cols.serie_id.label("serie_id"),
            cols.current_level.label("current_level"),
            cols.device_imei.label("device_imei"),
            cols.available.label("available"),
            cols.last_reading.label("last_reading"),
            ContainerType.name.label("container_type"),
            ContainerType.height_cm,
            ContainerType.volume_m3,
        )
        .select_from(target_c)
        .outerjoin(ContainerType, cols.container_type_id == ContainerType.id)
    )
    if target_c is Container:
        stmt = stmt.where(Container.deleted_at.is_(None))
    if site_ids is not None:
        stmt = stmt.where(cols.site_id.in_(site_ids))
    return stmt


def _waste_filter_conditions(
    waste_filter: Literal["all", "humedo", "reciclable"],
):
    if waste_filter == "reciclable":
        waste_name = func.lower(func.coalesce(WasteType.name, ""))
        return [or_(waste_name.like("%seca%"), waste_name.like("%recicl%"))]
    if waste_filter == "humedo":
        waste_name = func.lower(func.coalesce(WasteType.name, ""))
        return [
            or_(
                waste_name.like("%humeda%"),
                waste_name.like("%húmeda%"),
                waste_name.like("%hÃºmeda%"),
            )
        ]
    return []


async def _fetch_containers_by_site(
    db: AsyncSession, target_c, site_ids: list[int]
) -> dict[int, list[SiteContainerSummary]]:
    if not site_ids:
        return {}
    c_stmt = _containers_query(target_c, site_ids)
    c_result = await db.execute(c_stmt)
    containers_by_site: dict[int, list[SiteContainerSummary]] = {}
    for c_row in c_result.all():
        containers_by_site.setdefault(c_row.site_id, []).append(
            _build_site_container_summary(c_row)
        )
    return containers_by_site


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
    distribution: Literal["real", "whatif"] = "real",
    waste_filter: Literal["all", "humedo", "reciclable"] = "all",
) -> list[SiteCluster] | list[SiteMapOutputSchema]:
    grid_size = _zoom_to_grid_size(zoom)

    # Definir origen de datos de contenedores según la distribución
    target_c = Container
    if distribution == "whatif":
        from app.models.map.optimization import OptimizationWhatIfLevel
        from app.services.map.optimization_whatif_service import (
            get_or_create_default_whatif_plan,
        )

        plan = await get_or_create_default_whatif_plan(db)
        target_c = (
            select(
                Container.id.label("id"),
                Container.serie_id.label("serie_id"),
                Container.device_imei.label("device_imei"),
                Container.available.label("available"),
                Container.container_type_id.label("container_type_id"),
                Container.last_reading.label("last_reading"),
                Container.last_pickup.label("last_pickup"),
                func.coalesce(
                    OptimizationWhatIfLevel.optimized_site_id, Container.site_id
                ).label("site_id"),
                func.coalesce(
                    OptimizationWhatIfLevel.virtual_level, Container.current_level
                ).label("current_level"),
            )
            .outerjoin(
                OptimizationWhatIfLevel,
                (OptimizationWhatIfLevel.container_id == Container.id)
                & (OptimizationWhatIfLevel.plan_id == plan.id),
            )
            .where(Container.deleted_at.is_(None))
            .subquery()
        )

    cols = target_c.c if hasattr(target_c, "c") else Container
    c_id = cols.id
    c_site_id = cols.site_id
    c_level = cols.current_level
    c_available = cols.available
    c_last_reading = cols.last_reading
    c_last_pickup = cols.last_pickup
    waste_conditions = _waste_filter_conditions(waste_filter)

    # Nivel de zoom alto (>= 18): Retornar sitios individuales
    if grid_size is None:
        calc_level = _level_expr(level_aggregation, c_level)

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
                calc_level.label("current_level"),
                func.count(c_id).label("container_count"),
                func.max(c_last_reading).label("last_reading"),
                func.max(c_last_pickup).label("last_pickup"),
                func.coalesce(func.bool_or(c_available), True).label("available"),
            )
            .outerjoin(target_c, _active_containers_join(target_c, c_site_id))
            .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
            .where(
                Site.latitude >= lat_min,
                Site.latitude <= lat_max,
                Site.longitude >= lng_min,
                Site.longitude <= lng_max,
                Site.deleted_at.is_(None),
                *waste_conditions,
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
        containers_by_site = await _fetch_containers_by_site(db, target_c, site_ids)

        return [
            _build_site_map_output(r, containers_by_site.get(r.id, [])) for r in rows
        ]

    # Nivel de zoom bajo (< 17): Clusters espaciales agregados
    # Agrupamos por grilla espacial pero calculamos el CENTROIDE REAL de los sitios
    grid_lat = func.round(cast(Site.latitude / grid_size, Numeric), 0) * grid_size
    grid_lng = func.round(cast(Site.longitude / grid_size, Numeric), 0) * grid_size

    calc_cluster_level = (
        func.coalesce(func.max(c_level), 0)
        if level_aggregation == "max"
        else func.coalesce(func.round(cast(func.avg(c_level), Numeric), 0), 0)
    )

    stmt_cluster = (
        select(
            func.avg(Site.latitude).label("cluster_lat"),
            func.avg(Site.longitude).label("cluster_lng"),
            func.count(c_id).label("container_count"),
            calc_cluster_level.label("avg_level"),
            func.coalesce(func.max(c_level), 0).label("max_level"),
            func.coalesce(func.sum(case((c_available.is_(True), 1), else_=0)), 0).label(
                "available_count"
            ),
        )
        .outerjoin(target_c, _active_containers_join(target_c, c_site_id))
        .where(
            Site.latitude >= lat_min,
            Site.latitude <= lat_max,
            Site.longitude >= lng_min,
            Site.longitude <= lng_max,
            Site.deleted_at.is_(None),
            *waste_conditions,
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
            count=int(r.container_count),
            avg_level=float(r.avg_level),
            max_level=float(r.max_level),
            available_count=int(r.available_count),
        )
        for r in cluster_rows
    ]


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
        .outerjoin(Container, _active_containers_join(Container, Container.site_id))
        .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
        .where(Site.id.in_(site_ids), Site.deleted_at.is_(None))
        .group_by(Site.id, WasteType.name, WasteType.color)
        .order_by(Site.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    containers_by_site = await _fetch_containers_by_site(db, Container, site_ids)
    sites = [_build_site_map_output(r, containers_by_site.get(r.id, [])) for r in rows]

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
            .outerjoin(Container, _active_containers_join(Container, Container.site_id))
            .outerjoin(WasteType, Site.waste_type_id == WasteType.id)
            .where(Site.id == numeric_site_id, Site.deleted_at.is_(None))
            .group_by(Site.id, WasteType.name, WasteType.color)
        )
        result = await db.execute(stmt)
        row = result.first()
        if row:
            containers_by_site = await _fetch_containers_by_site(
                db, Container, [numeric_site_id]
            )
            return _build_site_map_output(
                row, containers_by_site.get(numeric_site_id, [])
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

    # Inicio de la simulacion: el escenario efectivo que reporta el simulador al
    # arrancar la sesion siempre lo incluye.
    scenario_start = (active_session.scenario or {}).get("start")
    if not scenario_start:
        return SiteLevelHistory(site_id=site_id, simulation_id=active_session.id)
    window_start = _as_utc(datetime.fromisoformat(str(scenario_start)))
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

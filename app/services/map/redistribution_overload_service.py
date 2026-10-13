"""Redistribución de contenedores con el modelo de sobrecarga (algorithm="overload").

Lee de la base los sitios que pueden participar de un plan, arma su perfil de
sobrecarga a partir de la historia y los agrupa por tipo de residuo y de
contenedor: solo se mueven contenedores entre sitios compatibles.

Ver redistribution_overload.py (modelo) y redistribution_overload_planner.py
(elección de movimientos).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.container import Container
from app.models.map.data_level import DataLevel
from app.models.map.site import Site
from app.services.map.redistribution_overload import (
    DEFAULT_CRITICAL_LEVEL,
    build_overload_profile,
)
from app.services.map.redistribution_overload_planner import SiteState
from app.services.map.reference_time_service import get_reference_time
from app.services.map.site_capacity_service import get_site_capacity_service
from app.services.map.site_projection_history_service import get_site_level_series

# Días de historia que se usan si la configuración no indica otra ventana.
DEFAULT_WINDOW_DAYS = 28
SERIES_INTERVAL_MINUTES = 60

# Sitios compatibles: mismo tipo de residuo y de contenedor.
CompatibilityGroup = tuple[int | None, int | None]


@dataclass(frozen=True)
class OverloadCandidate:
    state: SiteState
    name: str
    group: CompatibilityGroup
    # Nivel medio del sitio en la ventana; se usa para mostrar el impacto de
    # cada movimiento (nivel antes/después).
    average_level: float


async def load_overload_candidates(
    db: AsyncSession,
    window_days: int | None = None,
    critical_level: float = DEFAULT_CRITICAL_LEVEL,
    apply_capacity_constraints: bool = True,
) -> list[OverloadCandidate]:
    """Sitios que pueden recibir o donar contenedores, con su perfil de sobrecarga.

    Receptores posibles: sitios que pasaron el umbral crítico en la ventana.
    Donantes posibles: sitios con 2 o más contenedores. El resto no puede
    participar de ningún movimiento, así que no se carga su historia.
    """
    window_days = window_days or DEFAULT_WINDOW_DAYS
    reference_time = await get_reference_time(db)
    window_start = reference_time - timedelta(days=window_days)

    site_rows = (
        await db.execute(
            select(
                Site.id,
                Site.name,
                Site.latitude,
                Site.longitude,
                Site.waste_type_id,
                func.count(Container.id).label("containers"),
                func.min(Container.container_type_id).label("container_type_id"),
            )
            .join(
                Container,
                (Container.site_id == Site.id) & Container.deleted_at.is_(None),
            )
            .where(Site.deleted_at.is_(None))
            .group_by(Site.id)
        )
    ).all()

    reached_critical = set(
        (
            await db.scalars(
                select(Container.site_id)
                .join(DataLevel, DataLevel.container_id == Container.id)
                .where(
                    Container.deleted_at.is_(None),
                    DataLevel.reading_date >= window_start,
                    DataLevel.reading_date <= reference_time,
                    DataLevel.container_current_level >= critical_level,
                )
                .distinct()
            )
        ).all()
    )
    rows = [
        row for row in site_rows if row.id in reached_critical or row.containers >= 2
    ]
    if not rows:
        return []

    series = await get_site_level_series(
        db,
        site_ids=[row.id for row in rows],
        interval_minutes=SERIES_INTERVAL_MINUTES,
        level_aggregation="avg",
        lookback_days=window_days,
        end_time=reference_time,
    )
    capacity_service = get_site_capacity_service()

    candidates: list[OverloadCandidate] = []
    for row in rows:
        buckets = series.get(row.id, [])
        profile = build_overload_profile(
            buckets, row.containers, SERIES_INTERVAL_MINUTES
        )
        if profile is None:
            continue  # sin historia no se puede estimar qué le pasaría
        max_containers = None
        if apply_capacity_constraints:
            max_containers, _ = capacity_service.evaluate_site(
                row.latitude, row.longitude, row.containers
            )
        candidates.append(
            OverloadCandidate(
                state=SiteState(
                    site_id=row.id,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    containers=row.containers,
                    profile=profile,
                    max_containers=max_containers,
                ),
                name=row.name,
                group=(row.waste_type_id, row.container_type_id),
                average_level=sum(bucket.level for bucket in buckets) / len(buckets),
            )
        )
    return candidates


def group_by_compatibility(
    candidates: list[OverloadCandidate],
) -> dict[CompatibilityGroup, list[OverloadCandidate]]:
    groups: dict[CompatibilityGroup, list[OverloadCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.group].append(candidate)
    return dict(groups)

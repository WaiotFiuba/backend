from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.caba_geo_extension import (
    Barrio,
    CabaContainerSpatialMetadata,
    Comuna,
)
from app.models.map.container import Container
from app.models.map.neighborhood_demographic import NeighborhoodDemographic
from app.models.map.site_projection import SiteFeature

CABA_DEMOGRAPHICS_SOURCE = "caba_demographics"


@dataclass(frozen=True)
class CabaSiteFeatureSource:
    site_id: int
    neighborhood_name: str | None
    commune: int | None
    population: int | None
    density_per_km2: float | None
    density_factor: float | None
    container_count: int


@dataclass(frozen=True)
class SiteFeatureValue:
    feature_key: str
    numeric_value: float | None = None
    category_value: str | None = None


@dataclass(frozen=True)
class SiteFeatureRefreshResult:
    source: str
    matched_sites: int
    skipped_sites: int
    features_written: int


def build_caba_site_features(source: CabaSiteFeatureSource) -> list[SiteFeatureValue]:
    features: list[SiteFeatureValue] = []

    if source.neighborhood_name:
        features.append(
            SiteFeatureValue(
                feature_key="territory.neighborhood",
                category_value=source.neighborhood_name,
            )
        )
    if source.commune is not None:
        features.append(
            SiteFeatureValue(
                feature_key="territory.commune",
                numeric_value=float(source.commune),
                category_value=str(source.commune),
            )
        )
    if source.population is not None:
        features.append(
            SiteFeatureValue(
                feature_key="demographics.population",
                numeric_value=float(source.population),
            )
        )
    if source.density_per_km2 is not None:
        features.append(
            SiteFeatureValue(
                feature_key="demographics.density_per_km2",
                numeric_value=float(source.density_per_km2),
            )
        )
    if source.density_factor is not None:
        features.append(
            SiteFeatureValue(
                feature_key="demand.density_factor",
                numeric_value=float(source.density_factor),
            )
        )

    return features


def choose_dominant_caba_sources(
    rows: list[CabaSiteFeatureSource],
) -> dict[int, CabaSiteFeatureSource]:
    selected: dict[int, CabaSiteFeatureSource] = {}
    for row in sorted(rows, key=lambda item: (-item.container_count, item.site_id)):
        selected.setdefault(row.site_id, row)
    return selected


async def refresh_caba_site_features(
    db: AsyncSession,
    source: str = CABA_DEMOGRAPHICS_SOURCE,
) -> SiteFeatureRefreshResult:
    sources = await _load_caba_site_feature_sources(db)
    dominant_sources = choose_dominant_caba_sources(sources)

    await db.execute(delete(SiteFeature).where(SiteFeature.source == source))

    features_to_insert: list[SiteFeature] = []
    skipped_sites = 0
    for site_source in dominant_sources.values():
        feature_values = build_caba_site_features(site_source)
        if not feature_values:
            skipped_sites += 1
            continue
        features_to_insert.extend(
            SiteFeature(
                site_id=site_source.site_id,
                feature_key=feature.feature_key,
                numeric_value=feature.numeric_value,
                category_value=feature.category_value,
                source=source,
            )
            for feature in feature_values
        )

    db.add_all(features_to_insert)
    await db.flush()

    return SiteFeatureRefreshResult(
        source=source,
        matched_sites=len(dominant_sources) - skipped_sites,
        skipped_sites=skipped_sites,
        features_written=len(features_to_insert),
    )


async def _load_caba_site_feature_sources(
    db: AsyncSession,
) -> list[CabaSiteFeatureSource]:
    stmt = (
        select(
            Container.site_id.label("site_id"),
            Barrio.nombre.label("neighborhood_name"),
            Comuna.comuna.label("commune"),
            NeighborhoodDemographic.population.label("population"),
            NeighborhoodDemographic.density_per_km2.label("density_per_km2"),
            NeighborhoodDemographic.density_factor.label("density_factor"),
            func.count(Container.id).label("container_count"),
        )
        .join(
            CabaContainerSpatialMetadata,
            CabaContainerSpatialMetadata.container_id == Container.id,
        )
        .outerjoin(Barrio, Barrio.id == CabaContainerSpatialMetadata.barrio_id)
        .outerjoin(Comuna, Comuna.id == CabaContainerSpatialMetadata.comuna_id)
        .outerjoin(
            NeighborhoodDemographic,
            NeighborhoodDemographic.neighborhood_id == Barrio.id,
        )
        .where(
            Container.site_id.is_not(None),
            Container.deleted_at.is_(None),
        )
        .group_by(
            Container.site_id,
            Barrio.nombre,
            Comuna.comuna,
            NeighborhoodDemographic.population,
            NeighborhoodDemographic.density_per_km2,
            NeighborhoodDemographic.density_factor,
        )
        .order_by(Container.site_id, desc("container_count"))
    )
    result = await db.execute(stmt)
    return [
        CabaSiteFeatureSource(
            site_id=int(row.site_id),
            neighborhood_name=row.neighborhood_name,
            commune=row.commune,
            population=row.population,
            density_per_km2=row.density_per_km2,
            density_factor=row.density_factor,
            container_count=int(row.container_count),
        )
        for row in result.all()
        if row.site_id is not None
    ]

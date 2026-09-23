from app.services.map.site_feature_service import (
    CabaSiteFeatureSource,
    build_caba_site_features,
    choose_dominant_caba_sources,
)


def test_build_caba_site_features_maps_demographics_to_generic_features():
    source = CabaSiteFeatureSource(
        site_id=10,
        neighborhood_name="Palermo",
        commune=14,
        population=225970,
        density_per_km2=16132.5,
        density_factor=1.42,
        container_count=3,
    )

    features = build_caba_site_features(source)
    by_key = {feature.feature_key: feature for feature in features}

    assert by_key["territory.neighborhood"].category_value == "Palermo"
    assert by_key["territory.commune"].numeric_value == 14
    assert by_key["demographics.population"].numeric_value == 225970
    assert by_key["demographics.density_per_km2"].numeric_value == 16132.5
    assert by_key["demand.density_factor"].numeric_value == 1.42


def test_build_caba_site_features_allows_sites_without_territorial_match():
    source = CabaSiteFeatureSource(
        site_id=11,
        neighborhood_name=None,
        commune=None,
        population=None,
        density_per_km2=None,
        density_factor=None,
        container_count=1,
    )

    assert build_caba_site_features(source) == []


def test_choose_dominant_caba_sources_keeps_largest_container_match_per_site():
    rows = [
        CabaSiteFeatureSource(1, "Recoleta", 2, 100, 1000.0, 0.8, 1),
        CabaSiteFeatureSource(1, "Palermo", 14, 200, 2000.0, 1.2, 3),
        CabaSiteFeatureSource(2, "Belgrano", 13, 300, 3000.0, 1.4, 2),
    ]

    selected = choose_dominant_caba_sources(rows)

    assert selected[1].neighborhood_name == "Palermo"
    assert selected[2].neighborhood_name == "Belgrano"

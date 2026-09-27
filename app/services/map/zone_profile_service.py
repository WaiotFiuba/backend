from __future__ import annotations

from functools import lru_cache

from shapely.geometry import mapping

from app.digital_twin.synthetic_data.density_processor import get_density_processor
from app.digital_twin.synthetic_data.zone_classifier import get_zone_classifier


@lru_cache
def get_zone_profiles_geojson() -> dict:
    """FeatureCollection con el polígono de cada radio censal de CABA y su
    perfil de zona (zone_type, demand_multiplier, weekend_factor, curva
    horaria/semanal). Se cachea porque surge de datos estáticos (CSV/YAML)
    que no cambian durante la vida del proceso.
    """
    processor = get_density_processor()
    classifier = get_zone_classifier()

    features = []
    for radio in processor.radios:
        profile = classifier.get_profile_for_radio(radio.radio_code)
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(radio.geometry),
                "properties": {
                    "radio_code": radio.radio_code,
                    "barrio": profile.barrio,
                    "department_name": radio.department_name,
                    "population": radio.population,
                    "zone_type": profile.zone_type,
                    "demand_multiplier": round(profile.demand_multiplier, 3),
                    "weekend_factor": round(profile.weekend_factor, 3),
                    "hour_weights": profile.hour_weights,
                    "weekday_factors": profile.weekday_factors,
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}

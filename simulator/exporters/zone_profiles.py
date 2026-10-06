"""Capa de perfiles de zona: el poligono de cada radio censal con el perfil de
demanda que el simulador le asigna (tipo de zona, multiplicador, curvas).

El worker la arma al iniciar cada sesion, con el zone_profiles.yaml vigente, y
se la manda al backend, que la sirve al mapa del front.
"""

from __future__ import annotations

from shapely.geometry import mapping

from simulator.density_processor import DensityProcessor
from simulator.zone_classifier import ZoneClassifier


def build_zone_profiles_geojson(
    processor: DensityProcessor, classifier: ZoneClassifier
) -> dict:
    """FeatureCollection con el poligono de cada radio censal y su perfil de
    zona (zone_type, demand_multiplier, weekend_factor, curva horaria/semanal).
    """
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

from datetime import datetime
from pathlib import Path

import pytest

from app.digital_twin.synthetic_data.domain.entities import (
    Container,
    Device,
    Site,
)
from app.digital_twin.synthetic_data.simulation.engine import SyntheticDataSimulator
from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig
from app.digital_twin.synthetic_data.topology import SimulationTopology
from app.digital_twin.synthetic_data.zone_classifier import (
    DEFAULT_PROFILE,
    ZoneClassifier,
    get_zone_classifier,
)


def test_zone_classifier_loads_radios_and_profiles():
    classifier = get_zone_classifier()
    summary = classifier.summary()

    assert len(summary) > 0
    assert "residential_multifamily" in summary
    assert "commercial" in summary


def test_zone_classifier_distinguishes_multifamily_and_singlefamily():
    classifier = get_zone_classifier()

    # Perfil multifamiliar (edificios con encargado)
    # En sábado (weekday=5) y domingo (weekday=6), el factor baja fuertemente
    p_multi = classifier.get_profile("residential_multifamily")
    # Hora pico encargado (8h)
    assert p_multi.get_hour_weight(8) > 1.3
    # Fin de semana (sábado=5, domingo=6)
    assert p_multi.get_weekday_factor(5) <= 0.65
    assert p_multi.get_weekday_factor(6) <= 0.60

    # Perfil unifamiliar (casas)
    p_single = classifier.get_profile("residential_singlefamily")
    # Fin de semana no cae tan drásticamente porque la gente está en casa
    assert p_single.get_weekday_factor(5) >= 0.80
    assert p_single.get_weekday_factor(6) >= 0.80


def test_zone_classifier_radio_lookup():
    classifier = get_zone_classifier()

    # Búsqueda por radio censal existente (e.g. 20980101 en Palermo)
    profile = classifier.get_profile("20980101")
    assert profile.zone_key == "20980101"
    assert profile.demand_multiplier > 0
    assert profile.res_multifamily_pct >= 0
    assert profile.res_singlefamily_pct >= 0


def test_zone_classifier_barrio_lookup_fallback():
    classifier = get_zone_classifier()

    # Búsqueda por nombre de barrio
    profile_palermo = classifier.get_profile("PALERMO")
    assert profile_palermo.demand_multiplier > 0

    # Búsqueda con Comuna
    profile_comuna14 = classifier.get_profile("COMUNA 14")
    assert profile_comuna14.demand_multiplier > 0


def test_zone_classifier_unknown_fallback():
    classifier = get_zone_classifier()

    profile_unknown = classifier.get_profile("NON_EXISTENT_ZONE_XYZ_123")
    assert profile_unknown == DEFAULT_PROFILE


def test_simulation_differentiates_multifamily_vs_singlefamily_over_weekend():
    """
    Verifica que dos sitios residenciales (uno en radio de edificios multifamiliares
    y otro en radio de casas unifamiliares) muestran comportamientos diferenciados
    el fin de semana.
    """
    # Sitio 1: Multifamiliar (edificios)
    site_multi = Site(
        id="SITE_MULTI",
        name="Edificios Palermo",
        zone="residential_multifamily",
        latitude=-34.58,
        longitude=-58.42,
        demand_base=1.0,
    )
    # Sitio 2: Unifamiliar (casas)
    site_single = Site(
        id="SITE_SINGLE",
        name="Casas Villa Devoto",
        zone="residential_singlefamily",
        latitude=-34.60,
        longitude=-58.51,
        demand_base=1.0,
    )

    topology = SimulationTopology(
        sites=[site_multi, site_single],
        containers=[
            Container("C_MULTI", "SITE_MULTI", "Contenedor Multi", "residuos", 145),
            Container("C_SINGLE", "SITE_SINGLE", "Contenedor Single", "residuos", 145),
        ],
        devices=[
            Device("D_MULTI", "C_MULTI"),
            Device("D_SINGLE", "C_SINGLE"),
        ],
        initial_levels={"C_MULTI": 0.0, "C_SINGLE": 0.0},
    )

    simulator = SyntheticDataSimulator(
        ScenarioConfig(seed=42, periods=1, reading_jitter_minutes=0),
        topology=topology,
    )

    # Simular un Domingo a las 14:00 (weekday=6, hour=14)
    # En domingo a la tarde, los residentes de casas unifamiliares generan residuos
    # continuamente (factor 0.85 * 1.2), mientras que en edificios multifamiliares
    # el encargado no trabaja el domingo (factor 0.55 * 1.0).
    domingo_14hs = datetime(2026, 9, 13, 14, 0)  # 2026-09-13 es Domingo
    res = simulator.run_tick(domingo_14hs)

    levels = {m.container_id: m.fill_level_pct for m in res.measurements}

    # El contenedor unifamiliar debe haber acumulado significativamente más generación
    assert levels["C_SINGLE"] > levels["C_MULTI"]


def test_zone_classifier_calibration_loading():
    classifier = get_zone_classifier()
    assert hasattr(classifier, "calibration_target")
    assert hasattr(classifier, "calibration_tolerance_pct")
    assert 0.8 <= classifier.calibration_target <= 1.2
    assert 0.0 <= classifier.calibration_tolerance_pct <= 50.0


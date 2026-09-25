from datetime import datetime

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
            Container(
                id="C_MULTI",
                site_id="SITE_MULTI",
                name="Contenedor Multi",
                waste_type="residuos",
                height_cm=145,
            ),
            Container(
                id="C_SINGLE",
                site_id="SITE_SINGLE",
                name="Contenedor Single",
                waste_type="residuos",
                height_cm=145,
            ),
        ],
        devices=[
            Device(id="D_MULTI", container_id="C_MULTI"),
            Device(id="D_SINGLE", container_id="C_SINGLE"),
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


def _build_multi_single_topology() -> SimulationTopology:
    site_multi = Site(
        id="SITE_MULTI",
        name="Edificios Palermo",
        zone="residential_multifamily",
        latitude=-34.58,
        longitude=-58.42,
        demand_base=1.0,
    )
    site_single = Site(
        id="SITE_SINGLE",
        name="Casas Villa Devoto",
        zone="residential_singlefamily",
        latitude=-34.60,
        longitude=-58.51,
        demand_base=1.0,
    )
    return SimulationTopology(
        sites=[site_multi, site_single],
        containers=[
            Container(
                id="C_MULTI",
                site_id="SITE_MULTI",
                name="Contenedor Multi",
                waste_type="residuos",
                height_cm=145,
            ),
            Container(
                id="C_SINGLE",
                site_id="SITE_SINGLE",
                name="Contenedor Single",
                waste_type="residuos",
                height_cm=145,
            ),
        ],
        devices=[
            Device(id="D_MULTI", container_id="C_MULTI"),
            Device(id="D_SINGLE", container_id="C_SINGLE"),
        ],
        initial_levels={"C_MULTI": 0.0, "C_SINGLE": 0.0},
    )


def test_zone_multiplier_override_composes_over_automatic_zone_classifier():
    """
    Regression guard: el override manual por zona (el que manda worker.py desde
    los zone_overrides del front) debe MULTIPLICAR sobre la base automática de
    ZoneClassifier, no reemplazarla — una zona sin override sigue diferenciándose
    por su zone_type/curva horaria, y una zona con override ×2 debe dar
    exactamente el doble de lo que daría sin override (no un valor absoluto).
    """
    tick_time = datetime(2026, 9, 13, 14, 0)  # domingo 14hs, mismo tick que arriba

    baseline_sim = SyntheticDataSimulator(
        ScenarioConfig(seed=42, periods=1, reading_jitter_minutes=0),
        topology=_build_multi_single_topology(),
    )
    baseline = {
        m.container_id: m.fill_level_pct
        for m in baseline_sim.run_tick(tick_time).measurements
    }

    override_sim = SyntheticDataSimulator(
        ScenarioConfig(seed=42, periods=1, reading_jitter_minutes=0),
        topology=_build_multi_single_topology(),
    )
    overrides = {"residential_multifamily": 2.0}
    overridden = {
        m.container_id: m.fill_level_pct
        for m in override_sim.run_tick(
            tick_time, zone_multiplier=lambda zone: overrides.get(zone, 1.0)
        ).measurements
    }

    # La zona con override ×2 debe dar el doble de la base automática (composición
    # multiplicativa, no un valor absoluto que reemplace la base). Tolerancia
    # relajada porque engine.py redondea los increments a 4 decimales por tick.
    assert overridden["C_MULTI"] == pytest.approx(baseline["C_MULTI"] * 2.0, rel=1e-3)

    # La zona sin override configurado ('residential_singlefamily' no está en el
    # dict) debe quedar intacta, no aplastada a un multiplicador plano de 1.0.
    assert overridden["C_SINGLE"] == pytest.approx(baseline["C_SINGLE"], rel=1e-6)


def test_zone_classifier_calibration_loading():
    classifier = get_zone_classifier()
    assert hasattr(classifier, "calibration_target")
    assert hasattr(classifier, "calibration_tolerance_pct")
    assert 0.8 <= classifier.calibration_target <= 1.2
    assert 0.0 <= classifier.calibration_tolerance_pct <= 50.0

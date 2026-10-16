import csv
from datetime import datetime

import pytest

from simulator.domain.entities import (
    Container,
    Device,
    Site,
)
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import SimulationTopology
from simulator.zone_classifier import (
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

    # Búsqueda por nombre de barrio (la usa la topología sintética del CLI)
    profile_palermo = classifier.get_profile("Palermo")
    assert profile_palermo.barrio == "PALERMO"
    assert profile_palermo != DEFAULT_PROFILE


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
            tick_time, neighborhood_multiplier=lambda name: overrides.get(name, 1.0)
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


def _classifier_with_radios(tmp_path, rows):
    path = tmp_path / "land_use_by_radio.csv"
    fields = [
        "radio_code",
        "barrio",
        "zone_type",
        "res_multifamily_pct",
        "res_singlefamily_pct",
        "commercial_pct",
        "office_pct",
        "industrial_pct",
        "demand_multiplier",
        "weekend_factor",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, 0) for field in fields})
    return ZoneClassifier(land_use_radio_csv=path)


def _week(profile):
    return [[profile.effective_multiplier(h, d) for h in range(24)] for d in range(7)]


def test_each_radio_blends_the_curves_of_its_land_uses(tmp_path):
    # La curva horaria y semanal de cada radio es la mezcla de las curvas de
    # sus tipos de uso, ponderada por % de parcelas x basura que genera cada
    # tipo; el volumen semanal sigue siendo su demand_multiplier.
    classifier = _classifier_with_radios(
        tmp_path,
        [
            {
                "radio_code": "puro",
                "barrio": "X",
                "zone_type": "commercial",
                "commercial_pct": 100,
                "demand_multiplier": 1.4,
                "weekend_factor": 1.2,
            },
            {
                "radio_code": "mixto",
                "barrio": "X",
                "zone_type": "commercial",
                "commercial_pct": 50,
                "res_multifamily_pct": 50,
                "demand_multiplier": 1.25,
                "weekend_factor": 0.9,
            },
        ],
    )
    commercial = classifier.get_profile("commercial")
    multifamily = classifier.get_profile("residential_multifamily")
    puro = classifier.get_profile("puro")
    mixto = classifier.get_profile("mixto")

    # El radio puro tiene exactamente la curva de su tipo.
    for d in range(7):
        for h in range(24):
            assert puro.effective_multiplier(h, d) / 1.4 == pytest.approx(
                commercial.effective_multiplier(h, d) / commercial.demand_multiplier
            )

    # El mixto mezcla: 50% x 1,4 (comercial) contra 50% x 1,1 (multifamiliar).
    w_comm = 0.5 * 1.4 / (0.5 * 1.4 + 0.5 * 1.1)
    for d, h in ((1, 8), (5, 13), (6, 21)):
        expected = (
            w_comm
            * commercial.effective_multiplier(h, d)
            / commercial.demand_multiplier
            + (1 - w_comm)
            * multifamily.effective_multiplier(h, d)
            / multifamily.demand_multiplier
        )
        assert mixto.effective_multiplier(h, d) / 1.25 == pytest.approx(expected)

    # Media semanal = demand_multiplier (la calibración global no cambia).
    for profile, dm in ((puro, 1.4), (mixto, 1.25)):
        week = _week(profile)
        assert sum(map(sum, week)) / (7 * 24) == pytest.approx(dm)

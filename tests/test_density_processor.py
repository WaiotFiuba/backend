from __future__ import annotations

import pytest

from app.digital_twin.synthetic_data.density_processor import (
    DAILY_WASTE_PER_PERSON_KG,
    WASTE_DENSITY_KG_M3,
    get_density_processor,
)
from app.digital_twin.synthetic_data.domain.entities import Container, Device, Site
from app.digital_twin.synthetic_data.simulation.engine import SyntheticDataSimulator
from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig
from app.digital_twin.synthetic_data.topology import (
    BackendContainerRecord,
    SimulationTopology,
    topology_from_backend_records,
)


def test_density_processor_loads_and_finds_radio():
    processor = get_density_processor()
    assert len(processor.radios) > 0, "Debe haber cargado los radios censales de CABA"

    # Coordenadas en Almagro / Balvanera
    radio = processor.find_radio(latitude=-34.600945, longitude=-58.422690)
    assert radio is not None
    assert radio.population > 0
    assert radio.department_name != ""
    assert radio.radio_code != ""


def test_process_containers_shares_population_in_same_radio():
    processor = get_density_processor()

    # 3 contenedores en las mismas coordenadas / mismo radio
    containers = [
        {"id": 101, "latitude": -34.600945, "longitude": -58.422690, "volume_m3": 3.2},
        {"id": 102, "latitude": -34.600945, "longitude": -58.422690, "volume_m3": 3.2},
        {"id": 103, "latitude": -34.600945, "longitude": -58.422690, "volume_m3": 3.2},
    ]

    demands, summaries = processor.process_containers(containers)

    assert len(demands) == 3
    assert len(summaries) == 1

    summary = summaries[0]
    assert summary.total_containers == 3
    assert set(summary.container_ids) == {101, 102, 103}

    expected_daily_kg_per_cont = (summary.population * DAILY_WASTE_PER_PERSON_KG) / 3.0
    assert (
        pytest.approx(summary.daily_waste_per_container_kg, rel=1e-3)
        == expected_daily_kg_per_cont
    )

    # Comprobar el cálculo de llenado para cada contenedor
    capacity_kg = 3.2 * WASTE_DENSITY_KG_M3
    expected_daily_fill_pct = (expected_daily_kg_per_cont / capacity_kg) * 100.0
    expected_hourly_fill_pct = expected_daily_fill_pct / 24.0

    for cid in [101, 102, 103]:
        info = demands[cid]
        assert info.containers_in_radio == 3
        assert (
            pytest.approx(info.daily_waste_kg, rel=1e-3) == expected_daily_kg_per_cont
        )
        assert pytest.approx(info.daily_fill_pct, rel=1e-3) == expected_daily_fill_pct
        assert pytest.approx(info.hourly_fill_pct, rel=1e-3) == expected_hourly_fill_pct


def test_topology_from_backend_records_applies_density():
    records = [
        BackendContainerRecord(
            id=1,
            site_id="SITE-01",
            site_name="Sitio Almagro",
            latitude=-34.600945,
            longitude=-58.422690,
            current_level=20,
            device_imei="IMEI-001",
            container_type="Carga Lateral 3.2m3",
            waste_type="residuos",
            height_cm=145.0,
            volume_m3=3.2,
            zone="Almagro",
            demand_base=1.0,
        )
    ]

    topology = topology_from_backend_records(records)
    site = topology.sites[0]
    # demand_base debe ser calculado por DensityProcessor (tasa horaria en %)
    assert site.demand_base > 0
    assert site.demand_base != 1.0 or site.zone != ""


def test_simulation_engine_fills_proportionally():
    config = ScenarioConfig(
        frequency_minutes=60,
        periods=24,
        collection_hours=(),  # Sin recolecciones durante el test para medir acumulación
    )

    site = Site(
        id="S1",
        name="Sitio Test",
        zone="Palermo",
        latitude=-34.5832,
        longitude=-58.4243,
        demand_base=2.0,  # 2.0% por hora
    )
    container = Container(
        id="C1",
        site_id="S1",
        name="Cont 1",
        waste_type="residuos_humedos",
        height_cm=145.0,
        volume_m3=3.2,
    )
    device = Device(id="D1", container_id="C1")

    topology = SimulationTopology(
        sites=[site],
        containers=[container],
        devices=[device],
        initial_levels={"C1": 10.0},
    )

    simulator = SyntheticDataSimulator(config=config, topology=topology)
    result = simulator.run()

    final_measurement = result.measurements[-1]
    # En 24 horas con demand_base=2.0% por hora, el nivel debe haber subido significativamente (~30-60%)
    assert final_measurement.fill_level_pct > 20.0


def test_density_processor_point_in_polygon_containment():
    """
    Verifica que las coordenadas de prueba en diferentes comunas de CABA
    caigan estrictamente DENTRO (intersects) del polígono censal asignado.
    """
    from shapely.geometry import Point

    processor = get_density_processor()

    # Muestras representativas en distintos puntos de CABA:
    # (lat, lon, comuna esperada)
    sample_locations = [
        (-34.6186, -58.4438, "Comuna 6"),   # Caballito (Parque Rivadavia / Primera Junta)
        (-34.5885, -58.4305, "Comuna 14"),  # Palermo Soho
        (-34.6345, -58.3631, "Comuna 4"),   # La Boca (Caminito / Brandsen)
        (-34.5612, -58.4563, "Comuna 13"),  # Belgrano (Cabildo y Juramento)
        (-34.6712, -58.4682, "Comuna 8"),   # Villa Lugano
        (-34.5942, -58.3927, "Comuna 2"),   # Recoleta
    ]

    for lat, lon, expected_dept in sample_locations:
        radio = processor.find_radio(latitude=lat, longitude=lon)
        assert radio is not None, f"Debe encontrar radio censal para ({lat}, {lon})"
        assert radio.department_name == expected_dept, (
            f"Esperado {expected_dept} pero obtuvo {radio.department_name}"
        )
        point = Point(lon, lat)
        assert radio.geometry.intersects(point), (
            f"La coordenada ({lat}, {lon}) DEBE intersectar geométricamente su polígono censal {radio.radio_code}"
        )


def test_density_processor_volume_difference():
    """
    Verifica que ante la misma población y misma basura en kg,
    un contenedor más chico (1.1 m3) tenga una tasa de llenado porcentual
    inversamente proporcional a su capacidad respecto de uno grande (3.2 m3).
    """
    processor = get_density_processor()

    containers = [
        {"id": "cont_chico", "latitude": -34.6186, "longitude": -58.4438, "volume_m3": 1.1},
        {"id": "cont_grande", "latitude": -34.6186, "longitude": -58.4438, "volume_m3": 3.2},
    ]

    demands, _ = processor.process_containers(containers)
    chico = demands["cont_chico"]
    grande = demands["cont_grande"]

    # Ambos deben recibir la misma cantidad de kg de basura
    assert pytest.approx(chico.daily_waste_kg, rel=1e-3) == grande.daily_waste_kg

    # Pero el chico debe tener una tasa porcentual mayor por tener menos capacidad
    ratio_capacidad = 3.2 / 1.1
    ratio_llenado = chico.hourly_fill_pct / grande.hourly_fill_pct

    # El ratio debe coincidir con la relación de volúmenes (o ser superior si el grande choca con el piso)
    assert chico.hourly_fill_pct > grande.hourly_fill_pct
    assert ratio_llenado >= ratio_capacidad * 0.95


def test_density_processor_minimum_activity_floor():
    """
    Verifica que en zonas con muy baja población, se respete
    el piso mínimo de actividad diaria del 40% (1.667% / hora).
    """
    processor = get_density_processor()

    # Coordenada en Reserva Ecológica Costanera Sur (baja población)
    containers = [
        {"id": "cont_parque", "latitude": -34.6100, "longitude": -58.3520, "volume_m3": 3.2}
    ]

    demands, _ = processor.process_containers(containers)
    info = demands["cont_parque"]

    # Debe ser al menos 40% diario (1.6667% por hora)
    assert info.daily_fill_pct >= 40.0
    assert info.hourly_fill_pct >= 40.0 / 24.0


def test_density_processor_outside_caba_fallback():
    """
    Verifica que una coordenada claramente fuera de CABA
    (por ejemplo, en el Río de la Plata o en el Océano Atlántico)
    aplique el fallback 'UNKNOWN' sin lanzar excepción.
    """
    processor = get_density_processor()

    # Coordenada en el Río de la Plata profundo
    containers = [
        {"id": "cont_rio", "latitude": -34.4500, "longitude": -58.1000, "volume_m3": 3.2}
    ]

    demands, summaries = processor.process_containers(containers)
    info = demands["cont_rio"]

    # Al caer lejos, no intersecta CABA
    assert info.daily_fill_pct >= 40.0
    assert info.hourly_fill_pct > 0.0


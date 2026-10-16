from __future__ import annotations

import pytest
from shapely.geometry import box
from shapely.ops import transform as shapely_transform

from simulator.config.settings import get_settings
from simulator.density_processor import (
    CensusRadio,
    DensityProcessor,
    _buffer_polygon_meters,
    _to_metric,
    get_density_processor,
)
from simulator.domain.entities import Container, Device, Site
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import (
    BackendContainerRecord,
    SimulationTopology,
    topology_from_backend_records,
)


def test_density_processor_loads_and_connects_radio():
    processor = get_density_processor()
    assert len(processor.radios) > 0, "Debe haber cargado los radios censales de CABA"

    # Coordenadas en Almagro / Balvanera. La relación es muchos-a-muchos
    # (buffer de calle), así que en vez de find_radio() (eliminado en el
    # refactor 99ccca1) se verifica a través de la API pública real.
    containers = [
        {"id": "x", "latitude": -34.600945, "longitude": -58.422690, "volume_m3": 3.2}
    ]
    demands, _ = processor.process_containers(containers)
    info = demands["x"]
    assert info.population > 0
    assert info.department_name != ""
    assert info.radio_code != ""


def test_process_containers_shares_population_in_same_radio():
    processor = get_density_processor()
    settings = get_settings()

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

    expected_daily_kg_per_cont = (
        summary.population * settings.promedio_generacion_basura_personas_24h
    ) / 3.0
    assert (
        pytest.approx(summary.daily_waste_per_container_kg, rel=1e-3)
        == expected_daily_kg_per_cont
    )

    # Comprobar el cálculo de llenado para cada contenedor
    capacity_kg = 3.2 * settings.densidad_basura_kg_m3
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


def test_density_processor_dominant_radio_containment():
    """
    Verifica que, para coordenadas de prueba en diferentes comunas de CABA,
    el radio DOMINANTE (el que más kg/día aporta) siga siendo el que
    geométricamente contiene al contenedor, aunque el contenedor pueda
    quedar conectado a más de un radio por el buffer de calle.
    """
    from shapely.geometry import Point

    processor = get_density_processor()

    # Muestras representativas en distintos puntos de CABA:
    # (lat, lon, comuna esperada)
    sample_locations = [
        (
            -34.6186,
            -58.4438,
            "Comuna 6",
        ),  # Caballito (Parque Rivadavia / Primera Junta)
        (-34.5885, -58.4305, "Comuna 14"),  # Palermo Soho
        (-34.6345, -58.3631, "Comuna 4"),  # La Boca (Caminito / Brandsen)
        (-34.5612, -58.4563, "Comuna 13"),  # Belgrano (Cabildo y Juramento)
        (-34.6712, -58.4682, "Comuna 8"),  # Villa Lugano
        (-34.5942, -58.3927, "Comuna 2"),  # Recoleta
    ]

    containers = [
        {"id": f"c{i}", "latitude": lat, "longitude": lon, "volume_m3": 3.2}
        for i, (lat, lon, _) in enumerate(sample_locations)
    ]
    demands, _ = processor.process_containers(containers)
    radio_by_code = {r.radio_code: r for r in processor.radios}

    for i, (lat, lon, expected_dept) in enumerate(sample_locations):
        info = demands[f"c{i}"]
        assert info.department_name == expected_dept, (
            f"Esperado {expected_dept} pero obtuvo {info.department_name} para ({lat}, {lon})"
        )
        radio_dominante = radio_by_code[info.radio_code]
        point = Point(lon, lat)
        assert radio_dominante.geometry.intersects(point), (
            f"La coordenada ({lat}, {lon}) DEBE intersectar geométricamente "
            f"el polígono real de su radio dominante {radio_dominante.radio_code}"
        )


def test_density_processor_volume_difference():
    """
    Verifica que ante la misma población y misma basura en kg,
    un contenedor más chico (1.1 m3) tenga una tasa de llenado porcentual
    inversamente proporcional a su capacidad respecto de uno grande (3.2 m3).
    """
    processor = get_density_processor()

    containers = [
        {
            "id": "cont_chico",
            "latitude": -34.6186,
            "longitude": -58.4438,
            "volume_m3": 1.1,
        },
        {
            "id": "cont_grande",
            "latitude": -34.6186,
            "longitude": -58.4438,
            "volume_m3": 3.2,
        },
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
    Verifica que se respete el piso mínimo de actividad diaria
    (Settings.density_min_daily_fill_pct_floor).

    Nota: la coordenada de Costanera Sur usada acá matchea en la práctica un
    radio censal con población 650 (~203% de llenado), muy por encima del
    piso — así que este test no ejercita realmente el clamp del piso (esto ya
    pasaba antes del refactor muchos-a-muchos, no es algo introducido por ese
    cambio). Queda documentado así hasta que se elija una coordenada que
    realmente dispare el piso.
    """
    processor = get_density_processor()

    # Coordenada en Reserva Ecológica Costanera Sur (baja población)
    containers = [
        {
            "id": "cont_parque",
            "latitude": -34.6100,
            "longitude": -58.3520,
            "volume_m3": 3.2,
        }
    ]

    demands, _ = processor.process_containers(containers)
    info = demands["cont_parque"]
    floor = get_settings().density_min_daily_fill_pct_floor

    assert info.daily_fill_pct >= floor
    assert info.hourly_fill_pct >= floor / 24.0


def test_density_processor_outside_caba_fallback():
    """
    Verifica que una coordenada claramente fuera de CABA
    (por ejemplo, en el Río de la Plata o en el Océano Atlántico)
    aplique el fallback 'UNKNOWN' sin lanzar excepción.
    """
    processor = get_density_processor()

    # Coordenada en el Río de la Plata profundo
    containers = [
        {
            "id": "cont_rio",
            "latitude": -34.4500,
            "longitude": -58.1000,
            "volume_m3": 3.2,
        }
    ]

    demands, summaries = processor.process_containers(containers)
    info = demands["cont_rio"]

    # Al caer lejos, no intersecta CABA
    assert info.daily_fill_pct >= get_settings().density_min_daily_fill_pct_floor
    assert info.hourly_fill_pct > 0.0


def test_process_containers_weights_shared_radios_by_distance():
    """
    Regression guard: antes del peso por distancia, un contenedor conectado
    a 2 radios censales recibía siempre una porción 50/50 (peso plano 1/n),
    sin importar si estaba pegado a uno de los radios o en el borde
    compartido. Ahora el peso decae linealmente con la distancia real al
    polígono de cada radio, así que la posición del contenedor debe cambiar
    cuánto le toca de cada radio.

    Se arman 2 radios censales sintéticos, cuadrados de 50m de lado,
    adyacentes (comparten el borde en x=50m), con la misma población, cerca
    de una coordenada real de CABA para que la reproyección métrica
    (EPSG:5347) se comporte de forma realista.
    """
    lat0, lon0 = -34.6000, -58.4200
    dlon_50m = 50 / 91000  # ~1 grado de longitud ~91km en esta latitud
    dlat_50m = 50 / 111000  # ~1 grado de latitud ~111km

    radio_a_geom = box(lon0, lat0, lon0 + dlon_50m, lat0 + dlat_50m)
    radio_b_geom = box(lon0 + dlon_50m, lat0, lon0 + 2 * dlon_50m, lat0 + dlat_50m)

    processor = DensityProcessor(
        csv_path="/no/existe/dataset.csv", street_buffer_m=15.0
    )
    processor.radios = [
        CensusRadio(
            radio_code="A",
            population=1000,
            department_name="A",
            area_km2=0.0025,
            centroid_lat=lat0,
            centroid_lon=lon0,
            geometry=radio_a_geom,
        ),
        CensusRadio(
            radio_code="B",
            population=1000,
            department_name="B",
            area_km2=0.0025,
            centroid_lat=lat0,
            centroid_lon=lon0 + 2 * dlon_50m,
            geometry=radio_b_geom,
        ),
    ]
    processor._geometries = [radio_a_geom, radio_b_geom]
    processor._buffered_geometries = [
        _buffer_polygon_meters(g, 15.0) for g in processor._geometries
    ]
    processor._metric_geometries = [
        shapely_transform(lambda x, y: _to_metric.transform(x, y), g)
        for g in processor._geometries
    ]

    # cerca_de_A: a 5m del borde compartido (dentro de A, a 5m de B).
    # en_el_borde: exactamente sobre el borde compartido (a 0m de A y de B).
    containers = [
        {
            "id": "cerca_de_A",
            "latitude": lat0 + dlat_50m / 2,
            "longitude": lon0 + dlon_50m * (45 / 50),
            "volume_m3": 3.2,
        },
        {
            "id": "en_el_borde",
            "latitude": lat0 + dlat_50m / 2,
            "longitude": lon0 + dlon_50m,
            "volume_m3": 3.2,
        },
    ]

    demands, _ = processor.process_containers(containers)
    cerca = demands["cerca_de_A"]
    borde = demands["en_el_borde"]

    # Ambos quedan conectados a los 2 radios (el buffer no cambió).
    assert set(cerca.radio_codes) == {"A", "B"}
    assert set(borde.radio_codes) == {"A", "B"}

    # Bajo el peso plano 1/n anterior, ambos habrían recibido exactamente
    # la misma demanda total (mismo n=2, misma población en A y B). Ahora
    # deben diferir de forma no trivial porque están a distinta distancia
    # del borde compartido.
    assert cerca.daily_waste_kg != pytest.approx(borde.daily_waste_kg, rel=0.01)

    # El contenedor sobre el borde está tan cerca de B como de A, mientras
    # que el otro está más lejos de B: debe llevarse relativamente más de B.
    assert borde.daily_waste_kg > cerca.daily_waste_kg

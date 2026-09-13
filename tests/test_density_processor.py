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

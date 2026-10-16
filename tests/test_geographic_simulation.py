from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from app.commands.import_neighborhood_demographics import (
    _read_populations,
    density_factor,
    normalize_neighborhood,
)
from simulator.domain.entities import Container, Device, Site
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import SimulationTopology
from app.services.simulation_session_service import effective_multiplier


def test_density_factor_is_normalized_and_clamped():
    assert density_factor(10_000, 10_000) == 1.0
    assert density_factor(100_000, 10_000) == 2.0
    assert density_factor(100, 10_000) == 0.505


def test_neighborhood_normalization_ignores_case_and_accents():
    assert normalize_neighborhood("  Constitución ") == "constitucion"


def test_population_csv_only_requires_neighborhood_and_population():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "poblacion_barrios.csv"
        path.write_text("barrio,poblacion\nPalermo,225970\n", encoding="utf-8")

        populations = _read_populations(path)

    record = populations["palermo"]
    assert record.population == 225970
    assert record.year == 2010
    assert record.source == "Censo 2010"


def test_effective_multiplier_interpolates_in_simulated_time():
    started = datetime(2026, 1, 1, 0, 0)
    ends = started + timedelta(minutes=60)

    assert effective_multiplier(1, 2, started, started, ends) == 1
    assert (
        effective_multiplier(1, 2, started + timedelta(minutes=30), started, ends)
        == 1.5
    )
    assert effective_multiplier(1, 2, ends, started, ends) == 2


def test_incremental_engine_preserves_state_and_applies_geographic_demand():
    low_site = Site(
        id="LOW",
        name="Low",
        zone="Low",
        latitude=-34.6,
        longitude=-58.4,
        demand_base=0.5,
    )
    high_site = Site(
        id="HIGH",
        name="High",
        zone="High",
        latitude=-34.61,
        longitude=-58.41,
        demand_base=2.0,
    )
    containers = [
        Container(
            id="1",
            site_id="LOW",
            name="Low container",
            waste_type="residuos_humedos",
            height_cm=150,
        ),
        Container(
            id="2",
            site_id="HIGH",
            name="High container",
            waste_type="residuos_humedos",
            height_cm=150,
        ),
    ]
    topology = SimulationTopology(
        sites=[low_site, high_site],
        containers=containers,
        devices=[
            Device(id="D1", container_id="1"),
            Device(id="D2", container_id="2"),
        ],
        initial_levels={"1": 10, "2": 10},
    )
    simulator = SyntheticDataSimulator(
        ScenarioConfig(seed=5, periods=2, reading_jitter_minutes=0),
        topology=topology,
    )

    first = simulator.run_tick(datetime(2026, 1, 1, 12, 0))
    second = simulator.run_tick(datetime(2026, 1, 1, 13, 0))

    first_levels = {
        item.container_id: item.fill_level_pct for item in first.measurements
    }
    second_levels = {
        item.container_id: item.fill_level_pct for item in second.measurements
    }
    assert first_levels["2"] > first_levels["1"]
    assert second_levels["1"] > first_levels["1"]
    assert second_levels["2"] > first_levels["2"]

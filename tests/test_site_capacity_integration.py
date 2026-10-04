from app.digital_twin.synthetic_data.generators.topology import (
    generate_synthetic_topology,
)
from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig
from app.schemas.map.optimization import OptimizationConfig, SiteUtilizationMetric
from app.services.map.optimization_service import _solve_greedy
from app.services.map.site_capacity_service import get_site_capacity_service
import random


def test_site_capacity_service_evaluation():
    service = get_site_capacity_service()

    # Coordenadas en CABA (Arias 3450)
    max_c, puede = service.evaluate_site(-34.545914, -58.483065, current_containers=1)
    assert max_c > 0
    assert puede is True

    # Coordenadas saturadas
    max_c, puede = service.evaluate_site(
        -34.545914, -58.483065, current_containers=max_c
    )
    assert puede is False


def test_simulation_topology_assigns_max_containers():
    rng = random.Random(42)
    config = ScenarioConfig()
    topology = generate_synthetic_topology(config, rng)

    assert len(topology.sites) > 0
    for site in topology.sites:
        assert hasattr(site, "max_containers")
        assert site.max_containers > 0
        assert hasattr(site, "puede_ingresar")
        assert isinstance(site.puede_ingresar, bool)


def test_redistribution_comparison_with_and_without_capacity_constraints():
    # Donante con 5 contenedores
    donor = SiteUtilizationMetric(
        site_id=1,
        site_name="Sitio Donante",
        latitude=-34.6000,
        longitude=-58.4000,
        container_count=5,
        max_containers=10,
        puede_ingresar=True,
        waste_type_id=1,
        container_type_id=1,
        avg_fill_level=20.0,
        peak_fill_rate=0.0,
        overflow_frequency=0,
        utilization_score=0.15,
        category="idle",
    )

    # Receptor crítico saturado (max_containers = 2, container_count = 2)
    receiver_saturado = SiteUtilizationMetric(
        site_id=2,
        site_name="Sitio Receptor Saturado",
        latitude=-34.6010,
        longitude=-58.4010,
        container_count=2,
        max_containers=2,
        puede_ingresar=False,
        waste_type_id=1,
        container_type_id=1,
        avg_fill_level=95.0,
        peak_fill_rate=0.9,
        overflow_frequency=10,
        utilization_score=0.95,
        category="critical",
    )

    donors = [donor]
    receivers = [receiver_saturado]
    container_type_map = {101: 1, 102: 1, 103: 1}
    site_info = {1: donor, 2: receiver_saturado}

    # 1. EJECUCIÓN CON RESTRICCIONES ACTIVADAS (apply_capacity_constraints = True)
    config_con_restricciones = OptimizationConfig(
        window_days=7,
        max_distance_km=5.0,
        min_utilization_donor=0.35,
        min_utilization_receiver=0.70,
        target_utilization=0.60,
        algorithm="greedy",
        apply_capacity_constraints=True,
    )

    demand_con = {}
    for r in receivers:
        cupo_libre = (
            max(0, r.max_containers - r.container_count)
            if config_con_restricciones.apply_capacity_constraints
            else 99
        )
        if cupo_libre > 0:
            demand_con[r.site_id] = min(2, cupo_libre)

    moves_con = _solve_greedy(
        donors,
        receivers,
        {1: 2},
        demand_con,
        config_con_restricciones,
        {1: [101, 102]},
        container_type_map,
        site_info,
    )

    # Con restricciones activadas: 0 movimientos hacia el sitio saturado
    assert len(moves_con) == 0

    # 2. EJECUCIÓN SIN RESTRICCIONES (apply_capacity_constraints = False)
    config_sin_restricciones = OptimizationConfig(
        window_days=7,
        max_distance_km=5.0,
        min_utilization_donor=0.35,
        min_utilization_receiver=0.70,
        target_utilization=0.60,
        algorithm="greedy",
        apply_capacity_constraints=False,
    )

    demand_sin = {2: 2}  # Demanda estadística pura sin límite físico

    moves_sin = _solve_greedy(
        donors,
        receivers,
        {1: 2},
        demand_sin,
        config_sin_restricciones,
        {1: [101, 102]},
        container_type_map,
        site_info,
    )

    # Sin restricciones: Le asigna 2 contenedores al sitio saturado
    assert len(moves_sin) == 2
    assert all(m.to_site_id == 2 for m in moves_sin)


def test_site_capacity_service_inactive_when_file_missing(tmp_path):
    from app.services.map.site_capacity_service import SiteCapacityService

    inexistent_csv = str(tmp_path / "inexistent.csv")
    svc = SiteCapacityService(csv_path=inexistent_csv)

    assert svc.is_active is False
    cap, puede = svc.evaluate_site(-34.5459, -58.4830, current_containers=5)
    assert cap is None
    assert puede is True


def test_standalone_capacity_json_serializability():
    import json
    from scripts.calcular_capacidad_sitios import StandaloneSiteCapacityCalculator

    calc = StandaloneSiteCapacityCalculator()
    sitios = [
        {
            "id": 1,
            "name": "Sitio Arias 3450",
            "latitude": -34.545914,
            "longitude": -58.483065,
            "contenedores_actuales": 2,
        },
        {
            "id": 2,
            "name": "Sitio Vacio",
            "latitude": -34.545914,
            "longitude": -58.483065,
            "contenedores_actuales": 0,
        },
    ]

    resultado = calc.calcular_capacidad_sitios(sitios)
    # Debe serializar a JSON sin error (sin int64 de numpy no serializable)
    json_str = json.dumps(resultado)
    assert json_str is not None

    # Sitio con 2 de 2 contenedores -> no puede colocar más
    assert resultado[0]["puede_colocar_mas"] is False
    assert resultado[0]["cupo_disponible"] == 0

    # Sitio con 0 de 2 contenedores -> sí puede colocar más
    assert resultado[1]["puede_colocar_mas"] is True
    assert resultado[1]["cupo_disponible"] == 2

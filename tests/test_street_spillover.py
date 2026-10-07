from __future__ import annotations

import unittest
from datetime import datetime

from simulator.domain.entities import (
    Container,
    Device,
    Site,
)
from simulator.geo.addresses import (
    normalize_street_name,
    parse_street_address,
)
from simulator.simulation.opposing_sites import build_opposing_sites_map
from simulator.simulation.engine import (
    SyntheticDataSimulator,
)
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import SimulationTopology


class TestStreetPairingAndSpillover(unittest.TestCase):
    def test_parse_street_address(self):
        # 1. Dirección con "AV." y número
        self.assertEqual(
            parse_street_address("AV. CORRIENTES 1234"),
            ("CORRIENTES", 1234),
        )
        # 2. Dirección con número impar y formato invertido
        self.assertEqual(
            parse_street_address("CORRIENTES AV. 1235"),
            ("CORRIENTES", 1235),
        )
        # 3. Dirección con prefijo "Sitio ..."
        self.assertEqual(
            parse_street_address("Sitio RSU - CALLAO 520"),
            ("CALLAO", 520),
        )
        # 4. Nombre de calle compuesto
        self.assertEqual(
            parse_street_address("SAN MARTIN 450"),
            ("SAN MARTIN", 450),
        )
        # 5. Sin número
        self.assertIsNone(parse_street_address("CORRIENTES"))
        # 6. None o vacío
        self.assertIsNone(parse_street_address(None))

    def test_normalize_street_name_matches_dirty_route_segment_names(self):
        # Nombres de tramo de ruta reales del CSV de circuitos (sin altura
        # embebida) deben normalizar igual que la dirección de un contenedor
        # sobre la misma calle.
        self.assertEqual(normalize_street_name("Corrientes Av."), "CORRIENTES")
        self.assertEqual(
            parse_street_address("AV. CORRIENTES 1234")[0],
            normalize_street_name("Corrientes Av."),
        )
        self.assertEqual(normalize_street_name("Mitre, Bartolome"), "MITRE BARTOLOME")
        self.assertEqual(
            parse_street_address("MITRE BARTOLOME 500")[0],
            normalize_street_name("Mitre, Bartolome"),
        )
        # Sin texto util tras limpiar -> None (igual que parse_street_address)
        self.assertIsNone(normalize_street_name(""))
        self.assertIsNone(normalize_street_name(None))

    def test_ordinal_degree_and_masculine_sign_are_equivalent(self):
        # Los datos reales usan "°" (grado) y "º" (ordinal masculino)
        # indistintamente para lo mismo (ej. "Cabo 2°" vs "Cabo 2º").
        self.assertEqual(
            normalize_street_name("Lopez, Jorge Eduardo, Cabo 2°"),
            normalize_street_name("Lopez, Jorge Eduardo, Cabo 2º"),
        )

    def test_build_opposing_sites_map(self):
        # Dos sitios en la misma calle pero veredas opuestas (par vs. impar)
        site_even = Site(
            id="site-even",
            name="Sitio Par",
            zone="Palermo",
            latitude=-34.5800,
            longitude=-58.4200,
            demand_base=1.0,
            address="AV. SANTA FE 3000",  # Par
        )
        site_odd = Site(
            id="site-odd",
            name="Sitio Impar",
            zone="Palermo",
            latitude=-34.5801,
            longitude=-58.4201,  # A aprox 15 metros
            demand_base=1.0,
            address="AV. SANTA FE 3005",  # Impar
        )
        site_distant = Site(
            id="site-distant",
            name="Sitio Lejano",
            zone="Palermo",
            latitude=-34.5850,
            longitude=-58.4250,  # A más de 500m
            demand_base=1.0,
            address="AV. SANTA FE 3007",  # Impar pero lejos
        )
        site_other_street = Site(
            id="site-other",
            name="Sitio Otra Calle",
            zone="Palermo",
            latitude=-34.5800,
            longitude=-58.4200,
            demand_base=1.0,
            address="CALLAO 3000",  # Otra calle
        )

        sites = [site_even, site_odd, site_distant, site_other_street]
        opposing = build_opposing_sites_map(sites)

        # site-even y site-odd deben estar emparejados
        self.assertEqual(opposing.get("site-even"), "site-odd")
        self.assertEqual(opposing.get("site-odd"), "site-even")

        # site-distant y site-other no deben estar emparejados con site-even
        self.assertNotEqual(opposing.get("site-distant"), "site-even")
        self.assertNotIn("site-other", opposing)

    def test_spillover_when_full(self):
        # Escenario con site A (100%) y site B enfrente (50%)
        site_a = Site(
            id="site-a",
            name="Sitio A",
            zone="Centro",
            latitude=-34.6000,
            longitude=-58.3800,
            demand_base=5.0,  # Generará demanda
            address="CORRIENTES 1200",  # Par
        )
        site_b = Site(
            id="site-b",
            name="Sitio B",
            zone="Centro",
            latitude=-34.6001,
            longitude=-58.3801,  # Enfrente
            demand_base=0.0,  # Sin demanda propia para medir el traspaso
            address="CORRIENTES 1205",  # Impar
        )

        container_a = Container(
            id="c-a",
            site_id="site-a",
            name="Contenedor A",
            waste_type="residuos",
            height_cm=145.0,
        )
        container_b = Container(
            id="c-b",
            site_id="site-b",
            name="Contenedor B",
            waste_type="residuos",
            height_cm=145.0,
        )

        device_a = Device(id="dev-a", container_id="c-a")
        device_b = Device(id="dev-b", container_id="c-b")

        topology = SimulationTopology(
            sites=[site_a, site_b],
            containers=[container_a, container_b],
            devices=[device_a, device_b],
            initial_levels={"c-a": 100.0, "c-b": 50.0},
        )

        config = ScenarioConfig(
            name="test_spillover",
            seed=42,
            start=datetime(2026, 5, 2, 12, 0, 0),
            periods=1,
            frequency_minutes=60,
            collection_hours=(),  # Sin recolección en este test
        )

        sim = SyntheticDataSimulator(config, topology=topology)
        sim.initialize()

        # Ejecutar 1 tick
        sim.run_tick(datetime(2026, 5, 2, 12, 0, 0))

        # Contenedor A debe seguir al 100%
        self.assertEqual(sim.state.levels["c-a"], 100.0)
        # Contenedor B (enfrente) debe haber recibido el exceso generado por A y estar > 50%
        self.assertGreater(sim.state.levels["c-b"], 50.0)

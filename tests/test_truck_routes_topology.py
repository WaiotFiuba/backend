from __future__ import annotations

import unittest
from datetime import datetime

from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import Container, Device, SimulationTopology, Site
import random

from simulator.trucks.truck_engine import build_truck_fleet
from simulator.trucks.truck_routes import (
    build_collection_routes,
    build_rodrigo_bueno_route,
)


def _site(site_id: str, address: str, lat: float, lon: float) -> dict:
    return {
        "id": site_id,
        "address": address,
        "name": f"Sitio {address}",
        "latitude": lat,
        "longitude": lon,
    }


class TestCollectionRoutesFromTopology(unittest.TestCase):
    def test_routes_use_backend_site_ids_and_stream(self):
        black = _site("7", "SAN LUIS 2650", -34.6040, -58.4040)
        rodrigo_bueno = _site("8", "AV. ESPAÑA 2200", -34.6185, -58.3545)
        green = _site("9", "LIBERTAD 750", -34.6010, -58.3850)

        routes = build_collection_routes(
            black_container_sites=[black, rodrigo_bueno],
            green_container_sites=[green],
        )

        stops = [(r_id, s) for r_id, r in routes.items() for s in r.site_ids]
        # Cada sitio es una sola parada, con el id del backend tal cual
        # (sin prefijos tipo "contenedores_negros|").
        self.assertEqual(sorted(s for _, s in stops), ["7", "8", "9"])
        self.assertIn(("1184", "7"), stops)
        self.assertIn(("1RECLDM8510F6", "9"), stops)
        # El sitio de Rodrigo Bueno lo recorre solo su circuito.
        self.assertIn(("RODRIGO_BUENO", "8"), stops)

    def test_rodrigo_bueno_route_from_sites_only_takes_sites_in_the_barrio(self):
        route = build_rodrigo_bueno_route(
            [
                _site("lejos", "SAN LUIS 2650", -34.6040, -58.4040),
                _site("rb-lejos-del-acceso", "", -34.6230, -58.3610),
                _site("rb-cerca-del-acceso", "", -34.6160, -58.3570),
            ]
        )

        # Ordenadas por vecino mas cercano desde el acceso (Av. España).
        self.assertEqual(route.site_ids, ["rb-cerca-del-acceso", "rb-lejos-del-acceso"])
        self.assertEqual(len(route.waypoints), 2)

    def test_truck_only_empties_containers_of_its_stop_site(self):
        # Regresion: el motor indexaba ids de contenedor y de sitio bajo la
        # misma clave, asi que la parada "2" vaciaba tambien el contenedor
        # "2", que es del sitio "1".
        sites = [
            Site(
                id=site_id,
                name=f"Sitio {site_id}",
                zone="Balvanera",
                latitude=-34.6040,
                longitude=-58.4040,
                demand_base=1.0,
                address="SAN LUIS 2650",
            )
            for site_id in ("1", "2")
        ]
        containers = [
            Container(
                id=container_id,
                site_id=site_id,
                name="RSU",
                waste_type="RSU Fracción Húmeda",
                height_cm=145,
            )
            for container_id, site_id in (("2", "1"), ("5", "2"))
        ]
        topology = SimulationTopology(
            sites=sites,
            containers=containers,
            devices=[
                Device(id=f"sim-device-{c.id}", container_id=c.id) for c in containers
            ],
            initial_levels={"2": 80.0, "5": 80.0},
        )
        sim = SyntheticDataSimulator(
            ScenarioConfig(frequency_minutes=15), topology=topology
        )
        sim.initialize()
        for route in sim.truck_fleet.routes.values():
            route.site_ids = []
        sim.truck_fleet.routes["1184"].site_ids = ["2"]

        tick = sim.run_tick(datetime(2026, 9, 2, 22, 0))

        self.assertEqual([c.container_id for c in tick.collections], ["5"])


class TestBuildTruckFleet(unittest.TestCase):
    def test_each_site_goes_to_the_routes_of_its_container_color(self):
        # Los sitios son de un solo tipo: el negro va a las rutas de humedos y
        # el verde a las de secos, nunca a las dos.
        sites = [
            Site(
                id=site_id,
                name=f"Sitio {site_id}",
                zone="20350105",
                latitude=lat,
                longitude=lon,
                demand_base=1.0,
                address=address,
            )
            for site_id, address, lat, lon in (
                ("negro", "SAN LUIS 2650", -34.6040, -58.4040),
                ("verde", "LIBERTAD 750", -34.6010, -58.3850),
            )
        ]
        containers = [
            Container(
                id=f"c-{site_id}",
                site_id=site_id,
                name="RSU",
                waste_type=waste_type,
                height_cm=145,
            )
            for site_id, waste_type in (
                ("negro", "RSU Fracción Húmeda"),
                ("verde", "RSU Fracción Seca"),
            )
        ]
        topology = SimulationTopology(
            sites=sites,
            containers=containers,
            devices=[Device(id=f"d-{c.id}", container_id=c.id) for c in containers],
            initial_levels={c.id: 0.0 for c in containers},
        )

        fleet = build_truck_fleet(topology, ScenarioConfig(), random.Random(1))

        stops = [(r_id, s) for r_id, r in fleet.routes.items() for s in r.site_ids]
        self.assertEqual(sorted(s for _, s in stops), ["negro", "verde"])
        self.assertIn(("1184", "negro"), stops)
        self.assertIn(("1RECLDM8510F6", "verde"), stops)


if __name__ == "__main__":
    unittest.main()

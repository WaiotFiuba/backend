from __future__ import annotations

import unittest
from datetime import datetime

from simulator.trucks.truck_depots import (
    DEPOTS_BY_ZONE,
    TRANSFER_STATIONS,
    get_depot_for_zone,
    get_nearest_transfer_station,
)
from simulator.trucks.truck_engine import (
    TruckFleetSimulator,
    TruckStatus,
)
from simulator.trucks.truck_routes import (
    RouteSegment,
    TruckRoute,
    _fix_enie,
    _parse_routes_csv,
    assign_sites_to_routes,
    build_collection_routes,
    resolve_green_routes_csv_path,
    resolve_routes_csv_path,
)


class TestTruckSimulation(unittest.TestCase):
    def test_fix_enie_restores_enie(self):
        # El CSV fuente de circuitos trae la "ñ" corrompida como "±" en la
        # columna nomoficial (ej. "Ca±ada" en vez de "Cañada").
        self.assertEqual(_fix_enie("Casta±ares"), "Castañares")
        self.assertEqual(_fix_enie("Nu±ez"), "Nuñez")
        # Nombres sin el caracter corrompido quedan intactos.
        self.assertEqual(_fix_enie("San Luis"), "San Luis")

    def test_parse_routes_csv(self):
        routes = _parse_routes_csv(resolve_routes_csv_path())
        self.assertGreater(len(routes), 0)
        self.assertIn("1184", routes)

        # Ningun nombre de calle real cargado desde el CSV debe conservar el
        # caracter de mojibake que reemplazaba a la "ñ" en el archivo fuente.
        all_street_names = [
            seg.street_name for route in routes.values() for seg in route.segments
        ]
        self.assertTrue(
            any("ñ" in name.lower() for name in all_street_names),
            "se esperaba encontrar al menos una calle con 'ñ' ya corregida",
        )
        self.assertFalse(any("±" in name for name in all_street_names))

    def test_parse_green_routes_csv(self):
        routes = _parse_routes_csv(resolve_green_routes_csv_path())
        self.assertGreater(len(routes), 0)
        self.assertIn("1RECLDM8510F6", routes)

        route = routes["1RECLDM8510F6"]
        self.assertEqual(route.service_name, "Recoleccion De Contenedores Verdes")
        self.assertGreater(len(route.segments), 0)
        self.assertGreater(route.segments[0].length_m, 0.0)

    def test_build_collection_routes_includes_black_and_green_routes(self):
        routes = build_collection_routes(
            black_container_sites=[], green_container_sites=[]
        )
        self.assertIn("1184", routes)
        self.assertIn("1RECLDM8510F6", routes)
        # Sin sitios en el barrio, no hay circuito de Rodrigo Bueno.
        self.assertNotIn("RODRIGO_BUENO", routes)

    def test_depots_and_transfer_stations(self):
        self.assertEqual(len(DEPOTS_BY_ZONE), 7)
        self.assertEqual(len(TRANSFER_STATIONS), 3)

        depot_z3 = get_depot_for_zone(3)
        self.assertEqual(depot_z3.zone, 3)
        self.assertEqual(depot_z3.type, "base")

        nearest = get_nearest_transfer_station(-34.58, -58.44)
        self.assertEqual(nearest.id, "TRANSFER-COLEGIALES")

    def test_site_assignment_to_routes(self):
        routes = {
            "1184": TruckRoute(
                route_id="1184",
                zone=3,
                service_name="Carga Bilateral",
                segments=[
                    RouteSegment(
                        street_name="SAN LUIS",
                        alt_start=2601,
                        alt_end=2700,
                        sentido="Decreciente",
                        service_name="Carga Bilateral",
                        length_m=100.0,
                        zone=3,
                        comuna="3",
                        barrio="Balvanera",
                    )
                ],
            )
        }

        sites = [
            {
                "id": "SITE-1",
                "address": "SAN LUIS 2650",
                "latitude": -34.601,
                "longitude": -58.401,
            },
            {
                "id": "SITE-2",
                "address": "OTRA CALLE 100",
                "latitude": -34.610,
                "longitude": -58.410,
            },
        ]

        assignment = assign_sites_to_routes(sites, routes)
        self.assertIn("SITE-1", assignment["1184"])
        # SITE-2 no matchea ninguna calle indexada, pero cae por cercania
        # espacial a la unica ruta disponible (fallback de proximidad).
        self.assertEqual(routes["1184"].site_ids, ["SITE-1", "SITE-2"])

    def test_site_assignment_falls_back_to_nearest_route_by_distance(self):
        # Dos rutas lejos entre si, cada una con un sitio matcheado por calle.
        # Un sitio sin calle matcheada (huerfano) debe caer en la ruta cuyo
        # centroide de sitios ya asignados este geograficamente mas cerca.
        routes = {
            "NORTE": TruckRoute(
                route_id="NORTE",
                zone=1,
                service_name="Test",
                segments=[
                    RouteSegment(
                        street_name="CABILDO",
                        alt_start=100,
                        alt_end=200,
                        sentido="Creciente",
                        service_name="Test",
                        length_m=100.0,
                        zone=1,
                        comuna="1",
                        barrio="Belgrano",
                    )
                ],
            ),
            "SUR": TruckRoute(
                route_id="SUR",
                zone=8,
                service_name="Test",
                segments=[
                    RouteSegment(
                        street_name="RIESTRA",
                        alt_start=100,
                        alt_end=200,
                        sentido="Creciente",
                        service_name="Test",
                        length_m=100.0,
                        zone=8,
                        comuna="8",
                        barrio="Villa Soldati",
                    )
                ],
            ),
        }
        sites = [
            {
                "id": "SITE-NORTE",
                "address": "CABILDO 150",
                "latitude": -34.560,
                "longitude": -58.456,
            },
            {
                "id": "SITE-SUR",
                "address": "RIESTRA 150",
                "latitude": -34.670,
                "longitude": -58.460,
            },
            {
                "id": "SITE-HUERFANO",
                "address": "CALLE DESCONOCIDA 999",
                "latitude": -34.562,  # muy cerca de SITE-NORTE
                "longitude": -58.457,
            },
        ]

        assignment = assign_sites_to_routes(sites, routes)

        self.assertIn("SITE-HUERFANO", assignment["NORTE"])
        self.assertNotIn("SITE-HUERFANO", assignment["SUR"])

    def test_site_assignment_matches_dirty_route_segment_street_names(self):
        # Regresion: el CSV real de circuitos nombra las calles como
        # "Corrientes Av." (sufijo, no prefijo) o "Mitre, Bartolome"
        # (apellido, coma, nombre) porque nunca tienen altura embebida en el
        # nombre. Antes del fix, esto nunca matcheaba contra una direccion de
        # contenedor normalizada ("AV. CORRIENTES 1234" -> "CORRIENTES").
        routes = {
            "9001": TruckRoute(
                route_id="9001",
                zone=1,
                service_name="Test",
                segments=[
                    RouteSegment(
                        street_name="Corrientes Av.",
                        alt_start=1200,
                        alt_end=1300,
                        sentido="Creciente",
                        service_name="Test",
                        length_m=100.0,
                        zone=1,
                        comuna="1",
                        barrio="San Nicolas",
                    )
                ],
            ),
            "9002": TruckRoute(
                route_id="9002",
                zone=1,
                service_name="Test",
                segments=[
                    RouteSegment(
                        street_name="Mitre, Bartolome",
                        alt_start=400,
                        alt_end=600,
                        sentido="Creciente",
                        service_name="Test",
                        length_m=100.0,
                        zone=1,
                        comuna="1",
                        barrio="San Nicolas",
                    )
                ],
            ),
        }
        sites = [
            {
                "id": "SITE-CORRIENTES",
                "address": "AV. CORRIENTES 1234",
                "latitude": -34.603,
                "longitude": -58.381,
            },
            {
                "id": "SITE-MITRE",
                "address": "MITRE BARTOLOME 500",
                "latitude": -34.604,
                "longitude": -58.382,
            },
        ]

        assignment = assign_sites_to_routes(sites, routes)

        self.assertIn("SITE-CORRIENTES", assignment["9001"])
        self.assertIn("SITE-MITRE", assignment["9002"])

    def test_truck_engine_daytime_idle_and_night_dispatch(self):
        routes = {
            "101": TruckRoute(
                route_id="101",
                zone=3,
                service_name="Test Service",
                site_ids=["SITE-101", "SITE-102"],
                waypoints=[(-34.601, -58.401), (-34.602, -58.402)],
            )
        }
        sites_dict = {
            "SITE-101": (-34.601, -58.401),
            "SITE-102": (-34.602, -58.402),
        }

        simulator = TruckFleetSimulator(
            routes=routes,
            sites_dict=sites_dict,
            collection_hours=(21, 22, 23, 0, 1, 2, 3, 4, 5),
            collection_threshold_pct=60.0,
        )

        truck = simulator.trucks["TRUCK-101"]
        self.assertEqual(truck.status, TruckStatus.AT_DEPOT)

        # 1. Daytime step (14:00 hs) -> remains at depot
        day_time = datetime(2026, 9, 2, 14, 0, 0)
        containers_by_site = {
            "SITE-101": [{"id": 1, "current_level": 85.0}],
            "SITE-102": [{"id": 2, "current_level": 40.0}],
        }
        events = simulator.step(
            day_time,
            dt_seconds=300.0,
            speedup=1.0,
            containers_by_site=containers_by_site,
        )
        self.assertEqual(len(events), 0)
        self.assertEqual(truck.status, TruckStatus.AT_DEPOT)

        # 2. Night step (22:00 hs) -> dispatches
        night_time = datetime(2026, 9, 2, 22, 0, 0)
        events = simulator.step(
            night_time,
            dt_seconds=300.0,
            speedup=1.0,
            containers_by_site=containers_by_site,
        )
        self.assertEqual(truck.status, TruckStatus.COLLECTING)

        # 3. Force truck position at SITE-101 with collecting status
        truck.status = TruckStatus.COLLECTING
        truck.latitude = -34.601
        truck.longitude = -58.401
        truck.current_site_idx = 0
        truck.collected_containers_count = 0
        containers_by_site["SITE-101"][0]["current_level"] = 85.0

        # Run step -> should collect SITE-101 (85% >= 60%) but skip SITE-102 (40% < 60%)
        events = simulator.step(
            night_time,
            dt_seconds=60.0,
            speedup=1.0,
            containers_by_site=containers_by_site,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["container_id"], 1)
        self.assertLess(containers_by_site["SITE-101"][0]["current_level"], 10.0)
        self.assertGreater(truck.current_load_kg, 0.0)
        self.assertEqual(truck.collected_containers_count, 1)

        # SITE-102 (40%) should NOT be collected
        self.assertEqual(containers_by_site["SITE-102"][0]["current_level"], 40.0)

    def test_green_route_collects_recyclable_container(self):
        routes = {
            "GREEN": TruckRoute(
                route_id="GREEN",
                zone=1,
                service_name="Recoleccion De Contenedores Verdes",
                site_ids=["contenedores_verdes|1"],
                waypoints=[(-34.601, -58.401)],
            )
        }
        simulator = TruckFleetSimulator(
            routes=routes,
            sites_dict={"contenedores_verdes|1": (-34.601, -58.401)},
            collection_hours=(22,),
            collection_threshold_pct=0.0,
        )
        containers_by_site = {
            "contenedores_verdes|1": [
                {
                    "id": "contenedores_verdes|1",
                    "current_level": 80.0,
                    "waste_type": "RSU Fraccion Seca Reciclable",
                }
            ]
        }

        events = simulator.step(
            datetime(2026, 9, 3, 22, 0, 0),
            dt_seconds=60.0,
            speedup=1.0,
            containers_by_site=containers_by_site,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["container_id"], "contenedores_verdes|1")

    def test_rodrigo_bueno_site_is_collected_by_its_route(self):
        from simulator.simulation.engine import (
            SyntheticDataSimulator,
        )
        from simulator.simulation.scenario import ScenarioConfig
        from simulator.topology import (
            Container,
            Device,
            SimulationTopology,
            Site,
        )

        site = Site(
            id="158",
            name="YMA SUMAC 951",
            zone="Balvanera",
            latitude=-34.618579,
            longitude=-58.354145,
            demand_base=1.0,
        )
        cont = Container(
            id="328",
            site_id="158",
            name="RSU",
            waste_type="RSU Fracción Húmeda",
            height_cm=145,
            volume_m3=3.2,
        )
        dev = Device(id="sim-device-328", container_id="328")
        topo = SimulationTopology(
            sites=[site],
            containers=[cont],
            devices=[dev],
            initial_levels={"328": 100.0},
        )
        cfg = ScenarioConfig(frequency_minutes=15)
        sim = SyntheticDataSimulator(cfg, topology=topo)
        sim.initialize()
        sim.state.levels["328"] = 100.0

        # El sitio cae en el barrio Rodrigo Bueno: lo recorre solo su
        # circuito, con el id de sitio del backend.
        stops = [
            (r_id, s) for r_id, r in sim.truck_fleet.routes.items() for s in r.site_ids
        ]
        self.assertEqual(stops, [("RODRIGO_BUENO", "158")])

        # Tick a las 05:45
        tick = sim.run_tick(datetime(2026, 9, 2, 5, 45))
        self.assertEqual(len(tick.collections), 1)
        self.assertLess(sim.state.levels["328"], 10.0)

    def test_no_collection_days_blocks_trucks(self):
        routes = {
            "101": TruckRoute(
                route_id="101",
                zone=1,
                service_name="Test Service",
                site_ids=["SITE-101"],
                waypoints=[(-34.601, -58.401)],
            )
        }
        sites_dict = {"SITE-101": (-34.601, -58.401)}
        # 2026-09-02 is Wednesday (weekday = 2)
        simulator = TruckFleetSimulator(
            routes=routes,
            sites_dict=sites_dict,
            collection_hours=(22,),
            no_collection_days=(2,),  # Miércoles bloqueado
            collection_threshold_pct=0.0,
        )

        containers_by_site = {
            "SITE-101": [{"id": 1, "current_level": 85.0}],
        }
        wednesday_night = datetime(2026, 9, 2, 22, 0, 0)
        events = simulator.step(
            wednesday_night,
            dt_seconds=60.0,
            speedup=1.0,
            containers_by_site=containers_by_site,
        )
        self.assertEqual(len(events), 0)
        self.assertEqual(simulator.trucks["TRUCK-101"].status, TruckStatus.AT_DEPOT)

        # On Thursday (weekday = 3), collection should occur
        thursday_night = datetime(2026, 9, 3, 22, 0, 0)
        events_thu = simulator.step(
            thursday_night,
            dt_seconds=60.0,
            speedup=1.0,
            containers_by_site=containers_by_site,
        )
        self.assertEqual(len(events_thu), 1)
        self.assertEqual(simulator.trucks["TRUCK-101"].status, TruckStatus.COLLECTING)

from __future__ import annotations

import unittest
from datetime import datetime

from app.digital_twin.synthetic_data.simulation.truck_depots import (
    DEPOTS_BY_ZONE,
    TRANSFER_STATIONS,
    get_depot_for_zone,
    get_nearest_transfer_station,
)
from app.digital_twin.synthetic_data.simulation.truck_engine import (
    TruckFleetSimulator,
    TruckStatus,
)
from app.services.simulation.truck_route_service import (
    RouteSegment,
    TruckRoute,
    assign_sites_to_routes,
    load_routes_from_csv,
)


class TestTruckSimulation(unittest.TestCase):
    def test_load_routes_from_csv(self):
        routes = load_routes_from_csv()
        self.assertGreater(len(routes), 0)
        self.assertIn("RODRIGO_BUENO", routes)
        route_rb = routes["RODRIGO_BUENO"]
        self.assertGreater(len(route_rb.waypoints), 10)
        self.assertEqual(route_rb.zone, 1)

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
        self.assertEqual(routes["1184"].site_ids, ["SITE-1"])

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

    def test_scheduled_collection_matching_site_aliases(self):
        from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig
        from app.digital_twin.synthetic_data.topology import (
            SimulationTopology,
            Container,
            Device,
            Site,
        )
        from app.digital_twin.synthetic_data.simulation.engine import (
            SyntheticDataSimulator,
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
            id=328,
            site_id="158",
            name="RSU",
            waste_type="RSU Fracción Húmeda",
            height_cm=145,
            volume_m3=3.2,
        )
        dev = Device(id="sim-device-328", container_id=328)
        topo = SimulationTopology(
            sites=[site],
            containers=[cont],
            devices=[dev],
            initial_levels={328: 100.0},
        )
        cfg = ScenarioConfig(frequency_minutes=15)
        sim = SyntheticDataSimulator(cfg, topology=topo)
        sim.initialize()
        sim.state.levels[328] = 100.0

        from app.services.simulation.collection_schedule_service import (
            load_collection_schedule,
        )

        schedules = load_collection_schedule()
        schedules["RODRIGO_BUENO"] = {
            "route_id": "RODRIGO_BUENO",
            "stops": [
                {
                    "order": 1,
                    "site_id": "158",
                    "scheduled_time": "05:37",
                    "scheduled_minute_of_day": 5 * 60 + 37,
                }
            ],
        }
        if sim.truck_fleet and "RODRIGO_BUENO" in sim.truck_fleet.routes:
            sim.truck_fleet.routes["RODRIGO_BUENO"].site_ids = ["158"]

        # Tick a las 05:45 (que incluye parada a las 05:37)
        tick = sim.run_tick(datetime(2026, 9, 2, 5, 45))
        self.assertEqual(len(tick.collections), 1)
        self.assertLess(sim.state.levels[328], 10.0)

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

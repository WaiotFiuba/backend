from __future__ import annotations

import unittest
from datetime import datetime

from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import (
    Container,
    Device,
    SimulationTopology,
    Site,
)
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.trucks.truck_engine import TruckFleetSimulator
from simulator.trucks.collection_schedule import (
    get_sites_to_collect,
    load_collection_schedule,
)
from simulator.trucks.truck_routes import TruckRoute


class TestCollectionSchedulerSimulation(unittest.TestCase):
    def test_collection_schedule_coverage_and_time_format(self):
        schedules = load_collection_schedule()
        self.assertGreater(len(schedules), 0)

        total_stops = sum(len(s.get("stops", [])) for s in schedules.values())
        self.assertGreaterEqual(total_stops, 1000)

        # Verificar formato de paradas en una ruta
        sample_route = next(iter(schedules.values()))
        self.assertIn("route_id", sample_route)
        self.assertIn("stops", sample_route)
        self.assertGreater(len(sample_route["stops"]), 0)

        first_stop = sample_route["stops"][0]
        self.assertIn("scheduled_time", first_stop)
        self.assertIn("scheduled_minute_of_day", first_stop)
        self.assertIn("site_id", first_stop)

    def test_interval_catch_at_530_for_519_schedule(self):
        """
        Si un sitio tiene recolección programada a las 5:19 y el frequency_minutes es 15,
        el tick que salta de 5:15 a 5:30 debe incluir la recolección de las 5:19.
        """
        # Hora de parada: 05:19 (minuto 319 del día)
        # Intervalo 1: 05:00 a 05:15 (minutos 300 a 315) -> No debe incluir 5:19
        t_515 = datetime(2026, 9, 3, 5, 15, 0)
        # Intervalo 2: 05:15 a 05:30 (minutos 315 a 330) -> SÍ debe incluir 5:19
        t_530 = datetime(2026, 9, 3, 5, 30, 0)

        schedules = load_collection_schedule()
        # Mock de una ruta con parada a las 05:19
        mock_route_id = "TEST_ROUTE_519"
        schedules[mock_route_id] = {
            "route_id": mock_route_id,
            "stops": [
                {
                    "order": 1,
                    "site_id": "SITE_TEST_519",
                    "scheduled_time": "05:19",
                    "scheduled_minute_of_day": 5 * 60 + 19,  # 319
                }
            ],
        }

        # 1. A las 05:15 (intervalo 05:00 - 05:15) no debe recolectar
        stops_at_515 = get_sites_to_collect(
            t_515, frequency_minutes=15, route_id=mock_route_id
        )
        self.assertEqual(len(stops_at_515), 0)

        # 2. A las 05:30 (intervalo 05:15 - 05:30) debe recolectar la parada de las 05:19
        stops_at_530 = get_sites_to_collect(
            t_530, frequency_minutes=15, route_id=mock_route_id
        )
        self.assertEqual(len(stops_at_530), 1)
        self.assertEqual(stops_at_530[0]["site_id"], "SITE_TEST_519")

    def test_collection_threshold_60_percent_rule(self):
        """
        Verifica que solo los contenedores con nivel >= 60% se vacíen en el sitio.
        Los contenedores con < 60% deben permanecer con su nivel intacto.
        """
        route_id = "MOCK_ROUTE_60"
        schedules = load_collection_schedule()
        schedules[route_id] = {
            "route_id": route_id,
            "stops": [
                {
                    "order": 1,
                    "site_id": "SITE_MULTI_CONT",
                    "scheduled_time": "22:30",
                    "scheduled_minute_of_day": 22 * 60 + 30,  # 1350
                }
            ],
        }

        routes = {
            route_id: TruckRoute(
                route_id=route_id,
                zone=1,
                service_name="Test Service",
                site_ids=["SITE_MULTI_CONT"],
                waypoints=[(-34.60, -58.40)],
            )
        }

        fleet = TruckFleetSimulator(
            routes=routes,
            sites_dict={"SITE_MULTI_CONT": (-34.60, -58.40)},
            collection_hours=(21, 22, 23, 0, 1, 2, 3, 4, 5),
            collection_threshold_pct=60.0,
        )

        simulated_time = datetime(2026, 9, 3, 22, 30, 0)
        containers_by_site = {
            "SITE_MULTI_CONT": [
                {
                    "id": 101,
                    "current_level": 75.0,
                    "waste_type": "RSU Fracción Húmeda",
                },  # >= 60% -> Debe vaciarse
                {
                    "id": 102,
                    "current_level": 60.0,
                    "waste_type": "RSU Fracción Húmeda",
                },  # == 60% -> Debe vaciarse
                {
                    "id": 103,
                    "current_level": 59.0,
                    "waste_type": "RSU Fracción Húmeda",
                },  # < 60%  -> NO debe vaciarse
                {
                    "id": 104,
                    "current_level": 20.0,
                    "waste_type": "RSU Fracción Húmeda",
                },  # < 60%  -> NO debe vaciarse
            ]
        }

        events = fleet.step(
            simulated_time=simulated_time,
            dt_seconds=15 * 60.0,
            speedup=1.0,
            containers_by_site=containers_by_site,
        )

        # Se deben haber vaciado exactamente 2 contenedores (101 y 102)
        self.assertEqual(len(events), 2)
        collected_ids = {ev["container_id"] for ev in events}
        self.assertEqual(collected_ids, {101, 102})

        # Verificar niveles finales
        c_map = {
            c["id"]: c["current_level"] for c in containers_by_site["SITE_MULTI_CONT"]
        }
        self.assertLessEqual(c_map[101], 5.0)
        self.assertLessEqual(c_map[102], 5.0)
        self.assertEqual(c_map[103], 59.0)
        self.assertEqual(c_map[104], 20.0)

    def test_simulation_engine_tick_with_global_demand_10_and_frequency_15(self):
        """
        Prueba completa de SyntheticDataSimulator con:
        - global_demand_multiplier = 10.0
        - frequency_minutes = 15
        - Vaciado en horario programado a >= 60%
        """
        site_id = "SITE_DEMAND_TEST"
        route_id = "ROUTE_DEMAND_TEST"

        schedules = load_collection_schedule()
        schedules[route_id] = {
            "route_id": route_id,
            "stops": [
                {
                    "order": 1,
                    "site_id": site_id,
                    "scheduled_time": "23:45",
                    "scheduled_minute_of_day": 23 * 60 + 45,  # 1425
                }
            ],
        }

        site = Site(
            id=site_id,
            name="Av. Corrientes 1234",
            zone="San Nicolas",
            latitude=-34.6037,
            longitude=-58.3816,
            demand_base=2.5,
        )
        cont1 = Container(
            id="201",
            site_id=site_id,
            name="Contenedor Húmedo 1",
            waste_type="RSU Fracción Húmeda",
            height_cm=145.0,
            volume_m3=3.2,
        )
        cont2 = Container(
            id="202",
            site_id=site_id,
            name="Contenedor Húmedo 2",
            waste_type="RSU Fracción Húmeda",
            height_cm=145.0,
            volume_m3=3.2,
        )
        dev1 = Device(id="dev-201", container_id="201")
        dev2 = Device(id="dev-202", container_id="202")

        topo = SimulationTopology(
            sites=[site],
            containers=[cont1, cont2],
            devices=[dev1, dev2],
            initial_levels={"201": 55.0, "202": 30.0},
        )

        config = ScenarioConfig(
            start=datetime(2026, 9, 3, 23, 30, 0),
            periods=2,
            frequency_minutes=15,
            high_demand_multiplier=10.0,
        )

        simulator = SyntheticDataSimulator(config, topology=topo)
        simulator.initialize()

        # Inyectar ruta al simulador
        routes = {
            route_id: TruckRoute(
                route_id=route_id,
                zone=1,
                service_name="Test Recoleccion",
                site_ids=[site_id],
                waypoints=[(-34.6037, -58.3816)],
            )
        }
        simulator.truck_fleet = TruckFleetSimulator(
            routes=routes,
            sites_dict={site_id: (-34.6037, -58.3816)},
            collection_hours=(21, 22, 23, 0, 1, 2, 3, 4, 5),
            collection_threshold_pct=60.0,
        )

        # Tick 1: 23:30 a 23:45 (A las 23:45 toca recolección)
        # Con global_demand = 10.0, el cont1 que empezó en 55% subirá a >60% y debe ser vaciado en este tick.
        tick_time = datetime(2026, 9, 3, 23, 45, 0)
        res = simulator.run_tick(tick_time, global_demand_multiplier=10.0)

        # Verificar que se generó evento de recolección para cont1
        self.assertGreater(len(res.collections), 0)
        collected_container_ids = [c.container_id for c in res.collections]
        self.assertIn("201", [str(cid) for cid in collected_container_ids])

        # Nivel final de cont1 debe ser <= 5%
        self.assertLessEqual(simulator.state.levels["201"], 5.0)

        # Mediciones emitidas deben reflejar el nivel vaciado
        m_201 = next(m for m in res.measurements if str(m.container_id) == "201")
        self.assertLessEqual(m_201.fill_level_pct, 5.0)


if __name__ == "__main__":
    unittest.main()

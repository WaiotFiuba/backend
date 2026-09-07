from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.digital_twin.synthetic_data.simulation.truck_depots import (
    DEPOTS_BY_ZONE,
    Depot,
    get_depot_for_zone,
    get_nearest_transfer_station,
)
from app.services.simulation.truck_route_service import TruckRoute

logger = logging.getLogger(__name__)


class TruckStatus:
    AT_DEPOT = "AT_DEPOT"      # En espera en terminal/depósito
    COLLECTING = "COLLECTING"  # En recorrido de recolección activo


@dataclass
class TruckState:
    id: str
    route_id: str
    zone: int
    latitude: float
    longitude: float
    status: str = TruckStatus.AT_DEPOT
    capacity_kg: float = 10000.0
    current_load_kg: float = 0.0
    collected_containers_count: int = 0
    current_site_idx: int = 0
    depot_id: str = "DEPOT-Z3"
    speed_kmh: float = 30.0
    unloading_ticks_remaining: int = 0

class TruckFleetSimulator:
    def __init__(
        self,
        routes: dict[str, TruckRoute],
        sites_dict: dict[str, tuple[float, float]],
        collection_hours: tuple[int, ...] = (21, 22, 23, 0, 1, 2, 3, 4, 5, 6),
        collection_threshold_pct: float = 60.0,
    ) -> None:
        self.routes = routes
        self.sites_dict = sites_dict  # {site_id: (lat, lon)}
        self.collection_hours = collection_hours
        self.collection_threshold_pct = collection_threshold_pct
        self.trucks: dict[str, TruckState] = {}
        self._init_fleet()

    def _init_fleet(self) -> None:
        """Crea 1 camión recolector por cada circuito de recolección en el punto de inicio de su recorrido."""
        for r_id, route in self.routes.items():
            depot = get_depot_for_zone(route.zone)
            truck_id = f"TRUCK-{r_id}"
            num_stops = len(route.site_ids) if route.site_ids else 100
            truck_capacity = max(15000.0, float(num_stops * 400.0))
            if route.waypoints:
                start_lat, start_lon = route.waypoints[0]
            else:
                idx = int(r_id) if r_id.isdigit() else hash(r_id)
                start_lat = depot.latitude + ((idx % 7) - 3) * 0.00035
                start_lon = depot.longitude + (((idx // 7) % 7) - 3) * 0.00035
            self.trucks[truck_id] = TruckState(
                id=truck_id,
                route_id=r_id,
                zone=route.zone,
                latitude=round(start_lat, 6),
                longitude=round(start_lon, 6),
                status=TruckStatus.AT_DEPOT,
                capacity_kg=truck_capacity,
                current_load_kg=0.0,
                collected_containers_count=0,
                current_site_idx=0,
                depot_id=depot.id,
                speed_kmh=24.0 + random.uniform(-2.0, 4.0),
            )
        logger.info("Inicializada flota de %d camiones en sus terminales.", len(self.trucks))

    def step(
        self,
        simulated_time: datetime,
        dt_seconds: float,
        speedup: float,
        containers_by_site: dict[str, list[dict]],
        on_container_collected: Callable[[str, float, float], None] | None = None,
        max_step_meters: float | None = None,
    ) -> list[dict]:
        """
        Avanza la recolección discreta por los sitios de cada circuito a lo largo del turno (21:00 a 06:00).
        La velocidad y cantidad de sitios por tick se adaptan automáticamente a dt_seconds / frequency_minutes.
        """
        is_collection_time = simulated_time.hour in self.collection_hours
        collection_events: list[dict] = []
        from app.services.simulation.collection_schedule_service import get_sites_to_collect

        for truck_id, truck in self.trucks.items():
            route = self.routes.get(truck.route_id)
            if not route:
                continue

            dt_minutes = max(1, int(round((dt_seconds * speedup) / 60.0)))
            scheduled_stops = get_sites_to_collect(
                simulated_time=simulated_time,
                frequency_minutes=dt_minutes,
                route_id=truck.route_id,
            )

            if not scheduled_stops:
                # Si la ruta es sintética/mock no existente en el cronograma estático
                from app.services.simulation.collection_schedule_service import load_collection_schedule
                sched = load_collection_schedule()
                if route.site_ids and is_collection_time and truck.route_id not in sched:
                    scheduled_stops = [{"site_id": s} for s in route.site_ids]
                else:
                    truck.status = TruckStatus.AT_DEPOT if not is_collection_time else TruckStatus.COLLECTING
                    continue

            truck.status = TruckStatus.COLLECTING

            for stop in scheduled_stops:
                site_id = stop["site_id"]
                raw_id = str(site_id).split("|")[-1]
                site_containers = (
                    containers_by_site.get(str(site_id))
                    or containers_by_site.get(raw_id)
                    or containers_by_site.get(f"contenedores_negros|{raw_id}")
                    or containers_by_site.get(f"SITE-{raw_id}")
                    or []
                )
                seen_c_ids = set()
                for c in site_containers:
                    c_id = c.get("id")
                    if c_id in seen_c_ids:
                        continue
                    seen_c_ids.add(c_id)

                    # FILTRO EXCLUSIVO: Solo recolectar contenedores de Fracción Húmeda
                    w_type = str(c.get("waste_type") or "").lower()
                    c_type = str(c.get("container_type") or "").lower()
                    if any(x in w_type or x in c_type for x in ("seca", "recicl", "verde", "vidrio")):
                        continue

                    c_level = float(c.get("current_level", 0.0))
                    if c_level >= self.collection_threshold_pct:
                        # VACIAR CONTENEDOR (>= 60%)
                        new_level = round(random.uniform(0.0, 5.0), 1)
                        emptied_pct = c_level - new_level
                        emptied_kg = (emptied_pct / 100.0) * 350.0

                        c["current_level"] = new_level
                        truck.current_load_kg += emptied_kg
                        truck.collected_containers_count += 1

                        event = {
                            "truck_id": truck.id,
                            "site_id": site_id,
                            "container_id": c.get("id"),
                            "level_before": c_level,
                            "level_after": new_level,
                            "collected_kg": emptied_kg,
                            "timestamp": simulated_time.isoformat(),
                        }
                        collection_events.append(event)
                        if on_container_collected:
                            on_container_collected(
                                str(c.get("id")), c_level, new_level
                            )

        return collection_events

    def get_trucks_snapshot(self) -> list[dict]:
        """Retorna el estado de todos los camiones activos para la API y el Frontend."""
        result = []
        for truck in self.trucks.values():
            load_pct = round((truck.current_load_kg / max(1.0, truck.capacity_kg)) * 100.0, 1)
            result.append(
                {
                    "id": truck.id,
                    "route_id": truck.route_id,
                    "zone": truck.zone,
                    "latitude": round(truck.latitude, 6),
                    "longitude": round(truck.longitude, 6),
                    "status": truck.status,
                    "capacity_kg": truck.capacity_kg,
                    "current_load_kg": round(truck.current_load_kg, 1),
                    "load_percentage": load_pct,
                    "collected_containers_count": truck.collected_containers_count,
                    "current_site_idx": truck.current_site_idx,
                    "depot_id": truck.depot_id,
                }
            )
        return result


_LATEST_TRUCK_SNAPSHOT: list[dict] | None = None
_DEMO_FLEET_SIMULATOR: TruckFleetSimulator | None = None
_LAST_DEMO_STEP_TIME: float | None = None


def set_latest_truck_snapshot(snapshot: list[dict]) -> None:
    global _LATEST_TRUCK_SNAPSHOT
    _LATEST_TRUCK_SNAPSHOT = snapshot


def get_latest_truck_snapshot() -> list[dict]:
    global _LATEST_TRUCK_SNAPSHOT, _DEMO_FLEET_SIMULATOR
    if _LATEST_TRUCK_SNAPSHOT is not None:
        return _LATEST_TRUCK_SNAPSHOT

    if _DEMO_FLEET_SIMULATOR is None:
        from app.services.simulation.truck_route_service import load_routes_from_csv
        routes = load_routes_from_csv()
        sites_dict = {}
        if "RODRIGO_BUENO" in routes and routes["RODRIGO_BUENO"].waypoints:
            sites_dict = {
                s_id: routes["RODRIGO_BUENO"].waypoints[i]
                for i, s_id in enumerate(routes["RODRIGO_BUENO"].site_ids)
                if i < len(routes["RODRIGO_BUENO"].waypoints)
            }
        _DEMO_FLEET_SIMULATOR = TruckFleetSimulator(routes=routes, sites_dict=sites_dict)

    # El camión permanece estacionado en la base (AT_DEPOT) hasta que el usuario inicie la simulación desde el frontend
    return _DEMO_FLEET_SIMULATOR.get_trucks_snapshot()

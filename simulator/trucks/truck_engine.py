from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from simulator.trucks.truck_depots import (
    get_depot_for_zone,
)
from simulator.trucks.truck_routes import TruckRoute

logger = logging.getLogger(__name__)


def _is_green_route(route: TruckRoute) -> bool:
    service_name = route.service_name.lower()
    return any(token in service_name for token in ("verde", "recicl", "seca"))


def _is_recyclable_container(container: dict) -> bool:
    w_type = str(container.get("waste_type") or "").lower()
    c_type = str(container.get("container_type") or "").lower()
    return any(
        token in w_type or token in c_type
        for token in ("seca", "recicl", "verde", "vidrio")
    )


class TruckStatus:
    AT_DEPOT = "AT_DEPOT"  # En espera en terminal/depósito
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
        no_collection_days: tuple[int, ...] = (),
        collection_threshold_pct: float = 0.0,
        rng: random.Random | None = None,
    ) -> None:
        self.routes = routes
        self.sites_dict = sites_dict  # {site_id: (lat, lon)}
        self.collection_hours = collection_hours
        self.no_collection_days = set(no_collection_days)
        self.collection_threshold_pct = collection_threshold_pct
        # RNG propia (idealmente la del motor, sembrada con ScenarioConfig.seed)
        # para que la flota sea reproducible igual que el resto de la simulación.
        # Sin una instancia explícita, cae a random.Random() sin sembrar (uso
        # fuera de una simulación, ej. la vista previa de demo).
        self.rng = rng if rng is not None else random.Random()
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
                speed_kmh=24.0 + self.rng.uniform(-2.0, 4.0),
            )
        logger.info(
            "Inicializada flota de %d camiones en sus terminales.", len(self.trucks)
        )

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
        Avanza la recolección discreta por los sitios de cada circuito a lo largo de las horas activas de collection_hours.
        Distribuye dinámicamente todas las paradas de cada ruta a lo largo de las horas de recolección configuradas.
        """
        ordered_hours = sorted(self.collection_hours, key=lambda h: (h - 18) % 24)
        num_hours = len(ordered_hours)

        if (
            num_hours == 0
            or simulated_time.hour not in self.collection_hours
            or simulated_time.weekday() in self.no_collection_days
        ):
            for truck in self.trucks.values():
                truck.status = TruckStatus.AT_DEPOT
            return []

        hour_idx = ordered_hours.index(simulated_time.hour)
        collection_events: list[dict] = []

        for truck_id, truck in self.trucks.items():
            route = self.routes.get(truck.route_id)
            if not route or not route.site_ids:
                continue
            is_green_route = _is_green_route(route)

            total_sites = len(route.site_ids)
            if total_sites <= num_hours:
                sites_to_collect = route.site_ids
                end_idx = total_sites
            else:
                start_idx = int(hour_idx * total_sites / num_hours)
                end_idx = (
                    total_sites
                    if hour_idx == num_hours - 1
                    else int((hour_idx + 1) * total_sites / num_hours)
                )
                sites_to_collect = route.site_ids[start_idx:end_idx]

            scheduled_stops = [{"site_id": s} for s in sites_to_collect]

            truck.status = TruckStatus.COLLECTING
            truck.current_site_idx = end_idx

            if sites_to_collect:
                last_site = str(sites_to_collect[-1]).split("|")[-1]
                if last_site in self.sites_dict:
                    s_lat, s_lon = self.sites_dict[last_site]
                    truck.latitude = round(s_lat, 6)
                    truck.longitude = round(s_lon, 6)
                elif str(sites_to_collect[-1]) in self.sites_dict:
                    s_lat, s_lon = self.sites_dict[str(sites_to_collect[-1])]
                    truck.latitude = round(s_lat, 6)
                    truck.longitude = round(s_lon, 6)

            for stop in scheduled_stops:
                site_id = stop["site_id"]
                raw_id = str(site_id).split("|")[-1]
                site_containers = (
                    containers_by_site.get(str(site_id))
                    or containers_by_site.get(raw_id)
                    or containers_by_site.get(f"contenedores_verdes|{raw_id}")
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

                    # Match route stream: wet routes collect wet containers, green routes collect recyclables.
                    if _is_recyclable_container(c) != is_green_route:
                        continue

                    c_level = float(c.get("current_level", 0.0))
                    if c_level > 0.0 and c_level >= self.collection_threshold_pct:
                        # VACIAR CONTENEDOR
                        new_level = round(self.rng.uniform(0.0, min(c_level, 3.0)), 1)
                        if new_level >= c_level:
                            new_level = 0.0
                        emptied_pct = c_level - new_level
                        emptied_kg = (emptied_pct / 100.0) * 350.0

                        c["current_level"] = new_level
                        truck.current_load_kg += emptied_kg
                        truck.collected_containers_count += 1

                        event = {
                            "truck_id": truck.id,
                            "site_id": site_id,
                            "container_id": c.get("id"),
                            "container_index": c.get("index"),
                            "level_before": c_level,
                            "level_after": new_level,
                            "collected_kg": emptied_kg,
                            "timestamp": simulated_time.isoformat(),
                        }
                        collection_events.append(event)
                        if on_container_collected:
                            on_container_collected(str(c.get("id")), c_level, new_level)

        return collection_events

    def get_trucks_snapshot(self) -> list[dict]:
        """Retorna el estado de todos los camiones activos para la API y el Frontend."""
        result = []
        for truck in self.trucks.values():
            load_pct = round(
                (truck.current_load_kg / max(1.0, truck.capacity_kg)) * 100.0, 1
            )
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

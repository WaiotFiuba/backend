"""
Script para generar el cronograma estático pre-calculado de recolección de residuos (collection_schedule.json).
Carga TODAS las rutas de CABA y asigna TODOS los sitios de contenedores con sus horarios de pasada entre 21:00 y 06:00.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

# Agregar backend al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from simulator.trucks.truck_routes import (
    assign_sites_to_routes,
    load_routes_from_csv,
)

DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "datos" / "simulator" / "routes"
)
SCHEDULE_FILE = DATA_DIR / "collection_schedule.json"


def parse_time_str(hour_min: str) -> time:
    h, m = map(int, hour_min.split(":"))
    return time(hour=h, minute=m)


def minute_of_day(t: time) -> int:
    return t.hour * 60 + t.minute


def build_schedule_for_stops(
    route_id: str,
    stops: list[dict],
    start_time_str: str = "21:00",
    end_time_str: str = "06:00",
    capacity_kg: float = 10000.0,
    zone: int = 1,
) -> dict:
    """
    Distribuye los sitios de una ruta de forma secuencial
    a lo largo del turno nocturno (21:00 a 06:00 = 540 minutos).
    """
    total_stops = len(stops)
    if total_stops == 0:
        return {
            "route_id": route_id,
            "truck_id": f"TRUCK-{route_id}",
            "truck_capacity_kg": capacity_kg,
            "zone": zone,
            "shift_start": start_time_str,
            "shift_end": end_time_str,
            "total_stops": 0,
            "stops": [],
        }

    total_shift_minutes = 530.0  # Finaliza a las 05:50 hs (antes de las 06:00)
    step_minutes = total_shift_minutes / float(max(1, total_stops))

    base_dt = datetime(2026, 1, 1, 21, 0, 0)
    scheduled_stops = []

    for i, s in enumerate(stops):
        arrival_dt = base_dt + timedelta(minutes=int(i * step_minutes))
        arr_time = arrival_dt.time()
        time_formatted = f"{arr_time.hour:02d}:{arr_time.minute:02d}"

        scheduled_stops.append(
            {
                "order": i + 1,
                "site_id": s["id"],
                "scheduled_time": time_formatted,
                "scheduled_minute_of_day": minute_of_day(arr_time),
                "address": s.get("address", ""),
                "lat": round(s.get("lat", 0.0), 6),
                "lon": round(s.get("lon", 0.0), 6),
            }
        )

    return {
        "route_id": route_id,
        "truck_id": f"TRUCK-{route_id}",
        "truck_capacity_kg": capacity_kg,
        "zone": zone,
        "shift_start": start_time_str,
        "shift_end": end_time_str,
        "total_stops": total_stops,
        "stops": scheduled_stops,
    }


def generate_all_schedules() -> dict:
    """Genera el itinerario completo de recolección para TODAS las rutas de CABA."""
    from simulator.trucks.truck_depots import (
        get_depot_for_zone,
    )

    candidates = [
        Path(__file__).resolve().parent.parent
        / "db"
        / "datos"
        / "contenedores_negros.json",
        Path(__file__).resolve().parent.parent.parent
        / "backend"
        / "db"
        / "datos"
        / "contenedores_negros.json",
        Path("/app/db/datos/contenedores_negros.json"),
        Path("db/datos/contenedores_negros.json"),
    ]
    json_path = next((p for p in candidates if p.exists()), None)
    all_sites = []
    if json_path:
        with open(json_path, mode="r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            coords = feat.get("geometry", {}).get("coordinates", [])
            if len(coords) >= 2:
                lon, lat = coords[0], coords[1]
                all_sites.append(
                    {
                        "id": str(props.get("Id", "")),
                        "address": props.get("DireccionNormalizada", ""),
                        "lat": lat,
                        "lon": lon,
                    }
                )

    routes = load_routes_from_csv()
    sites_by_id = {s["id"]: s for s in all_sites}

    # Asignación primaria por coincidencia de calle y altura catastral
    assignment = assign_sites_to_routes(all_sites, routes)

    # Calcular centroides de cada ruta para asignación espacial de sitios huérfanos
    route_centers: dict[str, tuple[float, float]] = {}
    for r_id, r in routes.items():
        s_ids = assignment.get(r_id, [])
        if s_ids:
            pts = [sites_by_id[sid] for sid in s_ids if sid in sites_by_id]
            if pts:
                avg_lat = sum(p["lat"] for p in pts) / len(pts)
                avg_lon = sum(p["lon"] for p in pts) / len(pts)
                route_centers[r_id] = (avg_lat, avg_lon)
                continue
        if r.waypoints:
            route_centers[r_id] = r.waypoints[0]
        else:
            depot = get_depot_for_zone(r.zone)
            route_centers[r_id] = (depot.latitude, depot.longitude)

    assigned_site_ids = set()
    for s_ids in assignment.values():
        assigned_site_ids.update(s_ids)

    unassigned_sites = [s for s in all_sites if s["id"] not in assigned_site_ids]

    # Asignar sitios huérfanos a la ruta más cercana geográficamente
    for us in unassigned_sites:
        u_lat, u_lon = us["lat"], us["lon"]
        # Caso especial para Barrio Rodrigo Bueno
        if (
            -34.624 <= u_lat <= -34.615
            and -58.362 <= u_lon <= -58.350
            and "RODRIGO_BUENO" in routes
        ):
            best_r = "RODRIGO_BUENO"
        else:
            best_r = None
            best_dist = float("inf")
            for r_id, (rc_lat, rc_lon) in route_centers.items():
                if r_id == "RODRIGO_BUENO":
                    continue
                d = math.hypot(rc_lat - u_lat, rc_lon - u_lon)
                if d < best_dist:
                    best_dist = d
                    best_r = r_id

        if best_r:
            assignment.setdefault(best_r, []).append(us["id"])

    schedules = {}
    base_dt = datetime(2026, 1, 1, 21, 0, 0)

    for r_id, r in routes.items():
        s_ids = assignment.get(r_id, [])
        if not s_ids:
            continue
        stops = [sites_by_id[sid] for sid in s_ids if sid in sites_by_id]
        if stops:
            unvisited = list(stops)
            depot = get_depot_for_zone(r.zone)
            # Iniciar Nearest-Neighbor desde el depósito o primer punto
            curr = min(
                unvisited,
                key=lambda s: math.hypot(
                    s["lat"] - depot.latitude, s["lon"] - depot.longitude
                ),
            )
            unvisited.remove(curr)
            sorted_stops = [curr]
            while unvisited:
                curr = sorted_stops[-1]
                nxt = min(
                    unvisited,
                    key=lambda s: math.hypot(
                        s["lat"] - curr["lat"], s["lon"] - curr["lon"]
                    ),
                )
                unvisited.remove(nxt)
                sorted_stops.append(nxt)
            stops = sorted_stops

        total_stops = len(stops)
        step_minutes = 530.0 / float(max(1, total_stops))
        scheduled_stops = []

        for i, s in enumerate(stops):
            arrival_dt = base_dt + timedelta(minutes=int(i * step_minutes))
            arr_time = arrival_dt.time()
            time_formatted = f"{arr_time.hour:02d}:{arr_time.minute:02d}"
            scheduled_stops.append(
                {
                    "order": i + 1,
                    "site_id": s["id"],
                    "scheduled_time": time_formatted,
                    "scheduled_minute_of_day": arr_time.hour * 60 + arr_time.minute,
                    "address": s.get("address", ""),
                    "lat": round(s.get("lat", 0.0), 6),
                    "lon": round(s.get("lon", 0.0), 6),
                }
            )

        schedules[r_id] = {
            "route_id": r_id,
            "truck_id": f"TRUCK-{r_id}",
            "truck_capacity_kg": max(15000.0, float(total_stops * 400.0)),
            "zone": r.zone,
            "shift_start": "21:00",
            "shift_end": "06:00",
            "total_stops": total_stops,
            "stops": scheduled_stops,
        }

    return schedules


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    schedules = generate_all_schedules()
    target_path = DATA_DIR / "collection_schedule.json"
    with open(target_path, mode="w", encoding="utf-8") as f:
        json.dump(schedules, f, indent=2, ensure_ascii=False)
    print(f"Cronograma guardado exitosamente en: {target_path}")

    total_sites_covered = sum(s["total_stops"] for s in schedules.values())
    print(f"Total de rutas cubiertas: {len(schedules)}")
    print(
        f"Total de sitios de contenedores asignados a recorridos: {total_sites_covered}"
    )


if __name__ == "__main__":
    main()

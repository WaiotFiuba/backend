"""
Servicio para consultar el cronograma de recolección pre-calculado (collection_schedule.json).
Permite al backend determinar de forma instantánea qué sitios deben vaciarse en un intervalo de simulación.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("waiot.collection_schedule")

CANDIDATE_PATHS = [
    Path(__file__).resolve().parent.parent.parent.parent / "datos" / "collection_schedule.json",
    Path("/app/datos/collection_schedule.json"),
    Path("datos/collection_schedule.json"),
]


_CACHED_SCHEDULE: dict | None = None



def generate_all_schedules() -> dict:
    """Genera el itinerario completo de recolección para TODAS las rutas de CABA."""
    import math
    from app.digital_twin.synthetic_data.simulation.truck_depots import get_depot_for_zone
    from app.services.simulation.truck_route_service import (
        assign_sites_to_routes,
        load_routes_from_csv,
    )

    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "db" / "datos" / "contenedores_negros.json",
        Path(__file__).resolve().parent.parent.parent / "db" / "datos" / "contenedores_negros.json",
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
        if -34.624 <= u_lat <= -34.615 and -58.362 <= u_lon <= -58.350 and "RODRIGO_BUENO" in routes:
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
            curr = min(unvisited, key=lambda s: math.hypot(s["lat"] - depot.latitude, s["lon"] - depot.longitude))
            unvisited.remove(curr)
            sorted_stops = [curr]
            while unvisited:
                curr = sorted_stops[-1]
                nxt = min(
                    unvisited,
                    key=lambda s: math.hypot(s["lat"] - curr["lat"], s["lon"] - curr["lon"]),
                )
                unvisited.remove(nxt)
                sorted_stops.append(nxt)
            stops = sorted_stops

        total_stops = len(stops)
        # Turno de 21:00 a 06:00 (540 minutos)
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



def save_collection_schedule() -> None:
    """Genera y guarda el cronograma en el directorio de datos."""
    schedules = generate_all_schedules()
    saved = False
    for cand in CANDIDATE_PATHS:
        try:
            cand.parent.mkdir(parents=True, exist_ok=True)
            with open(cand, mode="w", encoding="utf-8") as f:
                json.dump(schedules, f, indent=2, ensure_ascii=False)
            logger.info("Cronograma guardado en %s", cand)
            saved = True
        except Exception:
            continue
    if not saved:
        logger.warning("No se pudo guardar el cronograma en ninguna de las rutas candidatas.")


def load_collection_schedule() -> dict:
    """Carga el cronograma de recolección desde el archivo JSON estático."""
    global _CACHED_SCHEDULE
    if _CACHED_SCHEDULE is not None:
        return _CACHED_SCHEDULE

    schedule_path = next((p for p in CANDIDATE_PATHS if p.exists()), None)
    if not schedule_path:
        # Autogenerar si no existe
        _CACHED_SCHEDULE = generate_all_schedules()
        save_collection_schedule()
        return _CACHED_SCHEDULE

    try:
        with open(schedule_path, mode="r", encoding="utf-8") as f:
            _CACHED_SCHEDULE = json.load(f)
            logger.info("Cronograma de recolección cargado desde %s", schedule_path)
            return _CACHED_SCHEDULE
    except Exception as e:
        logger.error("Error leyendo %s: %s", schedule_path, e)
        return {}


def get_sites_to_collect(
    simulated_time: datetime,
    frequency_minutes: int,
    route_id: str | None = None,
) -> list[dict]:
    """
    Retorna los sitios cuya hora programada de recolección cae en el intervalo
    (simulated_time - frequency_minutes, simulated_time].
    Si route_id es None, busca en TODAS las rutas de CABA.
    """
    schedules = load_collection_schedule()
    if not schedules:
        return []

    if route_id is not None:
        target_schedules = [schedules[route_id]] if route_id in schedules else []
    else:
        target_schedules = list(schedules.values())

    # Calcular minutos del día para el intervalo
    t_end = simulated_time
    t_start = simulated_time - timedelta(minutes=frequency_minutes)

    end_min = t_end.hour * 60 + t_end.minute
    start_min = t_start.hour * 60 + t_start.minute

    matching_stops = []
    for sched in target_schedules:
        for stop in sched.get("stops", []):
            s_min = stop.get("scheduled_minute_of_day")
            if s_min is None:
                continue

            # Caso 1: Intervalo dentro del mismo día
            if start_min <= end_min:
                if start_min < s_min <= end_min:
                    matching_stops.append(stop)
            # Caso 2: Intervalo cruza la medianoche (ej: 23:55 a 00:10)
            else:
                if s_min > start_min or s_min <= end_min:
                    matching_stops.append(stop)

    return matching_stops

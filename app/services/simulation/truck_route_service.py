from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.digital_twin.synthetic_data.generators.street_pairing import parse_street_address

logger = logging.getLogger(__name__)


@dataclass
class RouteSegment:
    street_name: str
    alt_start: int
    alt_end: int
    sentido: str
    service_name: str
    length_m: float
    zone: int
    comuna: str
    barrio: str


@dataclass
class TruckRoute:
    route_id: str
    zone: int
    service_name: str
    segments: list[RouteSegment] = field(default_factory=list)
    site_ids: list[str] = field(default_factory=list)
    waypoints: list[tuple[float, float]] = field(default_factory=list)
    total_distance_m: float = 0.0
    collection_distance_m: float = 0.0
    deadheading_distance_m: float = 0.0
    repeated_segments_count: int = 0
    street_sequence: list[str] = field(default_factory=list)


_CACHED_ROUTES: dict[str, TruckRoute] | None = None
FOCUS_RODRIGO_BUENO_MODE = False


def build_rodrigo_bueno_route() -> TruckRoute:
    """
    Construye la ruta optimizada paso a paso por los 36 contenedores del Barrio Rodrigo Bueno.
    Ordenada mediante Nearest Neighbor para garantizar un recorrido continuo y lógico.
    """
    import json
    import math

    candidates = [
        Path(__file__).resolve().parent.parent.parent / "db" / "datos" / "contenedores_negros.json",
        Path(__file__).resolve().parent.parent.parent.parent / "backend" / "db" / "datos" / "contenedores_negros.json",
        Path("/app/db/datos/contenedores_negros.json"),
        Path("db/datos/contenedores_negros.json"),
    ]
    json_path = next((p for p in candidates if p.exists()), None)
    rb_containers = []
    if json_path:
        try:
            with open(json_path, mode="r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            for feat in data.get("features", []):
                props = feat.get("properties", {})
                coords = feat.get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    # Cuadrilátero geográfico estricto del Barrio Rodrigo Bueno
                    if -34.624 <= lat <= -34.615 and -58.362 <= lon <= -58.350:
                        rb_containers.append({
                            "id": str(props.get("Id", "")),
                            "address": props.get("DireccionNormalizada", ""),
                            "lat": lat,
                            "lon": lon,
                        })
        except Exception as e:
            logger.warning("Error leyendo contenedores de Rodrigo Bueno: %s", e)

    # Ordenar por vecino más cercano (Nearest Neighbor) desde el acceso (Av. España / Calabria)
    entrance = (-34.61571, -58.35697)
    unvisited = list(rb_containers)
    sorted_stops = []
    if unvisited:
        curr = min(unvisited, key=lambda c: math.hypot(c["lat"] - entrance[0], c["lon"] - entrance[1]))
        unvisited.remove(curr)
        sorted_stops.append(curr)
        while unvisited:
            nxt = min(unvisited, key=lambda c: math.hypot(c["lat"] - curr["lat"], c["lon"] - curr["lon"]))
            unvisited.remove(nxt)
            sorted_stops.append(nxt)
            curr = nxt

    raw_stops = [(c["lat"], c["lon"]) for c in sorted_stops]
    waypoints: list[tuple[float, float]] = []
    for i in range(len(raw_stops)):
        p1 = raw_stops[i]
        waypoints.append(p1)
        if i < len(raw_stops) - 1:
            p2 = raw_stops[i + 1]
            d_lat = (p2[0] - p1[0]) * 111000.0
            d_lon = (p2[1] - p1[1]) * 91400.0
            dist = math.hypot(d_lat, d_lon)
            # Si hay un cambio de manzana (diagonal mayor a 25m), girar en la esquina de la intersección
            if dist > 25.0 and abs(d_lat) > 10.0 and abs(d_lon) > 10.0:
                corner = (p1[0], p2[1])
    site_ids = [c["id"] for c in sorted_stops]

    total_len_m = 0.0
    for i in range(1, len(waypoints)):
        d_lat = (waypoints[i][0] - waypoints[i - 1][0]) * 111000.0
        d_lon = (waypoints[i][1] - waypoints[i - 1][1]) * 111000.0 * math.cos(math.radians(waypoints[i][0]))
        total_len_m += math.hypot(d_lat, d_lon)

    street_seq = [c["address"] for c in sorted_stops]

    return TruckRoute(
        route_id="RODRIGO_BUENO",
        zone=1,
        service_name="Circuito Barrio Rodrigo Bueno - Puerto Madero (CLIBA)",
        waypoints=waypoints,
        site_ids=site_ids,
        total_distance_m=round(total_len_m, 2),
        collection_distance_m=round(total_len_m, 2),
        deadheading_distance_m=0.0,
        repeated_segments_count=0,
        street_sequence=street_seq,
        segments=[
            RouteSegment(
                street_name="AV. ESPAÑA / BARRIO RODRIGO BUENO",
                alt_start=1800,
                alt_end=2300,
                sentido="Continuo",
                service_name="Recolección Diferenciada Barrio Rodrigo Bueno",
                length_m=3500.0,
                zone=1,
                comuna="1",
                barrio="Puerto Madero",
            )
        ],
    )


def load_routes_from_csv(csv_path: Path | str | None = None) -> dict[str, TruckRoute]:
    global _CACHED_ROUTES
    if _CACHED_ROUTES is not None:
        return _CACHED_ROUTES

    if FOCUS_RODRIGO_BUENO_MODE:
        rb_route = build_rodrigo_bueno_route()
        routes = {rb_route.route_id: rb_route}
        _CACHED_ROUTES = routes
        logger.info(
            "Modo de prueba activo: Cargada ruta exclusiva %s con %d paradas continuas.",
            rb_route.route_id,
            len(rb_route.waypoints),
        )
        return routes
    if _CACHED_ROUTES is not None:
        return _CACHED_ROUTES

    if csv_path is None:
        candidates = [
            Path(__file__).resolve().parent.parent.parent.parent / "datos" / "rutas_recoleccion_residuos_humedos_clean.csv",
            Path("/app/datos/rutas_recoleccion_residuos_humedos_clean.csv"),
            Path("datos/rutas_recoleccion_residuos_humedos_clean.csv"),
        ]
        csv_path = next((p for p in candidates if p.exists()), candidates[0])

    if not Path(csv_path).exists():
        logger.warning("Archivo de rutas no encontrado en %s", csv_path)
        return {}

    routes: dict[str, TruckRoute] = {}

    with open(csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Encontrar columnas dinámicamente por prefijo
            k_ruta = next((k for k in row if k.startswith("cod_ruta_d")), None)
            k_calle = next((k for k in row if k.startswith("nomoficial")), None)
            k_izqini = next((k for k in row if k.startswith("alt_izqini")), None)
            k_derfin = next((k for k in row if k.startswith("alt_derfin")), None)
            k_sentido = next((k for k in row if k.startswith("sentido")), None)
            k_servic = next((k for k in row if k.startswith("nom_servic")), None)
            k_long = next((k for k in row if k.startswith("long")), None)
            k_zona = next((k for k in row if k.startswith("zona")), None)
            k_comuna = next((k for k in row if k.startswith("comuna")), None)
            k_barrio = next((k for k in row if k.startswith("barrio")), None)

            if not k_ruta or not row.get(k_ruta):
                continue

            # Normalizar ID de ruta (ej. '1184.000...' -> '1184')
            raw_id = row[k_ruta].split(".")[0].strip()
            if not raw_id:
                continue

            try:
                zona_int = int(float(row[k_zona])) if k_zona and row.get(k_zona) else 3
            except Exception:
                zona_int = 3

            service_name = row.get(k_servic, "Recoleccion Domiciliaria") if k_servic else "Recoleccion Domiciliaria"
            street_name = row.get(k_calle, "").strip()
            
            try:
                alt_start = int(float(row[k_izqini])) if k_izqini and row.get(k_izqini) else 0
            except Exception:
                alt_start = 0

            try:
                alt_end = int(float(row[k_derfin])) if k_derfin and row.get(k_derfin) else alt_start + 100
            except Exception:
                alt_end = alt_start + 100

            try:
                length_m = float(row[k_long]) if k_long and row.get(k_long) else 100.0
            except Exception:
                length_m = 100.0

            seg = RouteSegment(
                street_name=street_name,
                alt_start=alt_start,
                alt_end=alt_end,
                sentido=row.get(k_sentido, "Creciente") if k_sentido else "Creciente",
                service_name=service_name,
                length_m=length_m,
                zone=zona_int,
                comuna=row.get(k_comuna, "") if k_comuna else "",
                barrio=row.get(k_barrio, "") if k_barrio else "",
            )

            if raw_id not in routes:
                routes[raw_id] = TruckRoute(
                    route_id=raw_id,
                    zone=zona_int,
                    service_name=service_name,
                    segments=[seg],
                )
            else:
                routes[raw_id].segments.append(seg)

    if "RODRIGO_BUENO" not in routes:
        routes["RODRIGO_BUENO"] = build_rodrigo_bueno_route()

    _prepopulate_waypoints(routes)
    _CACHED_ROUTES = routes
    logger.info("Cargadas %d rutas de recoleccion desde CSV.", len(routes))
    return routes


def _prepopulate_waypoints(routes: dict[str, TruckRoute]) -> None:
    """Pre-carga los waypoints geográficos para cada ruta a partir de los contenedores de CABA."""
    import json
    import math
    from app.digital_twin.synthetic_data.simulation.truck_depots import get_depot_for_zone

    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "db" / "datos" / "contenedores_negros.json",
        Path("/app/db/datos/contenedores_negros.json"),
        Path("db/datos/contenedores_negros.json"),
    ]
    json_path = next((p for p in candidates if p.exists()), None)
    if json_path:
        try:
            with open(json_path, mode="r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            sites_list = []
            for feat in data.get("features", []):
                coords = feat.get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    props = feat.get("properties", {})
                    sites_list.append({
                        "id": props.get("Id", ""),
                        "address": props.get("DireccionNormalizada", ""),
                        "latitude": coords[1],
                        "longitude": coords[0],
                    })
            if sites_list:
                assign_sites_to_routes(sites_list, routes)
        except Exception as e:
            logger.warning("No se pudieron precargar sitios para rutas: %s", e)

    # Asegurar que todas las rutas tengan waypoints para trazar y transitar
    for r in routes.values():
        if not r.waypoints:
            depot = get_depot_for_zone(r.zone)
            idx = int(r.route_id) if r.route_id.isdigit() else hash(r.route_id)
            angle = (idx % 12) * (math.pi / 6.0)
            r.waypoints = [
                (round(depot.latitude + 0.005 * math.cos(angle), 5), round(depot.longitude + 0.005 * math.sin(angle), 5)),
                (round(depot.latitude + 0.009 * math.cos(angle + 0.4), 5), round(depot.longitude + 0.009 * math.sin(angle + 0.4), 5)),
                (round(depot.latitude + 0.006 * math.cos(angle + 0.8), 5), round(depot.longitude + 0.006 * math.sin(angle + 0.8), 5)),
                (depot.latitude, depot.longitude),
            ]


def assign_sites_to_routes(
    sites: list[dict], routes: dict[str, TruckRoute]
) -> dict[str, list[str]]:
    """
    Asigna cada sitio físico a su ruta de recolección (cod_ruta_d) correspondiente
    cruzando calle y rango de alturas catastrales, o por cercanía espacial.
    Retorna un diccionario {route_id: [site_id, site_id, ...]}.
    """
    # Crear índice rápido de tramos por nombre de calle normalizado
    # (calle_norm) -> list[(route_id, alt_min, alt_max, seg_idx)]
    street_index: dict[str, list[tuple[str, int, int, int]]] = {}
    for r_id, route in routes.items():
        if r_id == "RODRIGO_BUENO" and route.waypoints:
            continue
        route.site_ids.clear()
        route.waypoints.clear()
        for idx, seg in enumerate(route.segments):
            parsed = parse_street_address(seg.street_name)
            s_name = parsed[0] if parsed else seg.street_name.strip().upper()
            street_index.setdefault(s_name, []).append(
                (r_id, min(seg.alt_start, seg.alt_end), max(seg.alt_start, seg.alt_end), idx)
            )

    route_to_sites: dict[str, list[tuple[int, str, float, float]]] = {
        r_id: [] for r_id in routes
    }
    unassigned_sites = []

    for site in sites:
        site_id = str(site.get("id"))
        s_addr = site.get("address") or site.get("name") or ""
        parsed = parse_street_address(s_addr)
        lat = float(site.get("latitude", 0.0))
        lon = float(site.get("longitude", 0.0))

        matched_route_id: str | None = None
        matched_order: int = 0

        if parsed:
            st_name, st_num = parsed
            candidates = street_index.get(st_name, [])
            if st_num is not None:
                # Buscar cuadra que contenga la altura
                for r_id, a_min, a_max, seg_idx in candidates:
                    if a_min - 50 <= st_num <= a_max + 50:
                        matched_route_id = r_id
                        matched_order = seg_idx
                        break
            if not matched_route_id and candidates:
                matched_route_id = candidates[0][0]
                matched_order = candidates[0][3]

        if matched_route_id and matched_route_id in route_to_sites:
            route_to_sites[matched_route_id].append((matched_order, site_id, lat, lon))
        else:
            unassigned_sites.append((site_id, lat, lon))

    # Ordenar los sitios dentro de cada ruta por el orden de recorrido de sus cuadras
    result: dict[str, list[str]] = {}
    for r_id, items in route_to_sites.items():
        if r_id == "RODRIGO_BUENO" and routes[r_id].waypoints:
            result[r_id] = routes[r_id].site_ids
            continue
        items.sort(key=lambda x: x[0])
        site_ids = [item[1] for item in items]
        routes[r_id].site_ids = site_ids
        routes[r_id].waypoints = [(item[2], item[3]) for item in items if item[2] != 0.0]
        result[r_id] = site_ids

    return result

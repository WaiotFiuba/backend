from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from simulator.geo.addresses import (
    approximate_distance_meters,
    normalize_street_name,
    parse_street_address,
)

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
_CACHED_GREEN_ROUTES: dict[str, TruckRoute] | None = None
FOCUS_RODRIGO_BUENO_MODE = False


def _in_rodrigo_bueno(lat: float, lon: float) -> bool:
    # Cuadrilátero geográfico estricto del Barrio Rodrigo Bueno
    return -34.624 <= lat <= -34.615 and -58.362 <= lon <= -58.350


def _rodrigo_bueno_stops_from_file() -> list[dict]:
    """Paradas de Rodrigo Bueno leidas del archivo local de contenedores."""
    import json

    backend_root = Path(__file__).resolve().parent.parent.parent
    json_path = backend_root / "datos" / "digital_twin" / "contenedores_negros.json"
    rb_containers = []
    if json_path.exists():
        try:
            with open(json_path, mode="r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            for feat in data.get("features", []):
                props = feat.get("properties", {})
                coords = feat.get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    if _in_rodrigo_bueno(lat, lon):
                        rb_containers.append(
                            {
                                "id": str(props.get("Id", "")),
                                "address": props.get("DireccionNormalizada", ""),
                                "lat": lat,
                                "lon": lon,
                            }
                        )
        except Exception as e:
            logger.warning("Error leyendo contenedores de Rodrigo Bueno: %s", e)

    if not rb_containers:
        # Fallback de contenedores del circuito Rodrigo Bueno cuando el archivo JSON no está presente (ej: CI / GitHub Actions)
        rb_containers = [
            {
                "id": f"RB-{i:02d}",
                "address": f"Av. España / Rodrigo Bueno {i}",
                "lat": round(-34.6180 + (i % 6) * 0.0008, 6),
                "lon": round(-58.3580 + (i // 6) * 0.0010, 6),
            }
            for i in range(1, 25)
        ]
    return rb_containers


def build_rodrigo_bueno_route(sites: list[dict] | None = None) -> TruckRoute:
    """
    Construye la ruta optimizada paso a paso por los 36 contenedores del Barrio Rodrigo Bueno.
    Ordenada mediante Nearest Neighbor para garantizar un recorrido continuo y lógico.

    Con sites (sitios de la topologia: id, address, latitude, longitude) las
    paradas son los sitios que caen en el barrio; sin sites, los contenedores
    del archivo local.
    """
    import math

    if sites is None:
        rb_containers = _rodrigo_bueno_stops_from_file()
    else:
        rb_containers = [
            {
                "id": str(s["id"]),
                "address": s.get("address") or s.get("name") or "",
                "lat": float(s["latitude"]),
                "lon": float(s["longitude"]),
            }
            for s in sites
            if _in_rodrigo_bueno(float(s["latitude"]), float(s["longitude"]))
        ]

    # Ordenar por vecino más cercano (Nearest Neighbor) desde el acceso (Av. España / Calabria)
    entrance = (-34.61571, -58.35697)
    unvisited = list(rb_containers)
    sorted_stops = []
    if unvisited:
        curr = min(
            unvisited,
            key=lambda c: math.hypot(c["lat"] - entrance[0], c["lon"] - entrance[1]),
        )
        unvisited.remove(curr)
        sorted_stops.append(curr)
        while unvisited:
            nxt = min(
                unvisited,
                key=lambda c: math.hypot(
                    c["lat"] - curr["lat"], c["lon"] - curr["lon"]
                ),
            )
            unvisited.remove(nxt)
            sorted_stops.append(nxt)
            curr = nxt

    waypoints = [(c["lat"], c["lon"]) for c in sorted_stops]
    site_ids = [c["id"] for c in sorted_stops]
    street_seq = [c["address"] for c in sorted_stops]

    return TruckRoute(
        route_id="RODRIGO_BUENO",
        zone=1,
        service_name="Circuito Barrio Rodrigo Bueno - Puerto Madero (CLIBA)",
        waypoints=waypoints,
        site_ids=site_ids,
        total_distance_m=3500.0,
        collection_distance_m=3500.0,
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


def _fix_enie(street_name: str) -> str:
    """Corrige la "ñ" corrompida en el CSV fuente de circuitos de recoleccion."""
    return street_name.replace("±", "ñ")


def resolve_routes_csv_path() -> Path:
    """Ruta del CSV de rutas de recoleccion (la primera candidata que exista)."""
    backend_root = Path(__file__).resolve().parent.parent.parent
    base_datos = backend_root / "datos"
    candidates = [
        base_datos / "simulator" / "routes" / "rutas_recoleccion_residuos_humedos.csv",
        base_datos
        / "simulator"
        / "routes"
        / "rutas_recoleccion_residuos_humedos_clean.csv",
        base_datos / "rutas_recoleccion_residuos_humedos.csv",
        base_datos / "rutas_recoleccion_residuos_humedos_clean.csv",
        Path("/app/datos/simulator/routes/rutas_recoleccion_residuos_humedos.csv"),
        Path("/app/datos/rutas_recoleccion_residuos_humedos.csv"),
        Path("/app/datos/rutas_recoleccion_residuos_humedos_clean.csv"),
        Path("datos/simulator/routes/rutas_recoleccion_residuos_humedos.csv"),
        Path("datos/rutas_recoleccion_residuos_humedos.csv"),
        Path("datos/rutas_recoleccion_residuos_humedos_clean.csv"),
    ]
    return next((p for p in candidates if p.exists()), candidates[0])


def load_routes_from_csv(
    csv_path: Path | str | None = None,
    container_data_files: tuple[str, ...] = ("contenedores_negros.json",),
) -> dict[str, TruckRoute]:
    global _CACHED_ROUTES
    use_default_cache = csv_path is None
    if use_default_cache and _CACHED_ROUTES is not None:
        return _CACHED_ROUTES

    if use_default_cache and FOCUS_RODRIGO_BUENO_MODE:
        rb_route = build_rodrigo_bueno_route()
        routes = {rb_route.route_id: rb_route}
        _CACHED_ROUTES = routes
        logger.info(
            "Modo de prueba activo: Cargada ruta exclusiva %s con %d paradas continuas.",
            rb_route.route_id,
            len(rb_route.waypoints),
        )
        return routes

    if csv_path is None:
        csv_path = resolve_routes_csv_path()

    routes = _parse_routes_csv(csv_path)
    if not routes:
        return {}

    if "RODRIGO_BUENO" not in routes:
        routes["RODRIGO_BUENO"] = build_rodrigo_bueno_route()

    _prepopulate_waypoints(routes, container_data_files)
    if use_default_cache:
        _CACHED_ROUTES = routes
    logger.info("Cargadas %d rutas de recoleccion desde CSV.", len(routes))
    return routes


def _parse_routes_csv(csv_path: Path | str) -> dict[str, TruckRoute]:
    """Rutas con sus tramos de calle tal como vienen en el CSV, sin sitios."""
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

            service_name = (
                row.get(k_servic, "Recoleccion Domiciliaria")
                if k_servic
                else "Recoleccion Domiciliaria"
            )
            street_name = _fix_enie(row.get(k_calle, "").strip())

            try:
                alt_start = (
                    int(float(row[k_izqini])) if k_izqini and row.get(k_izqini) else 0
                )
            except Exception:
                alt_start = 0

            try:
                alt_end = (
                    int(float(row[k_derfin]))
                    if k_derfin and row.get(k_derfin)
                    else alt_start + 100
                )
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

    return routes


def load_green_routes_from_csv(
    csv_path: Path | str | None = None,
) -> dict[str, TruckRoute]:
    global _CACHED_GREEN_ROUTES
    use_default_cache = csv_path is None
    if use_default_cache and _CACHED_GREEN_ROUTES is not None:
        return _CACHED_GREEN_ROUTES

    if csv_path is None:
        csv_path = resolve_green_routes_csv_path()

    routes = load_routes_from_csv(
        csv_path,
        container_data_files=("contenedores_verdes.json",),
    )
    if use_default_cache:
        _CACHED_GREEN_ROUTES = routes
    return routes


def resolve_green_routes_csv_path() -> Path:
    """Ruta del CSV de rutas de residuos secos (la primera candidata que exista)."""
    backend_root = Path(__file__).resolve().parent.parent.parent
    base_datos = backend_root / "datos"
    candidates = [
        base_datos / "simulator" / "routes" / "rutas_recoleccion_residuos_secos.csv",
        base_datos
        / "simulator"
        / "routes"
        / "rutas_recoleccion_residuos_secos_clean.csv",
        base_datos / "rutas_recoleccion_residuos_secos.csv",
        base_datos / "rutas_recoleccion_residuos_secos_clean.csv",
        Path("/app/datos/simulator/routes/rutas_recoleccion_residuos_secos.csv"),
        Path("/app/datos/rutas_recoleccion_residuos_secos.csv"),
        Path("/app/datos/rutas_recoleccion_residuos_secos_clean.csv"),
        Path("datos/simulator/routes/rutas_recoleccion_residuos_secos.csv"),
        Path("datos/rutas_recoleccion_residuos_secos.csv"),
        Path("datos/rutas_recoleccion_residuos_secos_clean.csv"),
    ]
    return next((p for p in candidates if p.exists()), candidates[0])


def load_all_collection_routes() -> dict[str, TruckRoute]:
    routes = load_routes_from_csv().copy()
    routes.update(load_green_routes_from_csv())
    return routes


def build_collection_routes(
    wet_sites: list[dict], green_sites: list[dict]
) -> dict[str, TruckRoute]:
    """
    Rutas de la sesion con los sitios de la topologia que manda el backend.

    wet_sites van a las rutas de humedos (y a Rodrigo Bueno si caen en el
    barrio); green_sites, a las de secos. Cada sitio es un dict con id,
    address, name, latitude y longitude, y las paradas de las rutas quedan con
    el id del sitio tal cual (el mismo con el que el motor agrupa los
    contenedores). Se arman de cero en cada llamada (~0,6 s con los ~22.000
    sitios reales), asi que cada sesion tiene sus propias rutas.
    """
    rb_route = build_rodrigo_bueno_route(wet_sites)
    if FOCUS_RODRIGO_BUENO_MODE:
        routes = {rb_route.route_id: rb_route}
    else:
        # Los sitios del barrio los recorre solo su circuito, no tambien el
        # de la calle que les toque por direccion.
        rb_site_ids = set(rb_route.site_ids)
        routes = _parse_routes_csv(resolve_routes_csv_path())
        assign_sites_to_routes(
            [s for s in wet_sites if str(s["id"]) not in rb_site_ids], routes
        )
        green_routes = _parse_routes_csv(resolve_green_routes_csv_path())
        assign_sites_to_routes(green_sites, green_routes)
        routes.update(green_routes)
        if rb_route.site_ids:
            routes[rb_route.route_id] = rb_route
    _fill_fallback_waypoints(routes)

    logger.info(
        "Armadas %d rutas de recoleccion con %d sitios de la topologia.",
        len(routes),
        sum(len(r.site_ids) for r in routes.values()),
    )
    return routes


def _prepopulate_waypoints(
    routes: dict[str, TruckRoute],
    container_data_files: tuple[str, ...] = ("contenedores_negros.json",),
) -> None:
    """Pre-carga los waypoints geográficos para cada ruta a partir de los contenedores de CABA."""
    import json

    project_root = Path(__file__).resolve().parent.parent.parent
    candidates = []
    for filename in container_data_files:
        candidates.extend(
            [
                project_root / "datos" / "digital_twin" / filename,
                project_root / "datos" / filename,
                project_root / "db" / "datos" / filename,
                Path("/app/datos/digital_twin") / filename,
                Path("/app/datos") / filename,
                Path("/app/db/datos") / filename,
                Path("datos/digital_twin") / filename,
                Path("datos") / filename,
                Path("db/datos") / filename,
            ]
        )
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
                    sites_list.append(
                        {
                            "id": props.get("Id", ""),
                            "address": props.get("DireccionNormalizada", ""),
                            "latitude": coords[1],
                            "longitude": coords[0],
                        }
                    )
            if sites_list:
                assign_sites_to_routes(sites_list, routes)
        except Exception as e:
            logger.warning("No se pudieron precargar sitios para rutas: %s", e)

    _fill_fallback_waypoints(routes)


def _fill_fallback_waypoints(routes: dict[str, TruckRoute]) -> None:
    """Asegurar que todas las rutas tengan waypoints para trazar y transitar."""
    import math

    from simulator.trucks.truck_depots import (
        get_depot_for_zone,
    )

    for r in routes.values():
        if not r.waypoints:
            depot = get_depot_for_zone(r.zone)
            idx = int(r.route_id) if r.route_id.isdigit() else hash(r.route_id)
            angle = (idx % 12) * (math.pi / 6.0)
            r.waypoints = [
                (
                    round(depot.latitude + 0.005 * math.cos(angle), 5),
                    round(depot.longitude + 0.005 * math.sin(angle), 5),
                ),
                (
                    round(depot.latitude + 0.009 * math.cos(angle + 0.4), 5),
                    round(depot.longitude + 0.009 * math.sin(angle + 0.4), 5),
                ),
                (
                    round(depot.latitude + 0.006 * math.cos(angle + 0.8), 5),
                    round(depot.longitude + 0.006 * math.sin(angle + 0.8), 5),
                ),
                (depot.latitude, depot.longitude),
            ]


_FALLBACK_ORDER = (
    10**9
)  # va al final del recorrido de su ruta, no interfiere el orden por calle


def _assign_by_proximity(
    unassigned_sites: list[tuple[str, float, float]],
    routes: dict[str, TruckRoute],
    route_to_sites: dict[str, list[tuple[int, str, float, float]]],
) -> None:
    """Asigna sitios sin calle matcheada a la ruta cuyo centroide (de los
    sitios ya asignados por calle, o su primer waypoint, o el depósito de su
    zona como último recurso) está geográficamente más cerca."""
    from simulator.trucks.truck_depots import (
        get_depot_for_zone,
    )

    route_centers: dict[str, tuple[float, float]] = {}
    for r_id, route in routes.items():
        if r_id == "RODRIGO_BUENO":
            continue
        pts = [
            (lat, lon)
            for _, _, lat, lon in route_to_sites.get(r_id, [])
            if lat != 0.0 or lon != 0.0
        ]
        if pts:
            route_centers[r_id] = (
                sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts),
            )
        elif route.waypoints:
            route_centers[r_id] = route.waypoints[0]
        else:
            depot = get_depot_for_zone(route.zone)
            route_centers[r_id] = (depot.latitude, depot.longitude)

    if not route_centers:
        return

    for site_id, lat, lon in unassigned_sites:
        if lat == 0.0 and lon == 0.0:
            continue
        best_r_id = min(
            route_centers,
            key=lambda r_id: approximate_distance_meters(
                lat, lon, *route_centers[r_id]
            ),
        )
        route_to_sites[best_r_id].append((_FALLBACK_ORDER, site_id, lat, lon))


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
            # El nombre de un tramo de ruta nunca trae la altura embebida (vive
            # en columnas separadas), así que parse_street_address siempre
            # devolvería None acá — se usa normalize_street_name, que limpia
            # "AV."/comas/títulos sin exigir un número.
            s_name = normalize_street_name(seg.street_name) or (
                seg.street_name.strip().upper()
            )
            street_index.setdefault(s_name, []).append(
                (
                    r_id,
                    min(seg.alt_start, seg.alt_end),
                    max(seg.alt_start, seg.alt_end),
                    idx,
                )
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

    if unassigned_sites:
        _assign_by_proximity(unassigned_sites, routes, route_to_sites)

    # Ordenar los sitios dentro de cada ruta por el orden de recorrido de sus cuadras
    result: dict[str, list[str]] = {}
    for r_id, items in route_to_sites.items():
        if r_id == "RODRIGO_BUENO" and routes[r_id].waypoints:
            result[r_id] = routes[r_id].site_ids
            continue
        items.sort(key=lambda x: x[0])
        site_ids = [item[1] for item in items]
        routes[r_id].site_ids = site_ids
        routes[r_id].waypoints = [
            (item[2], item[3]) for item in items if item[2] != 0.0
        ]
        result[r_id] = site_ids

    return result

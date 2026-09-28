from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from geoalchemy2.elements import WKTElement
from shapely.geometry import LineString, shape
from shapely.strtree import STRtree
from sqlalchemy import bindparam, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.site import Site

logger = logging.getLogger(__name__)


@dataclass
class StreetSegment:
    geometry: LineString
    nomoficial: str = ""
    sentido: str = "DOBLE"  # "CRECIENTE", "DECRECIENTE", "DOBLE"
    tipo_c: str = ""
    coords: tuple[tuple[float, float], ...] = field(default_factory=tuple)

    def __post_init__(self):
        if not self.coords and self.geometry is not None:
            self.coords = tuple(self.geometry.coords)

    @property
    def is_avenida(self) -> bool:
        nom = self.nomoficial.upper()
        tipo = self.tipo_c.upper()
        return tipo == "AVENIDA" or "AV." in nom or "AVENIDA" in nom


class StreetSpatialIndex:
    def __init__(self, segments: Sequence[StreetSegment]) -> None:
        self.segments = list(segments)
        for s in self.segments:
            if not s.coords and s.geometry is not None:
                s.coords = tuple(s.geometry.coords)
        self.geometries = [s.geometry for s in self.segments]
        self.tree = STRtree(self.geometries) if self.geometries else None

    @classmethod
    def from_geojson(cls, filepath: Path | str) -> StreetSpatialIndex:
        path = Path(filepath)
        if not path.exists():
            logger.warning("Archivo de calles no encontrado en %s", path)
            return cls([])

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        segments: list[StreetSegment] = []
        for feature in data.get("features", []):
            geom_data = feature.get("geometry")
            if not geom_data:
                continue
            geom = shape(geom_data)
            props = feature.get("properties", {})
            nom = (
                str(props.get("nomoficial") or props.get("nom_mapa") or "")
                .upper()
                .strip()
            )
            sentido = str(props.get("sentido") or "DOBLE").upper().strip()
            tipo_c = str(props.get("tipo_c") or "").upper().strip()

            if isinstance(geom, LineString) and len(geom.coords) >= 2:
                segments.append(
                    StreetSegment(
                        geometry=geom,
                        nomoficial=nom,
                        sentido=sentido,
                        tipo_c=tipo_c,
                        coords=tuple(geom.coords),
                    )
                )

        logger.info(
            "Índice espacial de calles construido con %d tramos.", len(segments)
        )
        return cls(segments)

    def is_avenida_segment(self, idx: int) -> bool:
        if 0 <= idx < len(self.segments):
            return self.segments[idx].is_avenida
        return False

    def get_street_segment_indices_batch(
        self, lons: list[float], lats: list[float]
    ) -> list[int]:
        """
        Retorna el índice del tramo de calle (cuadra) más cercano para cada coordenada.
        """
        if not self.tree or not self.segments or not lons:
            return [0] * len(lons)
        points = shapely.points(lons, lats)
        nearest_indices = self.tree.nearest(points)
        return [int(idx) if idx is not None else 0 for idx in nearest_indices]

    def infer_load_side_batch(self, lons: list[float], lats: list[float]) -> list[str]:
        """
        Determina de forma vectorizada y ultra rápida si cada punto se encuentra a la
        IZQUIERDA o DERECHA del sentido de marcha de la calle más cercana.
        """
        if not self.tree or not self.segments or not lons:
            return ["BILATERAL"] * len(lons)

        points = shapely.points(lons, lats)
        nearest_indices = self.tree.nearest(points)

        results: list[str] = []
        for i in range(len(lons)):
            lon = lons[i]
            lat = lats[i]
            idx = nearest_indices[i]
            if idx is None or idx >= len(self.segments):
                results.append("BILATERAL")
                continue

            street = self.segments[idx]
            coords = street.coords
            if len(coords) < 2:
                results.append("BILATERAL")
                continue

            # Encontrar el sub-segmento (A -> B) más cercano al punto
            min_dist_sq = float("inf")
            best_a, best_b = coords[0], coords[1]
            px, py = lon, lat

            for j in range(len(coords) - 1):
                (ax, ay), (bx, by) = coords[j], coords[j + 1]
                abx, aby = bx - ax, by - ay
                ab_len_sq = abx * abx + aby * aby
                if ab_len_sq == 0:
                    t = 0.0
                else:
                    t = max(
                        0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab_len_sq)
                    )
                proj_x = ax + t * abx
                proj_y = ay + t * aby
                dist_sq = (px - proj_x) ** 2 + (py - proj_y) ** 2
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    best_a, best_b = (ax, ay), (bx, by)

            ax, ay = best_a
            bx, by = best_b
            cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
            is_left = cross > 0

            sentido = street.sentido
            if sentido == "DOBLE":
                results.append("BILATERAL")
            elif sentido == "DECRECIENTE":
                results.append("DERECHA" if is_left else "IZQUIERDA")
            else:
                # Para calles CRECIENTES o sin sentido especificado
                results.append("IZQUIERDA" if is_left else "DERECHA")

        return results

    def infer_load_side(self, lon: float, lat: float) -> str:
        return self.infer_load_side_batch([lon], [lat])[0]


def _get_meters_per_degree(avg_lat: float) -> tuple[float, float]:
    """Retorna (meters_per_lat, meters_per_lon) calculados geodésicamente para cualquier latitud."""
    meters_per_lat = 111320.0
    meters_per_lon = 111320.0 * max(0.01, math.cos(math.radians(avg_lat)))
    return meters_per_lat, meters_per_lon


def _spatial_cluster(
    containers: list[Container], distance_threshold_m: float = 20.0
) -> list[list[Container]]:
    """
    Agrupamiento espacial por proximidad métrica mediante Spatial Grid Hashing (O(N)).
    Calcula distancias geodésicas dinámicas según la latitud media (válido para cualquier ciudad).
    """
    if not containers:
        return []
    if len(containers) == 1:
        return [containers]

    avg_lat = sum(c.latitude for c in containers) / len(containers)
    meters_per_lat, meters_per_lon = _get_meters_per_degree(avg_lat)

    cell_lat_deg = distance_threshold_m / meters_per_lat
    cell_lon_deg = distance_threshold_m / meters_per_lon

    grid: dict[tuple[int, int], list[Container]] = {}
    for c in containers:
        gx = int(np.floor(c.longitude / cell_lon_deg))
        gy = int(np.floor(c.latitude / cell_lat_deg))
        grid.setdefault((gx, gy), []).append(c)

    visited: set[int] = set()
    clusters: list[list[Container]] = []
    threshold_sq = distance_threshold_m * distance_threshold_m

    for c in containers:
        if c.id in visited:
            continue
        gx = int(np.floor(c.longitude / cell_lon_deg))
        gy = int(np.floor(c.latitude / cell_lat_deg))

        current_cluster = [c]
        visited.add(c.id)

        # Buscar en las 9 celdas vecinas (3x3)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbor_cell = (gx + dx, gy + dy)
                for other in grid.get(neighbor_cell, []):
                    if other.id not in visited:
                        dy_m = (c.latitude - other.latitude) * meters_per_lat
                        dx_m = (c.longitude - other.longitude) * meters_per_lon
                        dist_sq = dx_m * dx_m + dy_m * dy_m
                        if dist_sq <= threshold_sq:
                            current_cluster.append(other)
                            visited.add(other.id)

        clusters.append(current_cluster)

    return clusters


def _split_by_distance(
    containers: list[Container], max_distance_m: float = 100.0
) -> list[list[Container]]:
    """
    Divide una lista de contenedores en sub-grupos si la distancia física entre ellos
    supera max_distance_m (100 metros) o si pertenecen a cuadras/alturas numéricas diferentes
    (diferencia >= 100 números de calle), garantizando que una calle larga (ej. del 1600 al 2600)
    se divida en múltiples sitios individuales por cuadra.
    """
    if len(containers) <= 1:
        return [containers]

    from app.digital_twin.synthetic_data.generators.street_pairing import (
        parse_street_address,
    )

    avg_lat = sum(c.latitude for c in containers) / len(containers)
    meters_per_lat, meters_per_lon = _get_meters_per_degree(avg_lat)

    # Extraer números de calle si están disponibles
    numbers = []
    for c in containers:
        parsed = parse_street_address(c.address) if c.address else None
        numbers.append(parsed[1] if parsed else None)

    threshold_sq = max_distance_m * max_distance_m
    n = len(containers)
    visited = [False] * n
    clusters: list[list[Container]] = []

    for i in range(n):
        if visited[i]:
            continue
        cluster: list[Container] = []
        queue = [i]
        visited[i] = True

        while queue:
            curr = queue.pop(0)
            c_curr = containers[curr]
            cluster.append(c_curr)

            for j in range(n):
                if not visited[j]:
                    c_other = containers[j]
                    dy_m = (c_curr.latitude - c_other.latitude) * meters_per_lat
                    dx_m = (c_curr.longitude - c_other.longitude) * meters_per_lon
                    dist_sq = dx_m * dx_m + dy_m * dy_m

                    # Verificar distancia espacial (<= 100m)
                    is_near_spatial = dist_sq <= threshold_sq

                    # Verificar diferencia numérica de altura si ambos tienen número (misma cuadra: diff < 100)
                    num_curr = numbers[curr]
                    num_other = numbers[j]
                    is_near_number = True
                    if num_curr is not None and num_other is not None:
                        is_near_number = abs(num_curr - num_other) < 100

                    if is_near_spatial and is_near_number:
                        visited[j] = True
                        queue.append(j)

        clusters.append(cluster)

    return clusters


def _is_bilateral_container_type(ctype: ContainerType | None) -> bool:
    if not ctype:
        return False
    name = (ctype.name or "").upper()
    desc = (ctype.description or "").upper()
    return (
        "BILATERAL" in name
        or "SOTERRADO" in name
        or "BILATERAL" in desc
        or "SOTERRADO" in desc
    )


async def cluster_and_create_sites(
    db: AsyncSession,
    calles_path: Path | str | None = None,
    distance_threshold_m: float = 20.0,
    clear_existing: bool = True,
) -> int:
    """
    Agrupa todos los contenedores por cercanía física real (<= distance_threshold_m)
    y por tipo de residuo/contenedor, creando los Sitios físicos correspondientes.
    """
    has_streets = False
    if calles_path:
        cp = Path(calles_path)
        if cp.exists() and cp.is_file():
            has_streets = True
            calles_path = cp

    if has_streets:
        print(
            f" -> [Nivel 2] Construyendo índice espacial de calles desde {calles_path}..."
        )
        street_index = StreetSpatialIndex.from_geojson(calles_path)
    else:
        street_index = None
        print(
            f" -> [Nivel 3] Sin archivo de calles. Ejecutando clustering métrico universal (<= {distance_threshold_m}m)..."
        )

    # 1. Cargar todos los contenedores con sus tipos de contenedor y residuo
    print(" -> Consultando contenedores de la base de datos...")
    stmt = select(Container).options(
        selectinload(Container.container_type).selectinload(ContainerType.waste_types)
    )
    result = await db.execute(stmt)
    containers = result.scalars().all()

    if not containers:
        logger.info("No hay contenedores para agrupar.")
        return 0

    print(
        f" -> Procesando {len(containers)} contenedores para agrupamiento espacial..."
    )

    # 2. Agrupar contenedores monotipo por (waste_type_id, container_type_id)
    groups_by_type: dict[tuple[int | None, int | None], list[Container]] = {}
    for c in containers:
        ctype = c.container_type
        wtype = ctype.waste_types[0] if ctype and ctype.waste_types else None
        w_id = wtype.id if wtype else None
        ct_id = ctype.id if ctype else None
        key = (w_id, ct_id)
        groups_by_type.setdefault(key, []).append(c)

    all_cluster_items: list[tuple[int | None, str, list[Container]]] = []

    for (w_id, ct_id), c_list in groups_by_type.items():
        if street_index is not None:
            first_c = c_list[0]
            is_bilateral = _is_bilateral_container_type(first_c.container_type)

            c_lons = [c.longitude for c in c_list]
            c_lats = [c.latitude for c in c_list]
            seg_indices = street_index.get_street_segment_indices_batch(c_lons, c_lats)
            sides = street_index.infer_load_side_batch(c_lons, c_lats)

            cuadra_subgroups: dict[tuple[int, str], list[Container]] = {}
            for c, seg_idx, side in zip(c_list, seg_indices, sides):
                is_ave = street_index.is_avenida_segment(seg_idx)
                final_side = "BILATERAL" if (is_bilateral and not is_ave) else side
                cuadra_subgroups.setdefault((seg_idx, final_side), []).append(c)

            for (seg_idx, side), sub_cluster in cuadra_subgroups.items():
                for split_cluster in _split_by_distance(
                    sub_cluster, max_distance_m=100.0
                ):
                    all_cluster_items.append((w_id, side, split_cluster))
        else:
            # Nivel 3: Agrupamiento métrico puro por proximidad espacial
            metric_clusters = _spatial_cluster(
                c_list, distance_threshold_m=distance_threshold_m
            )
            for split_cluster in metric_clusters:
                all_cluster_items.append((w_id, "DESCONOCIDO", split_cluster))

    if not all_cluster_items:
        return 0

    print(
        f" -> {len(all_cluster_items)} sitios identificados por cuadra, lado de acera y tipo de contenedor."
    )

    # 3. Cálculo de centroides para cada Sitio
    cluster_lons = [
        float(np.mean([c.longitude for c in cluster]))
        for _, _, cluster in all_cluster_items
    ]
    cluster_lats = [
        float(np.mean([c.latitude for c in cluster]))
        for _, _, cluster in all_cluster_items
    ]

    # 4. Limpieza de sitios previos si clear_existing es True
    if clear_existing:
        print(" -> Limpiando sitios previos para re-clustering limpio...")
        await db.execute(
            update(Container.__table__).values(site_id=None, site_name=None)
        )
        await db.execute(delete(Site.__table__))
        await db.flush()

    print(" -> Creando entidades Site en la base de datos...")
    sites_to_create: list[Site] = []
    for i, (w_id, load_side, cluster) in enumerate(all_cluster_items):
        avg_lon = cluster_lons[i]
        avg_lat = cluster_lats[i]
        first = cluster[0]
        addr = first.address or f"Lat {avg_lat:.5f}, Lon {avg_lon:.5f}"
        ctype_name = first.container_type.name if first.container_type else "RSU"
        site_name = f"Sitio {ctype_name} - {addr}"

        site = Site(
            name=site_name,
            description=f"Sitio con {len(cluster)} contenedor(es)",
            address=first.address,
            latitude=avg_lat,
            longitude=avg_lon,
            geom=WKTElement(f"POINT({avg_lon} {avg_lat})", srid=4326),
            load_side_category=load_side,
            waste_type_id=w_id,
        )
        sites_to_create.append(site)

    # Inserción masiva de Sites en lotes
    BATCH_SIZE = 2000
    for i in range(0, len(sites_to_create), BATCH_SIZE):
        batch = sites_to_create[i : i + BATCH_SIZE]
        db.add_all(batch)
        await db.flush()

    # 5. Actualización masiva de containers.site_id usando Core table update
    print(" -> Vinculando contenedores a sus respectivos sitios...")
    container_updates = []
    for site, (_, _, cluster) in zip(sites_to_create, all_cluster_items):
        for c in cluster:
            c.site_id = site.id
            c.site_name = site.name
            container_updates.append(
                {"b_id": c.id, "b_site_id": site.id, "b_site_name": site.name}
            )

    stmt_update = (
        update(Container.__table__)
        .where(Container.__table__.c.id == bindparam("b_id"))
        .values(site_id=bindparam("b_site_id"), site_name=bindparam("b_site_name"))
    )

    for i in range(0, len(container_updates), BATCH_SIZE):
        batch = container_updates[i : i + BATCH_SIZE]
        await db.execute(stmt_update, batch)

    await db.commit()
    print(
        f"[OK] Clustering completado con éxito: {len(sites_to_create)} sitios creados."
    )
    return len(sites_to_create)

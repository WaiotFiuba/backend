from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import shapely.wkt
from shapely.geometry import Point
from shapely.strtree import STRtree
from shapely.ops import transform
import pyproj


class SiteCapacityService:
    def __init__(self, csv_path: Optional[str] = None):
        if csv_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            csv_path = os.path.join(base_dir, "datos", "restricciones_contenedores.csv")

        self.csv_path = csv_path
        self._tree: Optional[STRtree] = None
        self._geoms_utm: list[Any] = []
        self._max_containers_list: list[int] = []
        self._wgs84_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32721", always_xy=True).transform
        self._initialized = False

        if os.path.exists(self.csv_path):
            self._load_data()

    def _load_data(self) -> None:
        try:
            df = pd.read_csv(self.csv_path, sep=";", encoding="utf-8")
            wkt_col = "geometry_wkt" if "geometry_wkt" in df.columns else "WKT"
            
            self._geoms_utm = []
            self._max_containers_list = []
            
            for _, row in df.iterrows():
                wkt_str = row[wkt_col]
                try:
                    g = shapely.wkt.loads(wkt_str)
                    g_utm = transform(self._wgs84_to_utm, g)
                    self._geoms_utm.append(g_utm)
                    self._max_containers_list.append(int(row.get("MAX_CONTENEDORES", 2)))
                except Exception:
                    continue

            if self._geoms_utm:
                self._tree = STRtree(self._geoms_utm)
                self._initialized = True
        except Exception:
            self._initialized = False

    def evaluate_site(
        self, latitude: float | None, longitude: float | None, current_containers: int = 0, default_cap: int = 2
    ) -> Tuple[int, bool]:
        if latitude is None or longitude is None or not self._initialized or self._tree is None:
            max_containers = default_cap
            return max_containers, max_containers > current_containers

        try:
            x_utm, y_utm = self._wgs84_to_utm(longitude, latitude)
            pt_utm = Point(x_utm, y_utm)
            cand_indices = self._tree.query(pt_utm.buffer(35.0))

            if len(cand_indices) == 0:
                max_containers = default_cap
            else:
                # En una esquina o cuadra, existen tramos para ambas márgenes (izq/der) e intersecciones.
                # La capacidad del sitio es la máxima de los tramos contiguos dentro de 25 metros.
                nearby = [i for i in cand_indices if self._geoms_utm[i].distance(pt_utm) <= 25.0]
                if nearby:
                    max_containers = max(self._max_containers_list[i] for i in nearby)
                else:
                    best_idx = min(cand_indices, key=lambda i: self._geoms_utm[i].distance(pt_utm))
                    max_containers = self._max_containers_list[best_idx]
        except Exception:
            max_containers = default_cap

        puede_ingresar = max_containers > current_containers
        return max_containers, puede_ingresar

    def get_capacity_for_coordinates(
        self, latitude: float | None, longitude: float | None, default_cap: int = 2
    ) -> int:
        cap, _ = self.evaluate_site(latitude, longitude, current_containers=0, default_cap=default_cap)
        return cap


@lru_cache(maxsize=1)
def get_site_capacity_service(csv_path: Optional[str] = None) -> SiteCapacityService:
    return SiteCapacityService(csv_path=csv_path)

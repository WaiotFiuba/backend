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
        self._indice_espacial_tramos: Optional[STRtree] = None
        self._geometrias_tramos_utm: list[Any] = []
        self._capacidades_maximas_tramos: list[int] = []
        self._conversor_wgs84_a_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32721", always_xy=True).transform
        self._datos_cargados = False

        if os.path.exists(self.csv_path):
            self._load_data()

    def _load_data(self) -> None:
        try:
            dataframe_restricciones = pd.read_csv(self.csv_path, sep=";", encoding="utf-8")
            columna_geometria = "geometry_wkt" if "geometry_wkt" in dataframe_restricciones.columns else "WKT"

            self._geometrias_tramos_utm = []
            self._capacidades_maximas_tramos = []

            for _, fila in dataframe_restricciones.iterrows():
                geometria_wkt_texto = fila[columna_geometria]
                try:
                    geometria_cruda = shapely.wkt.loads(geometria_wkt_texto)
                    geometria_tramo_utm = transform(self._conversor_wgs84_a_utm, geometria_cruda)
                    capacidad_maxima_tramo = int(fila.get("MAX_CONTENEDORES", 2))

                    self._geometrias_tramos_utm.append(geometria_tramo_utm)
                    self._capacidades_maximas_tramos.append(capacidad_maxima_tramo)
                except Exception:
                    continue

            if self._geometrias_tramos_utm:
                self._indice_espacial_tramos = STRtree(self._geometrias_tramos_utm)
                self._datos_cargados = True
        except Exception:
            self._datos_cargados = False

    def evaluate_site(
        self,
        latitude: float | None,
        longitude: float | None,
        current_containers: int = 0,
        default_cap: int = 2,
    ) -> Tuple[int, bool]:
        if latitude is None or longitude is None or not self._datos_cargados or self._indice_espacial_tramos is None:
            capacidad_maxima = default_cap
            return capacidad_maxima, capacidad_maxima > current_containers

        try:
            coord_x_utm, coord_y_utm = self._conversor_wgs84_a_utm(longitude, latitude)
            punto_sitio_utm = Point(coord_x_utm, coord_y_utm)
            
            # Buscar tramos viales dentro de un radio de tolerancia de 35 metros
            indices_tramos_candidatos = self._indice_espacial_tramos.query(punto_sitio_utm.buffer(35.0))

            if len(indices_tramos_candidatos) == 0:
                capacidad_maxima_permitida = default_cap
            else:
                # En una esquina existen tramos para ambas márgenes e intersecciones contiguas (<= 25m).
                # Tomamos la capacidad del tramo habilitado más favorable en la esquina.
                indices_tramos_contiguos = [
                    idx for idx in indices_tramos_candidatos
                    if self._geometrias_tramos_utm[idx].distance(punto_sitio_utm) <= 25.0
                ]

                if indices_tramos_contiguos:
                    capacidad_maxima_permitida = max(
                        self._capacidades_maximas_tramos[idx] for idx in indices_tramos_contiguos
                    )
                else:
                    indice_tramo_mas_cercano = min(
                        indices_tramos_candidatos,
                        key=lambda idx: self._geometrias_tramos_utm[idx].distance(punto_sitio_utm),
                    )
                    capacidad_maxima_permitida = self._capacidades_maximas_tramos[indice_tramo_mas_cercano]
        except Exception:
            capacidad_maxima_permitida = default_cap

        tiene_cupo_disponible = capacidad_maxima_permitida > current_containers
        return capacidad_maxima_permitida, tiene_cupo_disponible

    def get_capacity_for_coordinates(
        self, latitude: float | None, longitude: float | None, default_cap: int = 2
    ) -> int:
        capacidad_maxima, _ = self.evaluate_site(
            latitude, longitude, current_containers=0, default_cap=default_cap
        )
        return capacidad_maxima


@lru_cache(maxsize=1)
def get_site_capacity_service(csv_path: Optional[str] = None) -> SiteCapacityService:
    return SiteCapacityService(csv_path=csv_path)

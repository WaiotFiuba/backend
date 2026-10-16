from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Optional, Tuple
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point
from shapely.strtree import STRtree
import pyproj


# Resultado del caché cuando no hay un tramo de calle cerca del sitio.
_NO_SEGMENT = object()


class SiteCapacityService:
    def __init__(self, csv_path: Optional[str] = None):
        if csv_path is None:
            base_dir = os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            csv_path = os.path.join(
                base_dir, "datos", "digital_twin", "restricciones_contenedores.csv"
            )

        self.csv_path = csv_path
        self._indice_espacial_tramos: Optional[STRtree] = None
        self._geometrias_tramos_utm: list[Any] = []
        self._capacidades_maximas_tramos: list[int] = []
        self._conversor_wgs84_a_utm = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:32721", always_xy=True
        ).transform
        self._datos_cargados = False
        # Capacidad del tramo de calle por ubicación: depende solo de dónde está
        # el sitio, así que se calcula una vez por ubicación.
        self._capacidad_por_ubicacion: dict[tuple[float, float], Any] = {}

        if os.path.exists(self.csv_path):
            self._load_data()

    def _load_data(self) -> None:
        try:
            dataframe_restricciones = pd.read_csv(
                self.csv_path, sep=";", encoding="utf-8"
            )
            columna_geometria = (
                "geometry_wkt"
                if "geometry_wkt" in dataframe_restricciones.columns
                else "WKT"
            )

            # Todo vectorizado (shapely 2 + pyproj sobre arrays): convertir los
            # ~50.000 tramos uno por uno tardaba ~14 s.
            textos_wkt = dataframe_restricciones[columna_geometria]
            geometrias = shapely.from_wkt(
                textos_wkt.where(textos_wkt.notna(), None).to_numpy(dtype=object),
                on_invalid="ignore",
            )
            if "MAX_CONTENEDORES" in dataframe_restricciones.columns:
                capacidades = pd.to_numeric(
                    dataframe_restricciones["MAX_CONTENEDORES"], errors="coerce"
                ).to_numpy()
            else:
                capacidades = np.full(len(dataframe_restricciones), 2.0)

            # Se descartan las filas con geometría inválida o sin capacidad.
            validas = ~shapely.is_missing(geometrias) & ~np.isnan(capacidades)
            geometrias_utm = shapely.transform(
                geometrias[validas], self._conversor_wgs84_a_utm, interleaved=False
            )
            self._geometrias_tramos_utm = list(geometrias_utm)
            self._capacidades_maximas_tramos = [
                int(capacidad) for capacidad in capacidades[validas]
            ]

            if self._geometrias_tramos_utm:
                self._indice_espacial_tramos = STRtree(self._geometrias_tramos_utm)
                self._datos_cargados = True
        except Exception:
            self._datos_cargados = False

    @property
    def is_active(self) -> bool:
        """Indica si el dataset de restricciones y red vial está cargado y disponible."""
        return self._datos_cargados and self._indice_espacial_tramos is not None

    def evaluate_site(
        self,
        latitude: float | None,
        longitude: float | None,
        current_containers: int = 1,
        default_cap: int = 1,
    ) -> Tuple[int | None, bool]:
        if not self.is_active:
            # Si el archivo no existe o no se cargó, no se limita la capacidad en absoluto
            return None, True

        cont_actuales = max(1, current_containers)

        if latitude is None or longitude is None:
            return None, False

        try:
            capacidad_tramo = self._capacidad_tramo(latitude, longitude)
        except Exception:
            return cont_actuales, False

        if capacidad_tramo is _NO_SEGMENT:
            return cont_actuales, False

        if capacidad_tramo <= 0:
            capacidad_maxima = cont_actuales
            tiene_cupo = False
        else:
            capacidad_maxima = max(1, capacidad_tramo)
            tiene_cupo = capacidad_maxima > current_containers

        return capacidad_maxima, tiene_cupo

    def _capacidad_tramo(self, latitude: float, longitude: float) -> Any:
        """Capacidad del tramo de calle del sitio, o _NO_SEGMENT si no hay ninguno cerca."""
        clave = (latitude, longitude)
        if clave not in self._capacidad_por_ubicacion:
            self._capacidad_por_ubicacion[clave] = self._calcular_capacidad_tramo(
                latitude, longitude
            )
        return self._capacidad_por_ubicacion[clave]

    def _calcular_capacidad_tramo(self, latitude: float, longitude: float) -> Any:
        coord_x_utm, coord_y_utm = self._conversor_wgs84_a_utm(longitude, latitude)
        punto_sitio_utm = Point(coord_x_utm, coord_y_utm)

        # Buscar tramos viales dentro de un radio de tolerancia de 35 metros
        indices_tramos_candidatos = self._indice_espacial_tramos.query(
            punto_sitio_utm.buffer(35.0)
        )

        # Validar distancia euclidiana real <= 35m
        candidatos_validos = [
            (idx, self._geometrias_tramos_utm[idx].distance(punto_sitio_utm))
            for idx in indices_tramos_candidatos
            if self._geometrias_tramos_utm[idx].distance(punto_sitio_utm) <= 35.0
        ]

        if not candidatos_validos:
            return _NO_SEGMENT

        # En una esquina existen tramos para ambas márgenes e intersecciones contiguas (<= 25m).
        # Tomamos la capacidad del tramo habilitado más favorable en la esquina.
        contiguos = [idx for idx, dist in candidatos_validos if dist <= 25.0]

        if contiguos:
            return max(self._capacidades_maximas_tramos[idx] for idx in contiguos)
        idx_mas_cercano = min(candidatos_validos, key=lambda x: x[1])[0]
        return self._capacidades_maximas_tramos[idx_mas_cercano]

    def get_capacity_for_coordinates(
        self, latitude: float | None, longitude: float | None, default_cap: int = 2
    ) -> int | None:
        if not self.is_active:
            return None
        capacidad_maxima, _ = self.evaluate_site(
            latitude, longitude, current_containers=0, default_cap=default_cap
        )
        return capacidad_maxima


@lru_cache(maxsize=1)
def get_site_capacity_service(csv_path: Optional[str] = None) -> SiteCapacityService:
    return SiteCapacityService(csv_path=csv_path)

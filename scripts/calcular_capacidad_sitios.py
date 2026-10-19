from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional
import pandas as pd
import shapely.wkt
from shapely.geometry import Point
from shapely.strtree import STRtree
from shapely.ops import transform
import pyproj


class StandaloneSiteCapacityCalculator:
    def __init__(self, csv_path: Optional[str] = None):
        if csv_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            csv_path = os.path.join(
                base_dir, "datos", "digital_twin", "restricciones_contenedores.csv"
            )

        self.csv_path = csv_path
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(
                f"No se encontró el archivo de restricciones en: {self.csv_path}"
            )

        self.df = pd.read_csv(self.csv_path, sep=";", encoding="utf-8")
        self.wgs84_to_utm = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:32721", always_xy=True
        ).transform

        wkt_col = "geometry_wkt" if "geometry_wkt" in self.df.columns else "WKT"
        self.geoms_utm = []
        for wkt_str in self.df[wkt_col]:
            g = shapely.wkt.loads(wkt_str)
            self.geoms_utm.append(transform(self.wgs84_to_utm, g))

        self.tree = STRtree(self.geoms_utm)

    def calcular_capacidad_sitios(
        self, sites: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        sitios_procesados = []
        for s in sites:
            site = dict(s)
            lat = site.get("latitude")
            lon = site.get("longitude")
            raw_cont = site.get("current_containers_count")
            if raw_cont is None:
                raw_cont = site.get("contenedores_actuales")
            cont_actuales = int(raw_cont) if raw_cont is not None else 1

            if lat is None or lon is None:
                continue

            x_utm, y_utm = self.wgs84_to_utm(lon, lat)
            pt_utm = Point(x_utm, y_utm)
            cand_indices = self.tree.query(pt_utm.buffer(35.0))

            valid_candidates = [
                (i, self.geoms_utm[i].distance(pt_utm))
                for i in cand_indices
                if self.geoms_utm[i].distance(pt_utm) <= 35.0
            ]

            if len(valid_candidates) == 0:
                site["MAX_CONTENEDORES"] = max(1, cont_actuales)
                site["longitud_total_m"] = 0.0
                site["cupo_disponible"] = 0
                site["puede_ingresar_nuevo_contenedor"] = False
                site["puede_colocar_mas"] = False
                site["capacidad_status"] = "SIN_TRAMO_CERCANO"
                sitios_procesados.append(site)
                continue

            # Considerar tramos contiguos a la esquina/cuadra (<= 25m) para tomar el lado habilitado
            nearby_indices = [i for i, dist in valid_candidates if dist <= 25.0]
            if nearby_indices:
                best_idx = max(
                    nearby_indices,
                    key=lambda i: int(self.df.iloc[i]["MAX_CONTENEDORES"]),
                )
            else:
                best_idx = min(valid_candidates, key=lambda x: x[1])[0]

            geom_tramo = self.geoms_utm[best_idx]
            distancia_m = round(float(geom_tramo.length), 2)
            # Límites de longitud física: mínimo 5m y máximo 400m
            longitud_acotada_m = max(5.0, min(400.0, distancia_m))

            tramo = self.df.iloc[best_idx]
            max_cont_tramo = int(tramo["MAX_CONTENEDORES"])
            if max_cont_tramo <= 0:
                max_cont = max(1, cont_actuales)
                cupo = 0
            else:
                max_cont = max(1, max_cont_tramo)
                cupo = max(0, max_cont - cont_actuales)

            site["MAX_CONTENEDORES"] = max_cont
            site["segment_id"] = (
                int(tramo["segment_id"]) if "segment_id" in tramo else int(best_idx)
            )
            site["longitud_total_m"] = longitud_acotada_m
            site["cupo_disponible"] = int(cupo)
            site["puede_ingresar_nuevo_contenedor"] = bool(cupo > 0)
            site["puede_colocar_mas"] = bool(cupo > 0)

            sitios_procesados.append(site)

        return sitios_procesados


if __name__ == "__main__":
    print("Ejecutando calculador autónomo de capacidad...")
    calc = StandaloneSiteCapacityCalculator()

    sitios_ejemplo = [
        {
            "id": 1,
            "name": "Sitio Arias 3450",
            "latitude": -34.545914,
            "longitude": -58.483065,
            "contenedores_actuales": 2,
        }
    ]

    resultado = calc.calcular_capacidad_sitios(sitios_ejemplo)
    print("\nResultado del cálculo:")
    print(json.dumps(resultado, indent=2, ensure_ascii=False))

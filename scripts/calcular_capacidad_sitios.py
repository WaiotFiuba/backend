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
            csv_path = os.path.join(base_dir, "datos", "restricciones_contenedores.csv")

        self.csv_path = csv_path
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"No se encontró el archivo de restricciones en: {self.csv_path}")

        self.df = pd.read_csv(self.csv_path, sep=";", encoding="utf-8")
        self.wgs84_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32721", always_xy=True).transform
        
        wkt_col = "geometry_wkt" if "geometry_wkt" in self.df.columns else "WKT"
        self.geoms_utm = []
        for wkt_str in self.df[wkt_col]:
            g = shapely.wkt.loads(wkt_str)
            self.geoms_utm.append(transform(self.wgs84_to_utm, g))

        self.tree = STRtree(self.geoms_utm)

    def calcular_capacidad_sitios(self, sites: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sitios_procesados = []
        for s in sites:
            site = dict(s)
            lat = site.get("latitude")
            lon = site.get("longitude")
            if lat is None or lon is None:
                site["MAX_CONTENEDORES"] = 0
                site["capacidad_status"] = "SIN_COORDENADAS"
                sitios_procesados.append(site)
                continue

            x_utm, y_utm = self.wgs84_to_utm(lon, lat)
            pt_utm = Point(x_utm, y_utm)
            cand_indices = self.tree.query(pt_utm.buffer(35.0))

            if len(cand_indices) == 0:
                site["MAX_CONTENEDORES"] = 0
                site["cupo_disponible"] = 0
                site["puede_ingresar_nuevo_contenedor"] = False
                site["capacidad_status"] = "SIN_TRAMO_CERCANO"
                sitios_procesados.append(site)
                continue

            best_idx = min(cand_indices, key=lambda i: self.geoms_utm[i].distance(pt_utm))
            tramo = self.df.iloc[best_idx]

            max_cont = int(tramo["MAX_CONTENEDORES"])
            site["MAX_CONTENEDORES"] = max_cont
            site["segment_id"] = int(tramo["segment_id"])
            site["municipio"] = str(tramo["municipio"])
            site["calle_nombre"] = str(tramo["calle_nombre"])
            site["altura_desde"] = tramo["altura_desde"] if pd.notna(tramo["altura_desde"]) else ""
            site["altura_hasta"] = tramo["altura_hasta"] if pd.notna(tramo["altura_hasta"]) else ""
            site["esquina_inicio"] = str(tramo.get("esquina_inicio", ""))
            site["esquina_fin"] = str(tramo.get("esquina_fin", ""))
            site["acera_lado"] = str(tramo["acera_lado"])
            site["tipo_via"] = str(tramo["tipo_via"])
            site["ancho_calle_m"] = float(tramo.get("ancho_calle_m", 12.0))
            site["permite_calzada"] = (str(tramo["permite_calzada"]).upper() == "SI")
            site["permite_acera"] = (str(tramo["permite_acera"]).upper() == "SI")
            site["longitud_total_m"] = float(tramo["longitud_total_m"])
            site["espacio_bloqueado_m"] = float(tramo["espacio_bloqueado_m"])
            site["espacio_disponible_m"] = float(tramo["espacio_disponible_m"])
            
            if pd.notna(tramo.get("restricciones_json")):
                try:
                    site["restricciones"] = json.loads(tramo["restricciones_json"])
                except Exception:
                    site["restricciones"] = []
            else:
                site["restricciones"] = []

            cont_actuales = site.get("current_containers_count", site.get("contenedores_actuales", 0))
            site["cupo_disponible"] = max(0, max_cont - cont_actuales)
            site["puede_ingresar_nuevo_contenedor"] = (max_cont - cont_actuales) > 0

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
            "contenedores_actuales": 2
        }
    ]
    
    resultado = calc.calcular_capacidad_sitios(sitios_ejemplo)
    print("\nResultado del cálculo:")
    print(json.dumps(resultado, indent=2, ensure_ascii=False))

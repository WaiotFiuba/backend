from __future__ import annotations
 
import csv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
 
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import Point
from shapely.ops import transform as shapely_transform
from shapely.strtree import STRtree
 
logger = logging.getLogger(__name__)
 
# Constantes de cálculo en Backend
PROMEDIO_GENERACION_BASURA_PERSONAS_24H: float = 1.5  # kg por persona cada 24 horas
DENSIDAD_BASURA_KG_M3: float = 150.0
DAILY_WASTE_PER_PERSON_KG: float = PROMEDIO_GENERACION_BASURA_PERSONAS_24H
WASTE_DENSITY_KG_M3: float = DENSIDAD_BASURA_KG_M3
DEFAULT_HOURLY_FILL_PCT: float = (
    1.0  # 2.5  # Tasa base por hora (50-60% llenado diario)
)
 
# Buffer aplicado al polígono del radio censal para capturar contenedores
# de la vereda de enfrente / cuadra lindante (ancho aprox. calle + vereda).
DEFAULT_STREET_BUFFER_M: float = 15.0
 
# CRS métrico para CABA (Gauss-Krüger faja 5 / Argentina). Usado sólo para
# bufferizar polígonos en metros; los datos siguen viviendo en WGS84 (grados).
_SOURCE_CRS = "EPSG:4326"
_METRIC_CRS = "EPSG:5347"
_to_metric = Transformer.from_crs(_SOURCE_CRS, _METRIC_CRS, always_xy=True)
_to_geographic = Transformer.from_crs(_METRIC_CRS, _SOURCE_CRS, always_xy=True)
 
 
def _buffer_polygon_meters(geom, buffer_m: float):
    """Bufferiza un polígono en grados reproyectando a un CRS métrico y volviendo."""
    if buffer_m <= 0:
        return geom
    try:
        geom_metric = shapely_transform(lambda x, y: _to_metric.transform(x, y), geom)
        buffered_metric = geom_metric.buffer(buffer_m)
        return shapely_transform(
            lambda x, y: _to_geographic.transform(x, y), buffered_metric
        )
    except Exception:
        logger.exception("Fallo al bufferizar geometría, se usa geometría original.")
        return geom
 
 
@dataclass(frozen=True)
class CensusRadio:
    radio_code: str
    population: int
    department_name: str
    area_km2: float
    centroid_lat: float
    centroid_lon: float
    geometry: object
 
 
@dataclass(frozen=True)
class ContainerDemandInfo:
    container_id: int | str
    radio_code: str  # radio dominante (el que más kg/día aporta a este contenedor)
    department_name: str  # departamento del radio dominante
    radio_codes: list[str] = field(default_factory=list)  # todos los radios conectados
    population: int = 0  # población del radio dominante
    containers_in_radio: int = 0  # contenedores conectados al radio dominante
    daily_waste_kg: float = 0.0  # SUMA de aportes de todos los radios conectados
    capacity_kg: float = 0.0
    daily_fill_pct: float = 0.0
    hourly_fill_pct: float = 0.0
 
 
@dataclass(frozen=True)
class RadioContainersSummary:
    radio_code: str
    department_name: str
    population: int
    area_km2: float
    container_ids: list[int | str]
    total_containers: int
    daily_waste_per_container_kg: float
 
 
class DensityProcessor:
    """
    Procesador espacial que conecta radios censales de CABA con los
    contenedores que les sirven (relación muchos a muchos) y calcula
    la tasa de demanda y llenado según población.
 
    Un contenedor puede estar conectado a más de un radio censal (por
    ejemplo, el de su propia manzana y el de la manzana de enfrente),
    y un radio censal reparte su generación de basura entre todos los
    contenedores que tiene conectados.
    """
 
    def __init__(
        self,
        csv_path: str | Path | None = None,
        street_buffer_m: float = DEFAULT_STREET_BUFFER_M,
    ) -> None:
        self.radios: list[CensusRadio] = []
        self._geometries: list[object] = []
        self._buffered_geometries: list[object] = []
        self.street_buffer_m = street_buffer_m
        self._load_dataset(csv_path)
 
    def _resolve_csv_path(self, custom_path: str | Path | None) -> Path | None:
        if custom_path and Path(custom_path).exists():
            return Path(custom_path)
 
        candidates = [
            Path(__file__).resolve().parents[3] / "datos" / "radios_caba_filtrado.csv",
            Path(__file__).resolve().parents[2] / "datos" / "radios_caba_filtrado.csv",
            Path("/app/datos/radios_caba_filtrado.csv"),
            Path("datos/radios_caba_filtrado.csv"),
        ]
        return next((p for p in candidates if p.exists()), None)
 
    def _load_dataset(self, csv_path: str | Path | None) -> None:
        resolved = self._resolve_csv_path(csv_path)
        if not resolved:
            logger.warning(
                "No se encontró el dataset de radios censales de CABA. Usando fallback neutral."
            )
            return
 
        logger.info(f"Cargando dataset de radios censales desde: {resolved}")
        with open(resolved, mode="r", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                wkt_str = row.get("Geometría en WKT")
                if not wkt_str:
                    continue
 
                try:
                    geom = wkt.loads(wkt_str)
                    if not geom.is_valid:
                        geom = geom.buffer(0)
                except Exception:
                    continue
 
                try:
                    pop_val = (
                        row.get("Población total")
                        or row.get("Población total (en hogares familiares)")
                        or "0"
                    )
                    population = int(float(pop_val))
                except (ValueError, TypeError):
                    population = 0
 
                try:
                    area_val = row.get("Superficie en km2") or "0"
                    area_km2 = float(area_val)
                except (ValueError, TypeError):
                    area_km2 = 0.0
 
                try:
                    lat_val = row.get("Latitud del centroide") or "0"
                    lon_val = row.get("Longitud del centroide") or "0"
                    c_lat = float(lat_val)
                    c_lon = float(lon_val)
                except (ValueError, TypeError):
                    c_lat, c_lon = 0.0, 0.0
 
                radio_code = str(row.get("Código de radio") or "")
                dept_name = str(row.get("Nombre de departamento") or "")
 
                radio = CensusRadio(
                    radio_code=radio_code,
                    population=population,
                    department_name=dept_name,
                    area_km2=area_km2,
                    centroid_lat=c_lat,
                    centroid_lon=c_lon,
                    geometry=geom,
                )
                self.radios.append(radio)
                self._geometries.append(geom)
 
        if self._geometries:
            # Buffer pre-calculado por radio (se reutiliza en cada llamada a
            # process_containers, no depende de los contenedores de entrada).
            self._buffered_geometries = [
                _buffer_polygon_meters(g, self.street_buffer_m)
                for g in self._geometries
            ]
            logger.info(
                f"Dataset cargado con {len(self.radios)} radios censales "
                f"(buffer de {self.street_buffer_m}m aplicado)."
            )
 
    def process_containers(
        self,
        containers: Sequence[dict[str, object]],
        daily_kg_per_person: float = DAILY_WASTE_PER_PERSON_KG,
        waste_density_kg_m3: float = WASTE_DENSITY_KG_M3,
        global_demand_multiplier: float = 1.0,
        street_buffer_m: float | None = None,
    ) -> tuple[dict[int | str, ContainerDemandInfo], list[RadioContainersSummary]]:
        """
        Dada una lista de contenedores (cada uno con 'id', 'latitude', 'longitude'
        y 'volume_m3'):
        1. Para cada radio censal, encuentra todos los contenedores dentro de su
           polígono bufferizado (incluye contenedores de la vereda de enfrente).
        2. Un mismo contenedor puede quedar conectado a más de un radio.
        3. Cada radio reparte su generación diaria (población * kg/persona) entre
           todos sus contenedores conectados, ponderando por cuántos radios
           conecta cada contenedor (uno compartido entre 2 radios recibe media
           porción de cada uno, no una porción completa de cada uno).
        4. La demanda final de cada contenedor es la SUMA de los aportes de todos
           los radios a los que está conectado.
        """
        buffer_m = (
            street_buffer_m if street_buffer_m is not None else self.street_buffer_m
        )
        # Si se pide un buffer distinto al precalculado, rebufferizar al vuelo.
        buffered_geoms = self._buffered_geometries
        if street_buffer_m is not None and street_buffer_m != self.street_buffer_m:
            buffered_geoms = [
                _buffer_polygon_meters(g, buffer_m) for g in self._geometries
            ]
 
        if not self.radios or not containers:
            return {}, []
 
        # Paso 1: indice espacial sobre los CONTENEDORES
        container_ids: list[int | str] = []
        container_points: list[Point] = []
        for c in containers:
            cid = c.get("id")
            if cid is None:
                continue
            lat = float(c.get("latitude", 0.0))
            lon = float(c.get("longitude", 0.0))
            container_ids.append(cid)
            container_points.append(Point(lon, lat))
 
        if not container_points:
            return {}, []
 
        container_tree = STRtree(container_points)
 
        # Paso 2: por cada radio, buscar contenedores dentro del buffer
        radio_to_containers: dict[str, list[int | str]] = {}
        for radio, buffered_geom in zip(self.radios, buffered_geoms):
            indices = container_tree.query(buffered_geom, predicate="intersects")
            if len(indices) > 0:
                radio_to_containers[radio.radio_code] = [
                    container_ids[i] for i in indices
                ]
 
        # cuántos radios conecta cada contenedor. Se usa para ponderar
        # su peso en el reparto: un contenedor conectado a 2 radios no debe
        # recibir la porcion COMPLETA de ambos (eso lo sobrecargaria respecto a
        # un contenedor conectado a un solo radio), sino una porcion de cada
        # uno proporcional a que reparte su "capacidad" entre varios radios.
        radios_por_contenedor: dict[int | str, int] = defaultdict(int)
        for radio in self.radios:
            for cid in radio_to_containers.get(radio.radio_code, []):
                radios_por_contenedor[cid] += 1
 
        def _peso(cid: int | str) -> float:
            n = radios_por_contenedor.get(cid, 1)
            return 1.0 / n if n > 0 else 1.0
 
        # Paso 3: aporte (kg/dia) de cada radio hacia cada uno de sus contenedores
        # conectados, ponderado por el peso de cada contenedor dentro del pool
        # de ese radio. contributions[cid] = [(radio, kg_aportados_por_ese_radio), ...]
        # Nota: la SUMA de aportes que reparte un radio entre sus contenedores
        # sigue siendo exactamente population * daily_kg_per_person * multiplier
        # (se conserva el total generado), solo cambia como se distribuye esa
        # suma entre contenedores compartidos vs. exclusivos.
        contributions: dict[int | str, list[tuple[CensusRadio, float]]] = defaultdict(
            list
        )
        for radio in self.radios:
            conectados = radio_to_containers.get(radio.radio_code, [])
            if not conectados:
                continue
            pesos = {cid: _peso(cid) for cid in conectados}
            pool = sum(pesos.values())
            if pool <= 0:
                continue
            aporte_por_unidad_peso = (
                (radio.population * daily_kg_per_person) / pool
            ) * global_demand_multiplier
            for cid in conectados:
                aporte = aporte_por_unidad_peso * pesos[cid]
                contributions[cid].append((radio, aporte))
 
        # Paso 4: resumenes por radio censal (para debug/visualización)
        radio_summaries: list[RadioContainersSummary] = []
        for radio in self.radios:
            conectados = radio_to_containers.get(radio.radio_code)
            if not conectados:
                continue
            total_c = len(conectados)
            daily_kg_per_container = (
                ((radio.population * daily_kg_per_person) / total_c)
                * global_demand_multiplier
                if total_c > 0
                else 0.0
            )
            radio_summaries.append(
                RadioContainersSummary(
                    radio_code=radio.radio_code,
                    department_name=radio.department_name,
                    population=radio.population,
                    area_km2=radio.area_km2,
                    container_ids=conectados,
                    total_containers=total_c,
                    daily_waste_per_container_kg=round(daily_kg_per_container, 4),
                )
            )
 
        # Paso 5: demanda final por contenedor = suma de aportes de todos sus radios
        container_demands: dict[int | str, ContainerDemandInfo] = {}
        for c in containers:
            cid = c.get("id")
            if cid is None:
                continue
 
            vol_m3 = c.get("volume_m3")
            try:
                volume = float(vol_m3) if vol_m3 is not None else 1.0
            except (ValueError, TypeError):
                volume = 1.0
            capacity_kg = volume * waste_density_kg_m3
 
            aportes = contributions.get(cid, [])
 
            if aportes:
                daily_kg_total = sum(kg for _, kg in aportes)
                radio_dominante, _ = max(aportes, key=lambda t: t[1])
                radio_codes = sorted({r.radio_code for r, _ in aportes})
                population = radio_dominante.population
                department_name = radio_dominante.department_name
                containers_in_radio = len(
                    radio_to_containers.get(radio_dominante.radio_code, [])
                )
            else:
                # Contenedor fuera de CABA / sin ningún radio dentro del buffer
                daily_kg_total = 150.0 * global_demand_multiplier
                radio_codes = []
                population = 0
                department_name = "UNKNOWN"
                containers_in_radio = 1
 
            daily_fill_pct = max(20.0, (daily_kg_total / capacity_kg) * 100.0)
            hourly_fill_pct = daily_fill_pct / 24.0
 
            container_demands[cid] = ContainerDemandInfo(
                container_id=cid,
                radio_code=radio_dominante.radio_code if aportes else "UNKNOWN",
                department_name=department_name,
                radio_codes=radio_codes,
                population=population,
                containers_in_radio=containers_in_radio,
                daily_waste_kg=round(daily_kg_total, 4),
                capacity_kg=round(capacity_kg, 2),
                daily_fill_pct=round(daily_fill_pct, 4),
                hourly_fill_pct=round(hourly_fill_pct, 4),
            )
 
        return container_demands, radio_summaries
 
 
# Instancia singleton para evitar recargar el archivo repetidamente
_cached_processor: DensityProcessor | None = None
 
 
def get_density_processor() -> DensityProcessor:
    global _cached_processor
    if _cached_processor is None:
        _cached_processor = DensityProcessor()
    return _cached_processor
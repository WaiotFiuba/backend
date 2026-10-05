from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import Point
from shapely.ops import transform as shapely_transform
from shapely.strtree import STRtree

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# transforma entre los datos. CRS para bufferizar en metros; los datos son WGS84.
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


class CensusRadio(BaseModel):
    model_config = ConfigDict(frozen=True)

    radio_code: str
    population: int
    department_name: str
    area_km2: float
    centroid_lat: float
    centroid_lon: float
    geometry: object


class ContainerDemandInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    container_id: int | str
    radio_code: str  # radio dominante (el que mas kg/dia aporta al contenedor)
    department_name: str  # departamento del radio dominante
    radio_codes: list[str] = Field(default_factory=list)  # todos los radios conectados
    population: int = 0  # poblacion del radio dominante
    containers_in_radio: int = 0  # contenedores conectados al radio dominante
    daily_waste_kg: float = 0.0  # suma de aportes de todos los radios conectados
    capacity_kg: float = 0.0
    daily_fill_pct: float = 0.0
    hourly_fill_pct: float = 0.0


class RadioContainersSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    radio_code: str
    department_name: str
    population: int
    area_km2: float
    container_ids: list[int | str]
    total_containers: int
    daily_waste_per_container_kg: float


class DensityProcessor:
    """
    Conecta radios censales de CABA con los contenedores que les sirven
    (muchos a muchos, vía buffer de calle) y reparte su generación de
    basura entre ellos ponderando por distancia real.
    """

    def __init__(
        self,
        csv_path: str | Path | None = None,
        street_buffer_m: float | None = None,
    ) -> None:
        self.radios: list[CensusRadio] = []
        self._geometries: list[object] = []
        self._buffered_geometries: list[object] = []
        self._metric_geometries: list[object] = []
        self.street_buffer_m = (
            street_buffer_m
            if street_buffer_m is not None
            else get_settings().density_street_buffer_m
        )
        self._load_dataset(csv_path)

    def _resolve_csv_path(self, custom_path: str | Path | None) -> Path | None:
        if custom_path and Path(custom_path).exists():
            return Path(custom_path)

        backend_root = Path(__file__).resolve().parent.parent
        candidates = [
            backend_root
            / "datos"
            / "simulator"
            / "demography"
            / "radios_caba_filtrado.csv",
            Path("/app/datos/simulator/demography/radios_caba_filtrado.csv"),
            Path("datos/simulator/demography/radios_caba_filtrado.csv"),
            backend_root / "datos" / "radios_caba_filtrado.csv",
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
            # Buffer pre-calculado, reutilizado en cada process_containers().
            self._buffered_geometries = [
                _buffer_polygon_meters(g, self.street_buffer_m)
                for g in self._geometries
            ]
            # Geometría real reproyectada a métrico, para medir distancia en metros.
            self._metric_geometries = [
                shapely_transform(lambda x, y: _to_metric.transform(x, y), g)
                for g in self._geometries
            ]
            logger.info(
                f"Dataset cargado con {len(self.radios)} radios censales "
                f"(buffer de {self.street_buffer_m}m aplicado)."
            )

    def process_containers(
        self,
        containers: Sequence[dict[str, object]],
        daily_kg_per_person: float | None = None,
        waste_density_kg_m3: float | None = None,
        global_demand_multiplier: float = 1.0,
        street_buffer_m: float | None = None,
    ) -> tuple[dict[int | str, ContainerDemandInfo], list[RadioContainersSummary]]:
        """
        Asigna a cada contenedor (dict con 'id', 'latitude', 'longitude',
        'volume_m3') la demanda diaria/horaria de basura, sumando el aporte
        de todos los radios censales cuyo buffer lo alcanza, ponderado por
        distancia real a cada uno.
        """
        settings = get_settings()
        daily_kg_per_person = (
            daily_kg_per_person
            if daily_kg_per_person is not None
            else settings.promedio_generacion_basura_personas_24h
        )
        waste_density_kg_m3 = (
            waste_density_kg_m3
            if waste_density_kg_m3 is not None
            else settings.densidad_basura_kg_m3
        )
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

        container_ids: list[int | str] = []
        container_points: list[Point] = []
        metric_point_by_id: dict[int | str, Point] = {}
        for c in containers:
            cid = c.get("id")
            if cid is None:
                continue
            lat = float(c.get("latitude", 0.0))
            lon = float(c.get("longitude", 0.0))
            container_ids.append(cid)
            container_points.append(Point(lon, lat))
            metric_point_by_id[cid] = Point(_to_metric.transform(lon, lat))

        if not container_points:
            return {}, []

        container_tree = STRtree(container_points)
        metric_geom_by_code: dict[str, object] = dict(
            zip((r.radio_code for r in self.radios), self._metric_geometries)
        )

        radio_to_containers: dict[str, list[int | str]] = {}
        for radio, buffered_geom in zip(self.radios, buffered_geoms):
            indices = container_tree.query(buffered_geom, predicate="intersects")
            if len(indices) > 0:
                radio_to_containers[radio.radio_code] = [
                    container_ids[i] for i in indices
                ]

        # Peso por par (radio, contenedor): decae linealmente con la distancia
        # real al polígono del radio — máximo si esta adentro, 0 en el borde
        # externo del buffer.
        peso_por_par: dict[tuple[str, int | str], float] = {}
        for radio in self.radios:
            conectados = radio_to_containers.get(radio.radio_code, [])
            if not conectados:
                continue
            metric_geom = metric_geom_by_code[radio.radio_code]
            for cid in conectados:
                distancia_m = metric_geom.distance(metric_point_by_id[cid])
                peso_por_par[(radio.radio_code, cid)] = max(0.0, buffer_m - distancia_m)

        # Aporte (kg/dia) de cada radio a sus contenedores conectados,
        # ponderado por peso y conservando el total generado por el radio.
        contributions: dict[int | str, list[tuple[CensusRadio, float]]] = defaultdict(
            list
        )
        for radio in self.radios:
            conectados = radio_to_containers.get(radio.radio_code, [])
            if not conectados:
                continue
            pesos = {cid: peso_por_par[(radio.radio_code, cid)] for cid in conectados}
            pool = sum(pesos.values())
            if pool <= 0:
                # Todos los pesos en 0: reparte parejo para no perder la generación.
                pesos = dict.fromkeys(conectados, 1.0)
                pool = float(len(conectados))
            aporte_por_unidad_peso = (
                (radio.population * daily_kg_per_person) / pool
            ) * global_demand_multiplier
            for cid in conectados:
                aporte = aporte_por_unidad_peso * pesos[cid]
                contributions[cid].append((radio, aporte))

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
                # Contenedor fuera de CABA / sin ningún radio dentro del buffer.
                daily_kg_total = (
                    settings.density_fallback_daily_waste_kg * global_demand_multiplier
                )
                radio_codes = []
                population = 0
                department_name = "UNKNOWN"
                containers_in_radio = 1

            daily_fill_pct = max(
                settings.density_min_daily_fill_pct_floor,
                (daily_kg_total / capacity_kg) * 100.0,
            )
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

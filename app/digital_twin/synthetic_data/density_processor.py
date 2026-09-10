from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from shapely import wkt
from shapely.geometry import Point
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
    radio_code: str
    department_name: str
    population: int
    containers_in_radio: int
    daily_waste_kg: float
    capacity_kg: float
    daily_fill_pct: float
    hourly_fill_pct: float


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
    Procesador espacial para asignar contenedores a radios censales de CABA
    y calcular la tasa de demanda y llenado según población.
    """

    def __init__(self, csv_path: str | Path | None = None) -> None:
        self.radios: list[CensusRadio] = []
        self._geometries: list[object] = []
        self._tree: STRtree | None = None
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
            self._tree = STRtree(self._geometries)
            logger.info(
                f"Índice espacial construido con {len(self.radios)} radios censales."
            )

    def find_radio(self, latitude: float, longitude: float) -> CensusRadio | None:
        """
        Encuentra el radio censal al que pertenece la coordenada (Point in Polygon).
        Si cae en el límite o borde, utiliza el radio más cercano.
        """
        if not self._tree or not self.radios:
            return None

        point = Point(longitude, latitude)
        # Búsqueda exacta de contención/intersección
        indices = self._tree.query(point, predicate="intersects")
        if len(indices) > 0:
            return self.radios[indices[0]]

        # Fallback al polígono más cercano si la coordenada está ligeramente fuera/en calle
        nearest_idx = self._tree.nearest(point)
        if nearest_idx is not None:
            return self.radios[nearest_idx]

        return None

    def process_containers(
        self,
        containers: Sequence[dict[str, object]],
        daily_kg_per_person: float = DAILY_WASTE_PER_PERSON_KG,
        waste_density_kg_m3: float = WASTE_DENSITY_KG_M3,
        global_demand_multiplier: float = 1.0,
    ) -> tuple[dict[int | str, ContainerDemandInfo], list[RadioContainersSummary]]:
        """
        Dada una lista de contenedores (cada uno con 'id', 'latitude', 'longitude' y 'volume_m3'):
        1. Asigna cada contenedor a su radio censal correspondiente.
        2. Agrupa y calcula cuántos contenedores pertenecen a cada radio.
        3. Calcula la demanda (kg/24h) y el factor horario base para cada contenedor
           multiplicado por el factor de demanda global (global_demand_multiplier del front).
        """
        # Paso 1: Mapear contenedor -> radio
        container_to_radio: dict[int | str, CensusRadio | None] = {}
        radio_to_containers: dict[str, list[int | str]] = {}
        radio_by_code: dict[str, CensusRadio] = {}

        for c in containers:
            cid = c.get("id")
            if cid is None:
                continue
            lat = float(c.get("latitude", 0.0))
            lon = float(c.get("longitude", 0.0))

            radio = self.find_radio(latitude=lat, longitude=lon)
            container_to_radio[cid] = radio

            if radio:
                code = radio.radio_code
                radio_by_code[code] = radio
                if code not in radio_to_containers:
                    radio_to_containers[code] = []
                radio_to_containers[code].append(cid)

        # Paso 2: Construir resúmenes por radio censal
        radio_summaries: list[RadioContainersSummary] = []
        for code, c_ids in radio_to_containers.items():
            r = radio_by_code[code]
            total_c = len(c_ids)
            daily_kg_per_container = (
                ((r.population * daily_kg_per_person) / total_c)
                * global_demand_multiplier
                if total_c > 0
                else 0.0
            )
            radio_summaries.append(
                RadioContainersSummary(
                    radio_code=code,
                    department_name=r.department_name,
                    population=r.population,
                    area_km2=r.area_km2,
                    container_ids=c_ids,
                    total_containers=total_c,
                    daily_waste_per_container_kg=round(daily_kg_per_container, 4),
                )
            )

        # Paso 3: Calcular demanda e incremento para cada contenedor
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
            radio = container_to_radio.get(cid)

            if radio:
                count_in_radio = len(radio_to_containers.get(radio.radio_code, []))
                population = radio.population
                if count_in_radio > 0 and population > 0:
                    daily_kg = (
                        (population * daily_kg_per_person) / count_in_radio
                    ) * global_demand_multiplier
                else:
                    daily_kg = (
                        150.0 * global_demand_multiplier
                    )  # Fallback si población es 0

                daily_fill_pct = max(40.0, (daily_kg / capacity_kg) * 100.0)
                hourly_fill_pct = daily_fill_pct / 24.0

                info = ContainerDemandInfo(
                    container_id=cid,
                    radio_code=radio.radio_code,
                    department_name=radio.department_name,
                    population=population,
                    containers_in_radio=count_in_radio,
                    daily_waste_kg=round(daily_kg, 4),
                    capacity_kg=round(capacity_kg, 2),
                    daily_fill_pct=round(daily_fill_pct, 4),
                    hourly_fill_pct=round(hourly_fill_pct, 4),
                )
            else:
                # Contenedor fuera de CABA / sin radio
                daily_kg = 150.0 * global_demand_multiplier
                daily_fill_pct = max(40.0, (daily_kg / capacity_kg) * 100.0)
                hourly_fill_pct = daily_fill_pct / 24.0

                info = ContainerDemandInfo(
                    container_id=cid,
                    radio_code="UNKNOWN",
                    department_name="UNKNOWN",
                    population=0,
                    containers_in_radio=1,
                    daily_waste_kg=round(daily_kg, 4),
                    capacity_kg=round(capacity_kg, 2),
                    daily_fill_pct=round(daily_fill_pct, 4),
                    hourly_fill_pct=round(hourly_fill_pct, 4),
                )

            container_demands[cid] = info

        return container_demands, radio_summaries


# Instancia singleton para evitar recargar el archivo repetidamente
_cached_processor: DensityProcessor | None = None


def get_density_processor() -> DensityProcessor:
    global _cached_processor
    if _cached_processor is None:
        _cached_processor = DensityProcessor()
    return _cached_processor

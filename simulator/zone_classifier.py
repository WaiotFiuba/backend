"""
zone_classifier.py
──────────────────
Clasifica la zona de cada sitio/contenedor a partir del Relevamiento de Usos
del Suelo de CABA (BA Data 2022-2024) procesado por
simulator/demography/commands/process_land_use.py.

Toda la modulación horaria, semanal, factores fin de semana y multiplicadores
base se leen dinámicamente de 'config/zone_profiles.yaml'.

Flujo de resolución:
  1. El DensityProcessor conecta cada contenedor con todos los radios censales
     cuyo polígono (bufferizado +(metros configurable)m para capturar la vereda de enfrente) lo
     alcanza — relación muchos-a-muchos, ponderada por distancia real. El
     radio "dominante" es el que más kg/día le aporta a ese contenedor.
  2. En SimulationTopology, cada sitio recibe `site.zone = demand_info.radio_code`
     (el radio dominante).
  3. El ZoneClassifier busca el radio censal en 'land_use_by_radio.csv' y obtiene su
     ZoneProfile específico (distinguiendo residencial multifamiliar/edificios,
     unifamiliar/casas, comercial, oficinas, industrial).
  4. Si se consulta por tipo de zona directo (o por nombre de barrio, como hace
     la topología sintética del CLI), usa los perfiles de 'zone_profiles.yaml' o
     de 'land_use_by_barrio.csv'.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ZoneProfile:
    """Perfil de generación de residuos de una zona urbana / radio censal."""

    zone_key: str  # radio_code o nombre de barrio / zone_type
    barrio: str
    zone_type: str  # residential_multifamily / residential_singlefamily / commercial / office / industrial / mixed
    res_multifamily_pct: float
    res_singlefamily_pct: float
    commercial_pct: float
    office_pct: float
    industrial_pct: float
    demand_multiplier: float  # multiplicador base de demanda (del CSV de usos o YAML)
    weekend_factor: float  # factor relativo fin-de-semana / hábil (del CSV o YAML)
    hour_weights: dict[int, float] = field(default_factory=dict)  # del YAML de perfiles
    weekday_factors: dict[int, float] = field(
        default_factory=dict
    )  # del YAML de perfiles

    _temporal_norm: float | None = field(default=None, init=False, repr=False)
    _cached_hour_weights: tuple[float, ...] | None = field(
        default=None, init=False, repr=False
    )
    _cached_matrix: tuple[tuple[float, ...], ...] | None = field(
        default=None, init=False, repr=False
    )

    @property
    def residential_pct(self) -> float:
        """Suma de residencial multifamiliar y unifamiliar (para compatibilidad)."""
        return round(self.res_multifamily_pct + self.res_singlefamily_pct, 2)

    def _compute_hour_weight(self, hour: int) -> float:
        if hour in self.hour_weights:
            return self.hour_weights[hour]
        if not self.hour_weights:
            return 1.0

        defined_hours = sorted(self.hour_weights)
        prev_hour = max(
            (h for h in defined_hours if h < hour), default=defined_hours[-1] - 24
        )
        next_hour = min(
            (h for h in defined_hours if h > hour), default=defined_hours[0] + 24
        )
        prev_weight = self.hour_weights[prev_hour % 24]
        next_weight = self.hour_weights[next_hour % 24]
        t = (hour - prev_hour) / (next_hour - prev_hour)
        return prev_weight + (next_weight - prev_weight) * t

    def get_hour_weight(self, hour: int) -> float:
        """
        Factor de generación para la hora del día (0-23).
        Precalculado en tupla de 24h para acceso instantáneo O(1).
        """
        if self._cached_hour_weights is None:
            self._cached_hour_weights = tuple(
                self._compute_hour_weight(h) for h in range(24)
            )
        return self._cached_hour_weights[hour % 24]

    def get_weekday_factor(self, weekday: int) -> float:
        """
        Factor según día de la semana (Monday=0 … Sunday=6).
        """
        return self.weekday_factors.get(weekday, 1.0)

    @property
    def temporal_normalization_factor(self) -> float:
        """
        Media de la componente temporal (hour_weight × weekday_factor) sobre una semana
        completa (24h × 7 días). Cacheada en la instancia para evitar millones de evaluaciones.
        """
        if self._temporal_norm is None:
            total = sum(
                self.get_hour_weight(h) * self.get_weekday_factor(wd)
                for wd in range(7)
                for h in range(24)
            )
            mean = total / (24 * 7)
            self._temporal_norm = mean if mean > 0.0 else 1.0
        return self._temporal_norm

    def effective_multiplier(self, hour: int, weekday: int) -> float:
        """
        Multiplicador efectivo para un instante (hora, día de semana).
        Precalcula y cachea la matriz 7x24 para acceso ultra rápido O(1).
        """
        if self._cached_matrix is None:
            norm = self.temporal_normalization_factor
            matrix = []
            for wd in range(7):
                wf = self.get_weekday_factor(wd)
                matrix.append(
                    tuple(
                        self.demand_multiplier * (self.get_hour_weight(h) * wf / norm)
                        for h in range(24)
                    )
                )
            self._cached_matrix = tuple(matrix)
        return self._cached_matrix[weekday % 7][hour % 24]


# Perfil por defecto cuando no se puede determinar la zona
DEFAULT_PROFILE = ZoneProfile(
    zone_key="UNKNOWN",
    barrio="UNKNOWN",
    zone_type="residential_multifamily",
    res_multifamily_pct=50.0,
    res_singlefamily_pct=25.0,
    commercial_pct=15.0,
    office_pct=5.0,
    industrial_pct=5.0,
    demand_multiplier=1.0,
    weekend_factor=0.75,
    hour_weights={3: 0.25, 7: 1.3, 8: 1.6, 9: 1.4, 20: 1.5, 21: 1.7, 22: 1.4},
    weekday_factors={0: 0.85, 5: 0.60, 6: 0.55},
)


class ZoneClassifier:
    """
    Clasificador de zonas urbanas para CABA por radio censal y por barrio.

    Carga:
    1. `zone_profiles.yaml`      → perfiles base, modulación horaria y semanal por zone_type
    2. `land_use_by_radio.csv`   → perfiles granulares por cada radio censal
    3. `land_use_by_barrio.csv`  → perfiles agregados por barrio (fallback)

    Uso:
        classifier = ZoneClassifier()
        profile = classifier.get_profile("20980101")  # por radio censal
        profile_b = classifier.get_profile("PALERMO")  # por barrio
        mult = profile.effective_multiplier(hour=8, weekday=5)
    """

    def __init__(
        self,
        land_use_radio_csv: str | Path | None = None,
        land_use_barrio_csv: str | Path | None = None,
        zone_profiles_yaml: str | Path | None = None,
    ) -> None:
        self._radio_profiles: dict[str, ZoneProfile] = {}
        self._barrio_profiles: dict[str, ZoneProfile] = {}
        self._zone_hour_weights: dict[str, dict[int, float]] = {}
        self._zone_weekday_factors: dict[str, dict[int, float]] = {}
        self._zone_base_multipliers: dict[str, float] = {}
        self._zone_weekend_factors: dict[str, float] = {}
        # Calibración global: leída del YAML, usada por el engine para normalizar zone_mults
        self.calibration_target: float = 1.0
        self.calibration_tolerance_pct: float = 10.0

        backend_root = Path(__file__).resolve().parent.parent

        yaml_path = self._resolve_path(
            zone_profiles_yaml,
            [
                Path(__file__).resolve().parent / "config" / "zone_profiles.yaml",
                backend_root / "simulator" / "config" / "zone_profiles.yaml",
                Path("/app/simulator/config/zone_profiles.yaml"),
                Path("simulator/config/zone_profiles.yaml"),
            ],
        )
        if yaml_path:
            self._load_zone_profiles_yaml(yaml_path)

        radio_csv = self._resolve_path(
            land_use_radio_csv,
            [
                backend_root
                / "datos"
                / "simulator"
                / "land_use"
                / "land_use_by_radio.csv",
                Path("/app/datos/simulator/land_use/land_use_by_radio.csv"),
                Path("datos/simulator/land_use/land_use_by_radio.csv"),
                backend_root / "datos" / "land_use_by_radio.csv",
                Path("/app/datos/land_use_by_radio.csv"),
                Path("datos/land_use_by_radio.csv"),
            ],
        )
        if radio_csv:
            self._load_land_use_radio_csv(radio_csv)

        barrio_csv = self._resolve_path(
            land_use_barrio_csv,
            [
                backend_root
                / "datos"
                / "simulator"
                / "land_use"
                / "land_use_by_barrio.csv",
                Path("/app/datos/simulator/land_use/land_use_by_barrio.csv"),
                Path("datos/simulator/land_use/land_use_by_barrio.csv"),
                backend_root / "datos" / "land_use_by_barrio.csv",
                Path("/app/datos/land_use_by_barrio.csv"),
                Path("datos/land_use_by_barrio.csv"),
            ],
        )
        if barrio_csv:
            self._load_land_use_barrio_csv(barrio_csv)

    def _resolve_path(
        self, custom: str | Path | None, candidates: list[Path]
    ) -> Path | None:
        if custom and Path(custom).exists():
            return Path(custom)
        return next((p for p in candidates if p.exists()), None)

    # ── Carga de datos ─────────────────────────────────────────────────────────

    def _load_zone_profiles_yaml(self, path: Path) -> None:
        logger.info(f"Cargando perfiles de zona desde: {path}")
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        # Calibración global de demanda
        calibration = data.get("global_demand_calibration", {})
        self.calibration_target = float(calibration.get("target_zone_mult", 1.0))
        self.calibration_tolerance_pct = float(calibration.get("tolerance_pct", 10.0))
        logger.info(
            f"  Calibración global: target={self.calibration_target}, "
            f"tolerance=±{self.calibration_tolerance_pct}%"
        )

        profiles_raw = data.get("profiles", {})
        for zone_type, cfg in profiles_raw.items():
            hw = {int(k): float(v) for k, v in (cfg.get("hour_weights") or {}).items()}
            wf = {
                int(k): float(v) for k, v in (cfg.get("weekday_factors") or {}).items()
            }
            base_mult = float(cfg.get("base_demand_multiplier", 1.0))
            weekend_fac = float(cfg.get("weekend_factor", 0.75))

            self._zone_hour_weights[zone_type] = hw
            self._zone_weekday_factors[zone_type] = wf
            self._zone_base_multipliers[zone_type] = base_mult
            self._zone_weekend_factors[zone_type] = weekend_fac

        logger.info(f"  {len(self._zone_hour_weights)} zone_types cargados del YAML")

    def _load_land_use_radio_csv(self, path: Path) -> None:
        logger.info(f"Cargando usos del suelo por radio censal desde: {path}")
        with open(path, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                r_code = row["radio_code"].strip()
                barrio = row.get("barrio", "").strip().upper()
                zone_type = row["zone_type"].strip()

                profile = ZoneProfile(
                    zone_key=r_code,
                    barrio=barrio,
                    zone_type=zone_type,
                    res_multifamily_pct=float(row.get("res_multifamily_pct", 0)),
                    res_singlefamily_pct=float(row.get("res_singlefamily_pct", 0)),
                    commercial_pct=float(row.get("commercial_pct", 0)),
                    office_pct=float(row.get("office_pct", 0)),
                    industrial_pct=float(row.get("industrial_pct", 0)),
                    demand_multiplier=float(
                        row.get(
                            "demand_multiplier",
                            self._zone_base_multipliers.get(zone_type, 1.0),
                        )
                    ),
                    weekend_factor=float(
                        row.get(
                            "weekend_factor",
                            self._zone_weekend_factors.get(zone_type, 0.75),
                        )
                    ),
                    hour_weights=self._zone_hour_weights.get(zone_type, {}),
                    weekday_factors=self._zone_weekday_factors.get(zone_type, {}),
                )
                self._radio_profiles[r_code] = profile

        logger.info(f"  {len(self._radio_profiles):,} radios censales cargados")

    def _load_land_use_barrio_csv(self, path: Path) -> None:
        with open(path, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                barrio = row["barrio"].strip().upper()
                zone_type = row["zone_type"].strip()

                profile = ZoneProfile(
                    zone_key=barrio,
                    barrio=barrio,
                    zone_type=zone_type,
                    res_multifamily_pct=float(row.get("res_multifamily_pct", 0)),
                    res_singlefamily_pct=float(row.get("res_singlefamily_pct", 0)),
                    commercial_pct=float(row.get("commercial_pct", 0)),
                    office_pct=float(row.get("office_pct", 0)),
                    industrial_pct=float(row.get("industrial_pct", 0)),
                    demand_multiplier=float(
                        row.get(
                            "demand_multiplier",
                            self._zone_base_multipliers.get(zone_type, 1.0),
                        )
                    ),
                    weekend_factor=float(
                        row.get(
                            "weekend_factor",
                            self._zone_weekend_factors.get(zone_type, 0.75),
                        )
                    ),
                    hour_weights=self._zone_hour_weights.get(zone_type, {}),
                    weekday_factors=self._zone_weekday_factors.get(zone_type, {}),
                )
                self._barrio_profiles[barrio] = profile

    # ── API pública ────────────────────────────────────────────────────────────

    def get_profile_for_radio(self, radio_code: str) -> ZoneProfile:
        """Retorna el ZoneProfile para un radio censal."""
        code = str(radio_code).strip()
        if code in self._radio_profiles:
            return self._radio_profiles[code]
        return DEFAULT_PROFILE

    def get_profile(self, zone_identifier: str) -> ZoneProfile:
        """
        Resuelve el perfil ya sea por código de radio censal, nombre de barrio
        o tipo de zona directo (e.g. 'residential_multifamily', 'commercial').
        Lee todos los multiplicadores y factores de la configuración YAML.
        """
        if not zone_identifier:
            return DEFAULT_PROFILE

        key = str(zone_identifier).strip()
        # 1. Búsqueda directa por radio censal
        if key in self._radio_profiles:
            return self._radio_profiles[key]

        # 2. Búsqueda directa si se pasa el zone_type
        low_key = key.lower()
        if low_key in self._zone_hour_weights:
            base_mult = self._zone_base_multipliers.get(low_key, 1.0)
            base_wknd = self._zone_weekend_factors.get(low_key, 0.75)
            return ZoneProfile(
                zone_key=low_key,
                barrio=low_key,
                zone_type=low_key,
                res_multifamily_pct=100.0
                if low_key == "residential_multifamily"
                else 0.0,
                res_singlefamily_pct=100.0
                if low_key == "residential_singlefamily"
                else 0.0,
                commercial_pct=100.0 if low_key == "commercial" else 0.0,
                office_pct=100.0 if low_key == "office" else 0.0,
                industrial_pct=100.0 if low_key == "industrial" else 0.0,
                demand_multiplier=base_mult,
                weekend_factor=base_wknd,
                hour_weights=self._zone_hour_weights.get(low_key, {}),
                weekday_factors=self._zone_weekday_factors.get(low_key, {}),
            )

        # 3. Búsqueda por nombre exacto de barrio (la topología sintética del CLI
        # usa barrios como zona)
        return self._barrio_profiles.get(key.upper(), DEFAULT_PROFILE)

    def summary(self) -> dict[str, int]:
        """Distribución de zone_types de los radios censales cargados."""
        counts: dict[str, int] = {}
        for p in self._radio_profiles.values():
            counts[p.zone_type] = counts.get(p.zone_type, 0) + 1
        return counts


# ── Singleton ──────────────────────────────────────────────────────────────────
_cached_classifier: ZoneClassifier | None = None


def get_zone_classifier() -> ZoneClassifier:
    """Instancia singleton del ZoneClassifier."""
    global _cached_classifier
    if _cached_classifier is None:
        _cached_classifier = ZoneClassifier()
    return _cached_classifier


def reload_zone_classifier() -> ZoneClassifier:
    """Vuelve a crear el ZoneClassifier, releyendo zone_profiles.yaml.

    El worker la llama al iniciar cada sesion, asi los cambios del YAML se
    aplican en la simulacion siguiente sin reiniciar el proceso.
    """
    global _cached_classifier
    _cached_classifier = ZoneClassifier()
    return _cached_classifier

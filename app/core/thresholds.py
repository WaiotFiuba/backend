"""Umbrales de nivel de llenado (%) compartidos por toda la aplicación.

Es la única fuente de verdad: KPIs, mapa, proyección y redistribución leen los
umbrales de acá, y el frontend los obtiene a través de GET /map/config. Se
pueden sobrescribir con las variables de entorno LEVEL_THRESHOLD_NORMAL,
LEVEL_THRESHOLD_HIGH y LEVEL_THRESHOLD_CRITICAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.config import Settings, get_settings

FULL_LEVEL = 100


@dataclass(frozen=True)
class LevelThresholds:
    """Límites inferiores (inclusive) de cada categoría de nivel."""

    normal: int
    high: int
    critical: int
    full: int = FULL_LEVEL

    def __post_init__(self) -> None:
        if not 0 < self.normal < self.high < self.critical <= self.full:
            raise ValueError(
                "Los umbrales deben cumplir 0 < normal < high < critical <= full; "
                f"se recibió normal={self.normal}, high={self.high}, "
                f"critical={self.critical}, full={self.full}."
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> LevelThresholds:
        return cls(
            normal=settings.level_threshold_normal,
            high=settings.level_threshold_high,
            critical=settings.level_threshold_critical,
        )


@lru_cache
def get_level_thresholds() -> LevelThresholds:
    return LevelThresholds.from_settings(get_settings())

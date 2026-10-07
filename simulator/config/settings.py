from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorSettings(BaseSettings):
    """Configuracion del simulador. Lee las mismas variables de entorno / .env que
    antes leia app.core.config (SIMULATOR_*, DENSITY_*, ...)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # BACKEND_URL; se acepta tambien SIMULATOR_BACKEND_URL (nombre anterior)
    # para no romper los .env existentes.
    backend_url: str = Field(
        default="http://api:8000",
        validation_alias=AliasChoices("backend_url", "simulator_backend_url"),
    )
    simulator_poll_seconds: float = 2.0
    simulator_batch_size: int = 100000  # mediciones por request; si supera la cantidad de contenedores, cada tick va en un solo request
    simulator_container_limit: int | None = None
    simulator_control_miss_tolerance: int = 5
    promedio_generacion_basura_personas_24h: float = 1.5  # kg por persona cada 24 horas
    densidad_basura_kg_m3: float = 150.0  # kg/m3 de residuo suelto
    density_street_buffer_m: float = (
        18.0  # buffer en metros para conectar contenedores a radios censales vecinos
    )
    density_min_daily_fill_pct_floor: float = (
        20.0  # piso minimo de llenado diario (%) en zonas de baja poblacion
    )
    density_fallback_daily_waste_kg: float = (
        150.0  # kg/dia de fallback para contenedores sin ningun radio conectado
    )

    @field_validator("simulator_container_limit", mode="before")
    @classmethod
    def parse_container_limit(cls, v: Any) -> int | None:
        if v == "" or v is None:
            return None
        return int(v)


@lru_cache
def get_settings() -> SimulatorSettings:
    return SimulatorSettings()

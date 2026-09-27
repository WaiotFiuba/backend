from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 0
    map_db_pool_size: int = 5
    map_db_max_overflow: int = 0
    auto_create_db: bool = True
    auto_create_map_db: bool = False
    cors_allowed_origins: str = "*"
    enable_map_db: bool = False
    simulator_backend_url: str = "http://api:8000"
    simulator_poll_seconds: float = 2.0
    simulator_batch_size: int = 100000  # cubre un batch combinado (maxsize=2 ticks) sin fragmentarse en 2 requests
    simulator_container_limit: int | None = None
    simulator_control_miss_tolerance: int = 5
    simulator_delivery_queue_maxsize: int = 2
    simulator_delivery_drain_timeout: float = 30.0
    map_database_url: str = (
        "postgresql+asyncpg://waiot:waiot_pass@postgis:5432/waiot_map"
    )
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
def get_settings() -> Settings:
    return Settings()

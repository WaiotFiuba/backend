from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    db_echo: bool = False
    auto_create_db: bool = True
    auto_create_map_db: bool = False
    enable_map_db: bool = False
    simulator_backend_url: str = "http://api:8000"
    simulator_poll_seconds: float = 2.0
    simulator_batch_size: int = 100000  # cubre un batch combinado (maxsize=2 ticks) sin fragmentarse en 2 requests
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

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()

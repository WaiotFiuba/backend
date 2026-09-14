from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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
    simulator_batch_size: int = 30000
    map_database_url: str = (
        "postgresql+asyncpg://waiot:waiot_pass@postgis:5432/waiot_map"
    )
    promedio_generacion_basura_personas_24h: float = 1.5  # kg por persona cada 24 horas
    densidad_basura_kg_m3: float = 150.0  # kg/m3 de residuo suelto

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()

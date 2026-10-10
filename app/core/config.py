from functools import lru_cache

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
    simulation_claim_timeout_seconds: int = 60
    simulation_startup_timeout_minutes: int = 10
    enable_map_db: bool = False
    map_database_url: str = (
        "postgresql+asyncpg://waiot:waiot_pass@postgis:5432/waiot_map"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

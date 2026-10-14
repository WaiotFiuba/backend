from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    map_db_pool_size: int = 5
    map_db_max_overflow: int = 0
    cors_allowed_origins: str = "*"
    simulation_claim_timeout_seconds: int = 60
    simulation_startup_timeout_minutes: int = 10
    level_threshold_normal: int = 40
    level_threshold_high: int = 70
    level_threshold_critical: int = 80
    # Periodos de data_level que se conservan (~17 MB cada uno); 0 = sin limite.
    data_level_retention_periods: int = 1920
    data_level_size_warning_gb: float = 70.0
    log_level: str = "INFO"
    enable_map_db: bool = False
    map_database_url: str = (
        "postgresql+asyncpg://waiot:waiot_pass@postgis:5432/waiot_map"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

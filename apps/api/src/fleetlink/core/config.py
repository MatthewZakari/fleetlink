"""Immutable settings; environment only, never implicit dotenv discovery."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLEETLINK_", extra="ignore", frozen=True, hide_input_in_errors=True
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    database_enabled: bool = False
    postgres_host: str = Field(default="127.0.0.1", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="fleetlink_dev", min_length=1)
    postgres_user: SecretStr = Field(default=SecretStr("fleetlink_dev"), repr=False)
    postgres_password: SecretStr = Field(default=SecretStr("development-only-postgres"), repr=False)
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    database_pool_timeout: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    database_connect_timeout: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    database_command_timeout: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)

    def database_url(self) -> URL:
        """Construct a driver URL without interpolation; never log this URL."""
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user.get_secret_value(),
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

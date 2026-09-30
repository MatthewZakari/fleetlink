"""Immutable settings; environment only, never implicit dotenv discovery."""

from typing import Literal
from urllib.parse import quote

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

    redis_enabled: bool = False
    redis_host: str = Field(default="127.0.0.1", min_length=1, pattern=r"^[a-zA-Z0-9.:-]+$")
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_username: SecretStr | None = Field(default=None, repr=False)
    redis_password: SecretStr | None = Field(default=None, repr=False)
    redis_max_connections: int = Field(default=5, ge=1, le=50)
    redis_connect_timeout: float = Field(default=2, gt=0, le=30, allow_inf_nan=False)
    redis_operation_timeout: float = Field(default=2, gt=0, le=30, allow_inf_nan=False)

    celery_enabled: bool = False
    rabbitmq_host: str = Field(default="127.0.0.1", min_length=1, pattern=r"^[a-zA-Z0-9.:-]+$")
    rabbitmq_amqp_port: int = Field(default=5672, ge=1, le=65535)
    rabbitmq_user: SecretStr = Field(default=SecretStr("fleetlink_dev"), repr=False)
    rabbitmq_password: SecretStr = Field(default=SecretStr("development-only-rabbitmq"), repr=False)
    rabbitmq_vhost: str = Field(default="/", min_length=1, max_length=128)
    broker_connect_timeout: float = Field(default=2, gt=0, le=30, allow_inf_nan=False)
    broker_operation_timeout: float = Field(default=2, gt=0, le=30, allow_inf_nan=False)
    celery_queue: str = Field(
        default="fleetlink.technical.v1",
        pattern=r"^fleetlink\.technical\.v1(?:\.fl006\.[a-f0-9]{32})?$",
    )
    celery_concurrency: int = Field(default=1, ge=1, le=8)

    def broker_url(self) -> SecretStr:
        """Encoded AMQP URL; callers must never log its revealed value."""
        user = quote(self.rabbitmq_user.get_secret_value(), safe="")
        password = quote(self.rabbitmq_password.get_secret_value(), safe="")
        host = self.rabbitmq_host
        if ":" in host:
            host = f"[{host}]"
        vhost = quote(self.rabbitmq_vhost, safe="")
        return SecretStr(f"amqp://{user}:{password}@{host}:{self.rabbitmq_amqp_port}/{vhost}")

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

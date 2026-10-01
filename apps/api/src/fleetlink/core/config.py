"""Immutable settings; environment only, never implicit dotenv discovery."""

import os
import re
from ipaddress import IPv6Address
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


def reject_otel_overrides() -> None:
    """Upstream ambient configuration must not bypass FleetLink's privacy policy."""
    if any(key.startswith("OTEL_") for key in os.environ):
        raise ValueError("Unsupported OpenTelemetry environment override; use FLEETLINK_ settings")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLEETLINK_", extra="ignore", frozen=True, hide_input_in_errors=True
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    telemetry_enabled: bool = False
    otel_exporter_otlp_endpoint: SecretStr | None = Field(default=None, repr=False)
    otel_export_timeout_seconds: float = Field(default=2, gt=0, le=10, allow_inf_nan=False)
    otel_sample_ratio: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)

    @field_validator("otel_exporter_otlp_endpoint")
    @classmethod
    def validate_otel_endpoint(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        try:
            raw = value.get_secret_value()
            parsed = urlsplit(raw)
            hostname = parsed.hostname or ""
            if ":" in hostname:
                IPv6Address(hostname)
                host_valid = True
            else:
                host_valid = (
                    bool(
                        re.fullmatch(
                            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*",
                            hostname,
                        )
                    )
                    and len(hostname) <= 253
                )
            valid = (
                host_valid
                and not parsed.netloc.endswith(":")
                and parsed.scheme in ("http", "https")
                and parsed.hostname is not None
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and parsed.path in ("", "/")
                and (parsed.port is None or 1 <= parsed.port <= 65535)
                and not any(char.isspace() or ord(char) < 32 for char in raw)
                and not any(char in raw for char in ("\\", "%", "?", "#"))
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("OTLP endpoint must be an HTTP(S) origin without credentials or paths")
        return value

    @model_validator(mode="after")
    def validate_telemetry(self) -> "Settings":
        if self.telemetry_enabled:
            if self.otel_exporter_otlp_endpoint is None:
                raise ValueError("Enabled telemetry requires FLEETLINK_OTEL_EXPORTER_OTLP_ENDPOINT")
            # SDK/exporter environment fallbacks can capture secrets or replace ownership.
            reject_otel_overrides()
        return self

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

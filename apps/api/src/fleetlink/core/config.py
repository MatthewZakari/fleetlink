"""Validated environment settings; no infrastructure configuration yet."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLEETLINK_", extra="ignore", frozen=True, hide_input_in_errors=True
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

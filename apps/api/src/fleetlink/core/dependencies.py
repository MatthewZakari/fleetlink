"""Native request dependencies; no environment reads or service container."""

from fastapi import Request

from fleetlink.core.config import Settings


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_correlation_id(request: Request) -> str:
    request_id: str = request.state.correlation_id
    return request_id

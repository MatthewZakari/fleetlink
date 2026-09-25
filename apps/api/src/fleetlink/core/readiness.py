"""Application lifecycle readiness, with no external dependency checks."""

from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ready", "not_ready"]]
    dependency_checks: Literal["not_configured"] = "not_configured"


@dataclass
class Readiness:
    initialized: bool = False

    def snapshot(self) -> ReadinessResponse:
        status: Literal["ready", "not_ready"] = "ready" if self.initialized else "not_ready"
        return ReadinessResponse(status=status, checks={"application": status})


def get_readiness(request: Request) -> Readiness:
    readiness: Readiness = request.app.state.readiness
    return readiness

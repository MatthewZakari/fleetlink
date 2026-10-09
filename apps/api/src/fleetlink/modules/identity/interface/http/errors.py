"""Allowlisted authentication failures, without account or evidence disclosure."""

from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import Request, Response
from fastapi.routing import APIRoute

from fleetlink.core.http import Problem, problem, problem_responses
from fleetlink.modules.identity.application.ports import IdentityConflict, UserNotFound
from fleetlink.modules.identity.application.refresh_authentication import (
    RefreshAccountUnavailable,
    RefreshPossessionFailed,
)
from fleetlink.modules.identity.application.refresh_ports import (
    RefreshTokenConflict,
    RefreshTokenNotFound,
)
from fleetlink.modules.identity.application.refresh_protocol import InvalidRefreshCredential
from fleetlink.modules.identity.application.session_ports import SessionConflict, SessionNotFound
from fleetlink.modules.identity.domain.refresh_token import (
    RefreshSessionUnavailable,
    RefreshTokenExpired,
    RefreshTokenReuse,
)


class AuthenticationProblem(Problem):
    """Same seven-field envelope as Platform; fixed public denial semantics."""

    title: Literal["Unauthorized"] = "Unauthorized"
    status: Literal[401] = 401
    detail: Literal["Authentication failed."] = "Authentication failed."
    code: Literal["authentication_failed"] = "authentication_failed"


class AuthenticationDenied(RuntimeError):
    """Transport denial, including confirmed reuse AFTER its revocation commits."""


AUTHENTICATION_FAILURES = (
    AuthenticationDenied,
    InvalidRefreshCredential,
    RefreshPossessionFailed,
    RefreshAccountUnavailable,
    UserNotFound,
    SessionNotFound,
    RefreshTokenNotFound,
    IdentityConflict,
    SessionConflict,
    RefreshTokenConflict,
    RefreshTokenExpired,
    RefreshTokenReuse,
    RefreshSessionUnavailable,
)


def identity_problem_responses() -> dict[int | str, dict[str, object]]:
    """Use with a test/future APIRouter; this does not register any routes."""
    return {
        **problem_responses(),
        401: {
            "description": "Authentication failed",
            "content": {
                "application/problem+json": {"schema": AuthenticationProblem.model_json_schema()}
            },
        },
    }


class IdentityRoute(APIRoute):
    """Map only expected denials, after operation rollback/session cleanup.

    Validation, HTTP errors and unexpected exceptions retain Platform handling.
    Never use these authentication mappings for future authorized management APIs:
    their disclosure-safe resource/conflict policy requires a separate contract.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except AUTHENTICATION_FAILURES:
                return problem(
                    401,
                    "authentication_failed",
                    "Authentication failed.",
                    request.state.correlation_id,
                )

        return handle

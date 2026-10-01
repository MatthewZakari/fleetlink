"""Small synchronous secret-source boundary; no discovery, network or refresh."""

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import InitVar, dataclass, field
from types import MappingProxyType
from typing import Protocol
from urllib.parse import quote, quote_plus

from pydantic import SecretStr, ValidationError
from pydantic_core import InitErrorDetails

SECRET_FIELDS = frozenset(
    {
        "postgres_user",
        "postgres_password",
        "rabbitmq_user",
        "rabbitmq_password",
        "redis_username",
        "redis_password",
        "otel_exporter_otlp_endpoint",
    }
)


class SecretSource(Protocol):
    def resolve(self, name: str) -> SecretStr | None:
        """Resolve an explicitly named FLEETLINK variable; None means absent."""
        ...


@dataclass(frozen=True, repr=False)
class EnvironmentSecretSource:
    values: InitVar[Mapping[str, str] | None] = None
    _secrets: Mapping[str, SecretStr] = field(init=False, repr=False)

    def __post_init__(self, values: Mapping[str, str] | None) -> None:
        inputs = os.environ if values is None else values
        snapshot = {
            key: SecretStr(inputs[key])
            for name in SECRET_FIELDS
            if (key := f"FLEETLINK_{name.upper()}") in inputs
        }
        object.__setattr__(self, "_secrets", MappingProxyType(snapshot))

    def resolve(self, name: str) -> SecretStr | None:
        return self._secrets.get(name)


def safe_event(message: object, args: object) -> str:
    """Dynamic messages are never diagnostic events. Do not format their arguments."""
    events = {
        "request_completed",
        "request_failed",
        "technical_task_started",
        "technical_task_retry",
        "technical_task_failed",
        "technical_task_completed",
        "telemetry_export_failed",
        "telemetry_runtime_event",
        "worker_runtime_event",
    }
    return (
        message
        if isinstance(message, str) and message in events and not args
        else "application_event"
    )


@dataclass(frozen=True, repr=False)
class Redactor:
    """Immutable credential snapshot, owned by one application/resource lifetime."""

    secrets: tuple[SecretStr, ...] = ()

    def text(self, value: str) -> str:
        # Unwrapping here is solely for the diagnostic security boundary.
        variants: set[str] = set()
        for secret in self.secrets:
            raw = secret.get_secret_value()
            if raw:
                variants.update((raw, quote(raw, safe=""), quote_plus(raw, safe="")))
        for raw in sorted(variants, key=len, reverse=True):
            value = value.replace(raw, "<redacted>")
        return value


_EMPTY_REDACTOR = Redactor()  # Frozen tuple, shared safely; no mutable registry.
active_redactor: ContextVar[Redactor] = ContextVar("active_redactor", default=_EMPTY_REDACTOR)


@contextmanager
def redaction_scope(redactor: Redactor) -> Iterator[None]:
    token = active_redactor.set(redactor)
    try:
        yield
    finally:
        active_redactor.reset(token)


def safe_error_type(value: object) -> str:
    names = {
        "ValueError",
        "RuntimeError",
        "OSError",
        "TimeoutError",
        "ConnectionError",
        "DatabaseError",
        "RedisError",
        "BrokerError",
        "CompletionTimeout",
        "InvalidProbe",
        "TransientProbeError",
        "ProbeExhausted",
        "Retry",
    }
    return value if isinstance(value, str) and value in names else "operation_error"


def sanitized_validation(error: ValidationError, title: str) -> ValidationError:
    """Remove input/context from every Pydantic diagnostic serialization surface."""
    details: list[InitErrorDetails] = []
    for item in error.errors(include_input=False, include_context=False):
        details.append(
            {
                "type": "value_error",
                "loc": item["loc"],
                "input": "<redacted>",
                "ctx": {"error": ValueError(item["msg"])},
            }
        )
    return ValidationError.from_exception_data(title, details, hide_input=True)

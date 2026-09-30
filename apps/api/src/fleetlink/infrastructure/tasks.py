"""Harmless, explicitly registered transport probe. No domain dependencies."""

from __future__ import annotations

import logging

from celery import Celery, Task
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fleetlink.core.logging import correlation_id

TASK_NAME = "fleetlink.technical.probe.v1"
MAX_RETRIES = 2


class ProbePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    probe_id: str = Field(pattern=r"^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$")
    value: int = Field(ge=-1000, le=1000)
    failures_before_success: int = Field(default=0, ge=0, le=3)
    reject: bool = False


class InvalidProbe(ValueError):
    """Non-retryable technical input/failure."""


class TransientProbeError(RuntimeError):
    """Deterministic technical retry trigger."""


class ProbeExhausted(RuntimeError):
    """The three-attempt technical budget was exhausted."""


def validate_payload(payload: object) -> ProbePayload:
    try:
        return ProbePayload.model_validate(payload)
    except ValidationError:
        raise InvalidProbe("Invalid technical probe payload") from None


def execute_probe(payload: ProbePayload, retries: int) -> dict[str, object]:
    if payload.reject:
        raise InvalidProbe("Technical probe rejected; attempts=1")
    if retries < payload.failures_before_success:
        if retries >= MAX_RETRIES:
            raise ProbeExhausted("Technical retry budget exhausted; attempts=3")
        raise TransientProbeError("Technical retry requested")
    return {"probe_id": payload.probe_id, "value": payload.value * 2, "attempts": retries + 1}


def register_technical_task(app: Celery) -> None:
    @app.task(name=TASK_NAME, bind=True, shared=False, lazy=False, max_retries=MAX_RETRIES)
    def probe(task: Task[[object], dict[str, object]], payload: object) -> dict[str, object]:
        data = validate_payload(payload)
        token = correlation_id.set(data.probe_id)
        logger = logging.getLogger("fleetlink.worker")
        try:
            logger.info("technical_task_started")
            try:
                result = execute_probe(data, task.request.retries)
            except TransientProbeError as error:
                logger.info("technical_task_retry")
                raise task.retry(exc=error, countdown=min(2**task.request.retries, 2)) from error
            except (InvalidProbe, ProbeExhausted) as error:
                logger.warning("technical_task_failed", extra={"error_type": type(error).__name__})
                raise
            logger.info("technical_task_completed")
            return result
        finally:
            correlation_id.reset(token)

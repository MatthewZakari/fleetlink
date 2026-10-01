"""Explicit, instance-owned OpenTelemetry resources; no global provider registration."""

from fleetlink.observability.runtime import Telemetry, TelemetrySlot

__all__ = ["Telemetry", "TelemetrySlot"]

"""Canonical identity and eligibility only; no authentication or resource policy."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


class PlatformRole(StrEnum):
    CUSTOMER = "customer"
    MERCHANT = "merchant"
    RIDER = "rider"
    ADMINISTRATOR = "administrator"


@dataclass(frozen=True, slots=True)
class User:
    """Immutable snapshot; equality compares state, canonical identity is ``id``."""

    id: UUID
    created_at: datetime
    status: AccountStatus = AccountStatus.ACTIVE
    roles: frozenset[PlatformRole] = frozenset()
    version: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("User identity must be a UUID")
        if not isinstance(self.status, AccountStatus):
            raise ValueError("Invalid account status")
        if not isinstance(self.roles, frozenset) or any(
            not isinstance(role, PlatformRole) for role in self.roles
        ):
            raise ValueError("Invalid platform roles")
        if not isinstance(self.created_at, datetime) or self.created_at.utcoffset() is None:
            raise ValueError("Creation time must be timezone aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        if type(self.version) is not int or self.version < 0:
            raise ValueError("Version must be a nonnegative integer")

    def with_status(self, status: AccountStatus) -> "User":
        return replace(self, status=status)

    def assign_role(self, role: PlatformRole) -> "User":
        if not isinstance(role, PlatformRole):
            raise ValueError("Invalid platform role")
        return replace(self, roles=self.roles | {role})

    def remove_role(self, role: PlatformRole) -> "User":
        if not isinstance(role, PlatformRole):
            raise ValueError("Invalid platform role")
        return replace(self, roles=self.roles - {role})

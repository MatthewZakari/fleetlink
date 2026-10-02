"""Domain invariants, dependency direction and reviewed offline schema."""

import ast
import io
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

from fleetlink.modules.identity.domain.user import AccountStatus, PlatformRole, User

USER_ID = UUID("00000000-0000-4000-8000-000000000009")
CREATED = datetime(2026, 1, 1, tzinfo=UTC)


def test_identity_is_stable_and_snapshots_are_immutable() -> None:
    user = User(USER_ID, CREATED)
    assert user.id == USER_ID
    assert user.status is AccountStatus.ACTIVE
    assert user.roles == frozenset()
    assert user.version == 0
    assert user == User(USER_ID, CREATED)
    suspended = user.with_status(AccountStatus.SUSPENDED)
    assert suspended.id == user.id
    assert suspended != user
    assert user.status is AccountStatus.ACTIVE

    def mutate(target: object, field: str) -> None:
        setattr(target, field, UUID(int=1))

    with pytest.raises(FrozenInstanceError):
        mutate(user, "id")


@pytest.mark.parametrize("status", list(AccountStatus))
def test_status_values(status: AccountStatus) -> None:
    assert User(USER_ID, CREATED).with_status(status).status is status


@pytest.mark.parametrize("role", list(PlatformRole))
def test_role_assignment_and_removal_are_idempotent(role: PlatformRole) -> None:
    user = User(USER_ID, CREATED)
    assigned = user.assign_role(role)
    assert assigned.roles == frozenset({role})
    assert assigned.assign_role(role) == assigned
    assert assigned.remove_role(role) == user
    assert user.remove_role(role) == user


def test_one_identity_supports_all_roles_without_profiles() -> None:
    user = User(USER_ID, CREATED)
    for role in PlatformRole:
        user = user.assign_role(role)
    assert user.id == USER_ID
    assert user.roles == frozenset(PlatformRole)
    assert user.remove_role(PlatformRole.MERCHANT).roles == frozenset(
        {PlatformRole.CUSTOMER, PlatformRole.RIDER, PlatformRole.ADMINISTRATOR}
    )


def test_invalid_status_roles_and_uuid_are_rejected() -> None:
    with pytest.raises(ValueError, match="status"):
        User(USER_ID, CREATED, status=cast(AccountStatus, "active"))
    with pytest.raises(ValueError):
        AccountStatus("unknown")
    with pytest.raises(ValueError):
        PlatformRole("superuser")
    user = User(USER_ID, CREATED)
    with pytest.raises(ValueError, match="status"):
        user.with_status(cast(AccountStatus, "unknown"))
    with pytest.raises(ValueError, match="roles"):
        replace(user, roles=cast(frozenset[PlatformRole], frozenset({"customer"})))
    with pytest.raises(ValueError, match="roles"):
        replace(user, roles=cast(frozenset[PlatformRole], {PlatformRole.CUSTOMER}))
    for operation in (user.assign_role, user.remove_role):
        with pytest.raises(ValueError, match="role"):
            operation(cast(PlatformRole, "customer"))
    with pytest.raises(ValueError, match="UUID"):
        User(cast(UUID, str(USER_ID)), CREATED)


@pytest.mark.parametrize("version", [-1, True, 1.5])
def test_invalid_version(version: object) -> None:
    with pytest.raises(ValueError, match="Version"):
        User(USER_ID, CREATED, version=cast(int, version))


def test_creation_time_requires_awareness_and_normalizes_to_utc() -> None:
    with pytest.raises(ValueError, match="timezone"):
        User(USER_ID, datetime(2026, 1, 1))
    offset = datetime(2026, 1, 1, 5, tzinfo=timezone(timedelta(hours=5)))
    user = User(USER_ID, offset)
    assert user.created_at == CREATED
    assert user.created_at.tzinfo is UTC


def test_inner_layers_import_only_standard_library_or_identity_inner_layers() -> None:
    import sys

    root = Path(__file__).resolve().parents[1] / "src/fleetlink/modules/identity"
    for layer in ("domain", "application"):
        for path in (root / layer).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    assert node.level == 0
                    names = [node.module or ""]
                for name in names:
                    assert name.split(".")[0] in sys.stdlib_module_names or name.startswith(
                        (
                            "fleetlink.modules.identity.domain.",
                            "fleetlink.modules.identity.application.",
                        )
                    ), (path, name)


def test_offline_identity_migration_is_owned_and_reversible() -> None:
    output = io.StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE identity_users" in sql
    assert "CREATE TABLE identity_user_roles" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "PRIMARY KEY (user_id, role)" in sql
    assert "ON DELETE CASCADE" in sql
    output.seek(0)
    output.truncate()
    command.downgrade(config, "0002_identity_foundation:0001_technical_baseline", sql=True)
    sql = output.getvalue()
    assert "DROP TABLE identity_user_roles" in sql
    assert "DROP TABLE identity_users" in sql
    assert "DROP EXTENSION" not in sql
    assert "spatial_ref_sys" not in sql

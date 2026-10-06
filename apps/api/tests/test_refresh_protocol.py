"""FL-012 synthetic protocol vectors; never print or snapshot bearer material."""

import base64
import hashlib
import hmac
import logging
import secrets
import traceback
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from fleetlink.core.logging import JsonFormatter
from fleetlink.infrastructure.database import Database
from fleetlink.modules.identity.application import refresh_protocol as protocol
from fleetlink.modules.identity.domain.refresh_token import (
    RefreshTokenRecord,
    RefreshTokenStatus,
    RefreshVerifier,
)
from fleetlink.modules.identity.infrastructure.refresh_repository import (
    SqlAlchemyRefreshTokenRepository,
)

IDENTIFIER = UUID("00000000-0000-4000-8000-000000000012")
CREATED = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def credential() -> protocol.RefreshCredential:
    # Deliberately synthetic material, assembled in memory instead of a wire fixture.
    return protocol.RefreshCredential(IDENTIFIER, bytes(range(32)))


@pytest.fixture
def record(credential: protocol.RefreshCredential) -> RefreshTokenRecord:
    return RefreshTokenRecord(
        credential.candidate_id,
        UUID(int=10),
        protocol.derive_refresh_verifier(credential),
        CREATED,
        CREATED + timedelta(hours=1),
    )


def test_secure_generation_unique_and_round_trip() -> None:
    issued = [protocol.generate_refresh_credential() for _ in range(128)]
    assert len({item.credential.candidate_id for item in issued}) == len(issued)
    assert len({item.credential.reveal().rsplit(".", 1)[1] for item in issued}) == len(issued)
    assert len({item.verifier.value for item in issued}) == len(issued)
    for item in issued:
        assert item.credential.candidate_id.version == 4
        assert len(item.credential.reveal()) == 86
        parsed = protocol.parse_refresh_credential(item.credential.reveal())
        assert parsed.candidate_id == item.credential.candidate_id
        assert bool(parsed.reveal() == item.credential.reveal())
        assert protocol.derive_refresh_verifier(parsed) == item.verifier
        assert 1 <= len(item.verifier.value) == 45 <= 512
        secret = base64.urlsafe_b64decode(item.credential.reveal().rsplit(".", 1)[1] + "=")
        assert len(secret) == 32
        assert bool(secret != item.verifier.value)
        assert bool(secret != item.verifier.value[13:])
        assert bool(item.credential.reveal().encode() != item.verifier.value)
        record = RefreshTokenRecord(
            parsed.candidate_id, UUID(int=10), item.verifier, CREATED, CREATED + timedelta(hours=1)
        )
        assert protocol.verify_refresh_credential(item.credential.reveal(), record)


def test_deterministic_seam_uses_explicit_32_random_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[int] = []

    def synthetic_bytes(size: int) -> bytes:
        requested.append(size)
        return bytes(range(size))

    monkeypatch.setattr(secrets, "token_bytes", synthetic_bytes)
    monkeypatch.setattr(protocol, "uuid4", lambda: IDENTIFIER)
    issued = protocol.generate_refresh_credential()
    assert requested == [32]
    assert issued.credential.candidate_id == IDENTIFIER
    # Independently assemble the specified hash preimage to pin version/UUID binding.
    expected = hashlib.sha256(
        b"fleetlink.refresh-token"
        + bytes([0])
        + b"flrt1"
        + bytes([0])
        + IDENTIFIER.bytes
        + bytes(range(32))
    ).digest()
    assert issued.verifier.value == b"flrt1:sha256:" + expected
    assert protocol.derive_refresh_verifier(issued.credential) == issued.verifier


def test_possession_and_identifier_binding(
    credential: protocol.RefreshCredential, record: RefreshTokenRecord
) -> None:
    assert protocol.verify_refresh_credential(credential.reveal(), record)
    wrong_secret = protocol.RefreshCredential(IDENTIFIER, bytes(reversed(range(32))))
    assert not protocol.verify_refresh_credential(wrong_secret.reveal(), record)
    assert not protocol.verify_refresh_credential(str(IDENTIFIER), record)
    other_id = UUID(int=99)
    moved = replace(record, id=other_id)
    assert not protocol.verify_refresh_credential(credential.reveal(), moved)
    changed_id = protocol.RefreshCredential(other_id, bytes(range(32)))
    assert not protocol.verify_refresh_credential(changed_id.reveal(), record)
    assert not protocol.verify_refresh_credential(changed_id.reveal(), moved)
    assert protocol.derive_refresh_verifier(changed_id) != record.verifier


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "uuid_only",
        "empty_secret",
        "oversized",
        "truncated",
        "short_secret",
        "long_secret",
        "invalid_uuid",
        "compact_uuid",
        "uppercase_uuid",
        "braced_uuid",
        "invalid_encoding",
        "standard_base64",
        "padding",
        "pad_bits",
        "unicode",
        "newline",
        "space",
        "extra_component",
        "unsupported_version",
        "bytes",
        "none",
        "int",
    ],
)
def test_malformed_credentials_fail_closed_without_input_in_errors(
    case: str, credential: protocol.RefreshCredential, record: RefreshTokenRecord
) -> None:
    wire = credential.reveal()
    version, identifier, encoded = wire.split(".")
    candidates: dict[str, object] = {
        "empty": "",
        "uuid_only": identifier,
        "empty_secret": f"{version}.{identifier}.",
        "oversized": wire + "x" * 100_000,
        "truncated": wire[:-1],
        "short_secret": f"{version}.{identifier}." + encoded[:-4],
        "long_secret": wire + "AAAA",
        "invalid_uuid": wire.replace(identifier, "g" * 36),
        "compact_uuid": wire.replace(identifier, identifier.replace("-", "")),
        "uppercase_uuid": wire.replace(identifier, "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        "braced_uuid": wire.replace(identifier, "{" + identifier + "}"),
        "invalid_encoding": wire[:-1] + "!",
        "standard_base64": wire[:-1] + "+",
        "padding": wire[:-1] + "=",
        # Last canonical character encodes six bits, of which two must be zero.
        "pad_bits": wire[:-1] + "9",
        "unicode": wire[:-1] + "é",
        "newline": wire[:-1] + "\n",
        "space": wire[:-1] + " ",
        "extra_component": wire + ".extra",
        "unsupported_version": wire.replace("flrt1", "flrt2", 1),
        "bytes": wire.encode(),
        "none": None,
        "int": 12,
    }
    presented = candidates[case]
    before = replace(record)
    assert not protocol.verify_refresh_credential(presented, record)
    assert record == before
    with pytest.raises(protocol.InvalidRefreshCredential) as caught:
        protocol.parse_refresh_credential(presented)
    assert str(caught.value) == "Invalid refresh credential"
    assert caught.value.args == ("Invalid refresh credential",)
    diagnostics = repr(caught.value) + "".join(traceback.format_exception(caught.value))
    assert bool(encoded not in diagnostics)
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "evidence",
    [
        b"x",
        b"x" * 512,
        b"flrt1:sha256:",
        b"flrt2:sha256:" + bytes(32),
        b"flrt1:sha512:" + bytes(32),
        b"flrt1:sha256:" + bytes(31),
        b"flrt1:sha256:" + bytes(33),
        bytes(32),
    ],
)
def test_unknown_or_corrupt_verifier_fails_closed(
    evidence: bytes, credential: protocol.RefreshCredential, record: RefreshTokenRecord
) -> None:
    assert not protocol.verify_refresh_credential(
        credential.reveal(), replace(record, verifier=RefreshVerifier(evidence))
    )


def test_digest_comparison_uses_safe_primitive_for_equal_length_bytes(
    monkeypatch: pytest.MonkeyPatch,
    credential: protocol.RefreshCredential,
    record: RefreshTokenRecord,
) -> None:
    compare = hmac.compare_digest
    calls: list[tuple[int, int]] = []

    def compared(left: bytes, right: bytes) -> bool:
        assert type(left) is bytes and type(right) is bytes
        calls.append((len(left), len(right)))
        return compare(left, right)

    monkeypatch.setattr(hmac, "compare_digest", compared)
    assert protocol.verify_refresh_credential(credential.reveal(), record)
    wrong = protocol.RefreshCredential(IDENTIFIER, bytes(reversed(range(32))))
    assert not protocol.verify_refresh_credential(wrong.reveal(), record)
    assert calls == [(32, 32), (32, 32)]


def test_possession_is_independent_of_lifecycle_and_never_mutates(
    credential: protocol.RefreshCredential, record: RefreshTokenRecord
) -> None:
    consumed = record.consume(UUID(int=13), CREATED + timedelta(minutes=1))
    for snapshot in (record, consumed):
        before = replace(snapshot)
        assert protocol.verify_refresh_credential(credential.reveal(), snapshot)
        assert not protocol.verify_refresh_credential("invalid", snapshot)
        assert snapshot == before
    assert record.status is RefreshTokenStatus.CURRENT
    assert consumed.status is RefreshTokenStatus.CONSUMED


def test_representations_immutability_and_controlled_diagnostics(
    credential: protocol.RefreshCredential,
    record: RefreshTokenRecord,
    caplog: pytest.LogCaptureFixture,
) -> None:
    issued = protocol.IssuedRefreshCredential(credential, record.verifier)
    wire = credential.reveal()
    secret = wire.rsplit(".", 1)[1]
    diagnostics = " ".join(repr(value) + str(value) for value in (credential, issued, record))
    assert bool(wire not in diagnostics and secret not in diagnostics)

    def mutate(target: object, name: str) -> None:
        setattr(target, name, bytes(32))

    with pytest.raises(FrozenInstanceError):
        mutate(credential, "_secret")
    assert credential != protocol.parse_refresh_credential(wire)
    with caplog.at_level(logging.DEBUG):
        protocol.generate_refresh_credential()
        protocol.parse_refresh_credential(wire)
        protocol.verify_refresh_credential(wire, record)
        protocol.verify_refresh_credential("invalid", record)
    assert caplog.records == []
    # Existing controlled logging must not interpolate arbitrary sensitive messages/args.
    event = logging.LogRecord("fleetlink", logging.INFO, "", 0, "%s", (credential,), None)
    assert bool(secret not in JsonFormatter().format(event))
    event = logging.LogRecord("fleetlink", logging.INFO, "", 0, wire, (), None)
    assert bool(secret not in JsonFormatter().format(event))


@pytest.mark.parametrize("secret", [None, "text", b"", bytes(31), bytes(33), bytearray(32)])
def test_invalid_secret_wrapper_has_fixed_error(secret: object) -> None:
    with pytest.raises(protocol.InvalidRefreshCredential, match="^Invalid refresh credential$"):
        protocol.RefreshCredential(IDENTIFIER, cast(bytes, secret))


def test_invalid_wrapper_uuid() -> None:
    with pytest.raises(protocol.InvalidRefreshCredential, match="^Invalid refresh credential$"):
        protocol.RefreshCredential(cast(UUID, "invalid"), bytes(32))


def test_generation_failure_has_safe_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(size: int) -> bytes:
        raise OSError("FAKE-ENTROPY-ERROR-MUST-NOT-LEAK")

    monkeypatch.setattr(secrets, "token_bytes", unavailable)
    with pytest.raises(protocol.RefreshCredentialGenerationError) as caught:
        protocol.generate_refresh_credential()
    assert str(caught.value) == "Refresh credential generation failed"
    assert "FAKE-ENTROPY" not in "".join(traceback.format_exception(caught.value))


def test_generation_has_no_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Generation must not access persistence")

    monkeypatch.setattr(Database, "__init__", forbidden)
    monkeypatch.setattr(SqlAlchemyRefreshTokenRepository, "__init__", forbidden)
    monkeypatch.setattr(SqlAlchemyRefreshTokenRepository, "add", forbidden)
    monkeypatch.setattr(SqlAlchemyRefreshTokenRepository, "rotate", forbidden)
    issued = protocol.generate_refresh_credential()
    assert isinstance(issued.verifier, RefreshVerifier)

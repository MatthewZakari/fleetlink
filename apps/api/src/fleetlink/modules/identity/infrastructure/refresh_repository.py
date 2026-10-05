"""Atomic token/session CAS under the existing explicit caller transaction."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fleetlink.modules.identity.application.refresh_ports import (
    RefreshRotation,
    RefreshTokenConflict,
    RefreshTokenNotFound,
)
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession
from fleetlink.modules.identity.domain.refresh_token import (
    InvalidRefreshRotation,
    RefreshTokenRecord,
    RefreshTokenStatus,
    RefreshVerifier,
    prepare_rotation,
)
from fleetlink.modules.identity.infrastructure.models import RefreshTokenRecord as Record
from fleetlink.modules.identity.infrastructure.session_repository import (
    SqlAlchemyAuthenticationSessionRepository,
)


class SqlAlchemyRefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshTokenRecord) -> None:
        if token.version != 0 or token.status is not RefreshTokenStatus.CURRENT:
            raise RefreshTokenConflict("New token must be current with version zero")
        self._session.add(
            Record(
                id=token.id,
                session_id=token.session_id,
                verifier=token.verifier.value,
                created_at=token.created_at,
                expires_at=token.expires_at,
                status=token.status.value,
                replaced_by_id=None,
                consumed_at=None,
                version=0,
            )
        )
        await self._session.flush()

    async def get(self, token_id: UUID) -> RefreshTokenRecord | None:
        record = await self._session.scalar(
            select(Record).where(Record.id == token_id).execution_options(populate_existing=True)
        )
        if record is None:
            return None
        return RefreshTokenRecord(
            id=record.id,
            session_id=record.session_id,
            verifier=RefreshVerifier(record.verifier),
            created_at=record.created_at,
            expires_at=record.expires_at,
            status=RefreshTokenStatus(record.status),
            replaced_by_id=record.replaced_by_id,
            consumed_at=record.consumed_at,
            version=record.version,
        )

    async def rotate(
        self,
        token: RefreshTokenRecord,
        replacement: RefreshTokenRecord,
        session: AuthenticationSession,
        *,
        at: datetime,
    ) -> RefreshRotation:
        try:
            consumed = prepare_rotation(token, replacement, session, at=at)
        except InvalidRefreshRotation:
            raise RefreshTokenConflict("Rotation snapshots are incompatible") from None
        # Session first: serializes rotation against revocation and other lineage writes.
        saved_session = await SqlAlchemyAuthenticationSessionRepository(self._session).save(session)
        changed = await self._session.scalar(
            update(Record)
            .where(
                Record.id == token.id,
                Record.version == token.version,
                Record.session_id == token.session_id,
                Record.verifier == token.verifier.value,
                Record.created_at == token.created_at,
                Record.expires_at == token.expires_at,
                Record.status == RefreshTokenStatus.CURRENT.value,
                Record.consumed_at.is_(None),
                Record.replaced_by_id.is_(None),
            )
            .values(
                status=consumed.status.value,
                consumed_at=consumed.consumed_at,
                replaced_by_id=consumed.replaced_by_id,
                version=Record.version + 1,
            )
            .returning(Record.id)
            .execution_options(synchronize_session=False)
        )
        if changed is None:
            if await self._session.scalar(select(Record.id).where(Record.id == token.id)) is None:
                raise RefreshTokenNotFound("Refresh token does not exist")
            raise RefreshTokenConflict("Refresh token snapshot conflicts with persisted state")
        # Deferred FK permits consume-before-insert; partial uniqueness forbids a second head.
        await self.add(replacement)
        return RefreshRotation(
            saved_session, replace(consumed, version=token.version + 1), replacement
        )

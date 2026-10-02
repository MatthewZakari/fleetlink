"""Caller-owned transactions; stable lineage and irreversible revocation."""

from dataclasses import replace
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fleetlink.modules.identity.application.session_ports import SessionConflict, SessionNotFound
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession, SessionStatus
from fleetlink.modules.identity.infrastructure.models import AuthenticationSessionRecord as Record


class SqlAlchemyAuthenticationSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: AuthenticationSession) -> None:
        if session.version != 0:
            raise SessionConflict("New session must have version zero")
        self._session.add(
            Record(
                id=session.id,
                user_id=session.user_id,
                family_id=session.family_id,
                created_at=session.created_at,
                expires_at=session.expires_at,
                status=session.status.value,
                version=session.version,
            )
        )
        await self._session.flush()

    async def get(self, session_id: UUID) -> AuthenticationSession | None:
        record = await self._session.scalar(
            select(Record).where(Record.id == session_id).execution_options(populate_existing=True)
        )
        return _snapshot(record) if record is not None else None

    async def get_by_family(self, family_id: UUID) -> AuthenticationSession | None:
        record = await self._session.scalar(
            select(Record)
            .where(Record.family_id == family_id)
            .execution_options(populate_existing=True)
        )
        return _snapshot(record) if record is not None else None

    async def save(self, session: AuthenticationSession) -> AuthenticationSession:
        changed = await self._session.scalar(
            update(Record)
            .where(
                Record.id == session.id,
                Record.version == session.version,
                Record.user_id == session.user_id,
                Record.family_id == session.family_id,
                Record.created_at == session.created_at,
                Record.expires_at == session.expires_at,
                or_(
                    Record.status == SessionStatus.ACTIVE.value,
                    Record.status == session.status.value,
                ),
            )
            .values(status=session.status.value, version=Record.version + 1)
            .returning(Record.id)
            .execution_options(synchronize_session=False)
        )
        if changed is None:
            if await self._session.scalar(select(Record.id).where(Record.id == session.id)) is None:
                raise SessionNotFound("Session does not exist")
            raise SessionConflict("Session snapshot conflicts with persisted state")
        return replace(session, version=session.version + 1)


def _snapshot(record: Record) -> AuthenticationSession:
    return AuthenticationSession(
        id=record.id,
        user_id=record.user_id,
        family_id=record.family_id,
        created_at=record.created_at,
        expires_at=record.expires_at,
        status=SessionStatus(record.status),
        version=record.version,
    )

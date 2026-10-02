"""SQLAlchemy adapter. Use only inside Database.session() and an explicit begin()."""

from dataclasses import replace
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fleetlink.modules.identity.application.ports import IdentityConflict, UserNotFound
from fleetlink.modules.identity.domain.user import AccountStatus, PlatformRole, User
from fleetlink.modules.identity.infrastructure.models import UserRecord, UserRoleRecord


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        if user.version != 0:
            raise IdentityConflict("New identity must have version zero")
        self._session.add(
            UserRecord(
                id=user.id,
                status=user.status.value,
                created_at=user.created_at,
                version=user.version,
            )
        )
        # No ORM relationship: flush the parent before its explicit role records.
        await self._session.flush()
        await self._insert_roles(user)

    async def get(self, user_id: UUID) -> User | None:
        rows = (
            await self._session.execute(
                select(UserRecord, UserRoleRecord.role)
                .outerjoin(UserRoleRecord, UserRoleRecord.user_id == UserRecord.id)
                .where(UserRecord.id == user_id)
                .execution_options(populate_existing=True)
            )
        ).all()
        if not rows:
            return None
        record = rows[0][0]
        return User(
            id=record.id,
            status=AccountStatus(record.status),
            created_at=record.created_at,
            version=record.version,
            roles=frozenset(PlatformRole(role) for _, role in rows if role is not None),
        )

    async def save(self, user: User) -> User:
        changed = await self._session.scalar(
            update(UserRecord)
            .where(
                UserRecord.id == user.id,
                UserRecord.version == user.version,
                UserRecord.created_at == user.created_at,
            )
            .values(status=user.status.value, version=UserRecord.version + 1)
            .returning(UserRecord.id)
            .execution_options(synchronize_session=False)
        )
        if changed is None:
            if (
                await self._session.scalar(select(UserRecord.id).where(UserRecord.id == user.id))
                is None
            ):
                raise UserNotFound("Identity does not exist")
            raise IdentityConflict("Identity snapshot conflicts with persisted state")
        await self._session.execute(delete(UserRoleRecord).where(UserRoleRecord.user_id == user.id))
        await self._insert_roles(user)
        return replace(user, version=user.version + 1)

    async def _insert_roles(self, user: User) -> None:
        self._session.add_all(
            UserRoleRecord(user_id=user.id, role=role.value) for role in sorted(user.roles)
        )
        await self._session.flush()

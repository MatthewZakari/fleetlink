"""Explicit Identity mappings on the shared, application-owned metadata registry."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fleetlink.infrastructure.database import metadata
from fleetlink.modules.identity.domain.user import AccountStatus, PlatformRole


class Base(DeclarativeBase):
    metadata = metadata


class UserRecord(Base):
    __tablename__ = "identity_users"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_identity_users"),
        CheckConstraint(
            "status IN (" + ", ".join(repr(value.value) for value in AccountStatus) + ")",
            name="ck_identity_users_status",
        ),
        CheckConstraint("version >= 0", name="ck_identity_users_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class UserRoleRecord(Base):
    __tablename__ = "identity_user_roles"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "role", name="pk_identity_user_roles"),
        CheckConstraint(
            "role IN (" + ", ".join(repr(value.value) for value in PlatformRole) + ")",
            name="ck_identity_user_roles_role",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("identity_users.id", name="fk_identity_user_roles_user", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(16), primary_key=True)

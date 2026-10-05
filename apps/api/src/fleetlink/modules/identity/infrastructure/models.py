"""Explicit Identity mappings on the shared, application-owned metadata registry."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    text,
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


class AuthenticationSessionRecord(Base):
    __tablename__ = "identity_auth_sessions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_identity_auth_sessions"),
        UniqueConstraint("family_id", name="uq_identity_auth_sessions_family"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_identity_auth_sessions_status"),
        CheckConstraint("version >= 0", name="ck_identity_auth_sessions_version"),
        CheckConstraint("expires_at > created_at", name="ck_identity_auth_sessions_expiry"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("identity_users.id", name="fk_identity_auth_sessions_user", ondelete="RESTRICT"),
        nullable=False,
    )
    family_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class RefreshTokenRecord(Base):
    __tablename__ = "identity_refresh_tokens"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_identity_refresh_tokens"),
        CheckConstraint(
            "status IN ('current', 'consumed')", name="ck_identity_refresh_tokens_status"
        ),
        CheckConstraint("version >= 0", name="ck_identity_refresh_tokens_version"),
        CheckConstraint("expires_at > created_at", name="ck_identity_refresh_tokens_expiry"),
        CheckConstraint(
            "octet_length(verifier) BETWEEN 1 AND 512", name="ck_identity_refresh_tokens_verifier"
        ),
        CheckConstraint("replaced_by_id <> id", name="ck_identity_refresh_tokens_replacement"),
        CheckConstraint(
            "(status = 'current' AND consumed_at IS NULL AND replaced_by_id IS NULL) OR "
            "(status = 'consumed' AND consumed_at IS NOT NULL AND replaced_by_id IS NOT NULL "
            "AND consumed_at >= created_at AND consumed_at < expires_at)",
            name="ck_identity_refresh_tokens_lifecycle",
        ),
        Index(
            "uq_identity_refresh_tokens_current",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "identity_auth_sessions.id",
            name="fk_identity_refresh_tokens_session",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    verifier: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "identity_refresh_tokens.id",
            name="fk_identity_refresh_tokens_replacement",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

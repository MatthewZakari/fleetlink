"""FL-010 stable session lineages; downgrade destroys session data only."""

import sqlalchemy as sa
from alembic import op

revision = "0003_auth_session_foundation"
down_revision = "0002_identity_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_identity_auth_sessions"),
        sa.UniqueConstraint("family_id", name="uq_identity_auth_sessions_family"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity_users.id"],
            name="fk_identity_auth_sessions_user",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_identity_auth_sessions_status"
        ),
        sa.CheckConstraint("version >= 0", name="ck_identity_auth_sessions_version"),
        sa.CheckConstraint("expires_at > created_at", name="ck_identity_auth_sessions_expiry"),
    )


def downgrade() -> None:
    op.drop_table("identity_auth_sessions")

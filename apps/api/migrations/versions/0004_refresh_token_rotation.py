"""FL-011 refresh evidence; downgrade destroys only refresh-token records."""

import sqlalchemy as sa
from alembic import op

# Fits Alembic's existing VARCHAR(32) revision column.
revision = "0004_refresh_token_rotation"
down_revision = "0003_auth_session_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("verifier", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_identity_refresh_tokens"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["identity_auth_sessions.id"],
            name="fk_identity_refresh_tokens_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["identity_refresh_tokens.id"],
            name="fk_identity_refresh_tokens_replacement",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "status IN ('current', 'consumed')", name="ck_identity_refresh_tokens_status"
        ),
        sa.CheckConstraint("version >= 0", name="ck_identity_refresh_tokens_version"),
        sa.CheckConstraint("expires_at > created_at", name="ck_identity_refresh_tokens_expiry"),
        sa.CheckConstraint(
            "octet_length(verifier) BETWEEN 1 AND 512", name="ck_identity_refresh_tokens_verifier"
        ),
        sa.CheckConstraint("replaced_by_id <> id", name="ck_identity_refresh_tokens_replacement"),
        sa.CheckConstraint(
            "(status = 'current' AND consumed_at IS NULL AND replaced_by_id IS NULL) OR "
            "(status = 'consumed' AND consumed_at IS NOT NULL AND replaced_by_id IS NOT NULL "
            "AND consumed_at >= created_at AND consumed_at < expires_at)",
            name="ck_identity_refresh_tokens_lifecycle",
        ),
    )
    op.create_index(
        "uq_identity_refresh_tokens_current",
        "identity_refresh_tokens",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )


def downgrade() -> None:
    op.drop_table("identity_refresh_tokens")

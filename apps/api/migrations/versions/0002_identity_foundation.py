"""FL-009 Identity foundation; downgrade destroys Identity data only."""

import sqlalchemy as sa
from alembic import op

revision = "0002_identity_foundation"
down_revision = "0001_technical_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_identity_users"),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'disabled')", name="ck_identity_users_status"
        ),
        sa.CheckConstraint("version >= 0", name="ck_identity_users_version"),
    )
    op.create_table(
        "identity_user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "role", name="pk_identity_user_roles"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity_users.id"],
            name="fk_identity_user_roles_user",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('customer', 'merchant', 'rider', 'administrator')",
            name="ck_identity_user_roles_role",
        ),
    )


def downgrade() -> None:
    op.drop_table("identity_user_roles")
    op.drop_table("identity_users")

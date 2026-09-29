"""Technical baseline: require infrastructure-provisioned PostGIS, no domain tables."""

from alembic import context, op
from alembic.util import CommandError
from sqlalchemy import text

revision = "0001_technical_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not context.is_offline_mode():
        available = op.get_bind().scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis')")
        )
        if not available:
            raise CommandError(
                "PostGIS prerequisite missing: ask database administrator to provision it"
            )
        return
    # Works online and in offline SQL; validation occurs when SQL is executed.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
                RAISE EXCEPTION 'PostGIS missing: ask database administrator to provision it';
            END IF;
        END $$
    """)


def downgrade() -> None:
    """Only Alembic revision tracking changes; preserve all extensions and data."""

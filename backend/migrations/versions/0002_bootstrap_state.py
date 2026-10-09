"""Remember installation bootstrap without recreating deleted registry entries."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bootstrap_state",
        sa.Column("name", sa.String(50), primary_key=True),
        sa.Column("initialized_at", sa.DateTime(), nullable=False),
    )
    # An installation with users has already bootstrapped. Its missing upstreams
    # may be intentional deletions and must not be silently restored on upgrade.
    op.execute(
        sa.text(
            "INSERT INTO bootstrap_state (name, initialized_at) "
            "SELECT 'defaults', CURRENT_TIMESTAMP WHERE EXISTS (SELECT 1 FROM users)"
        )
    )


def downgrade():
    op.drop_table("bootstrap_state")

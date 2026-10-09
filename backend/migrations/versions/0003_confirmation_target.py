"""Bind pending write approvals to the connection and tool that were reviewed."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("confirmations", sa.Column("target_hash", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("confirmations", "target_hash")

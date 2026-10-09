"""Initial commerce gateway schema.

Revision ID: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
    )
    op.create_table(
        "invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime()),
    )
    op.create_table(
        "upstreams",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(32), nullable=False, unique=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auth_type", sa.String(16), nullable=False),
        sa.Column("header_name", sa.String(100)),
        sa.Column("credential_key", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_checked_at", sa.DateTime()),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("user_name", sa.String(100), nullable=False),
        sa.Column("user_email", sa.String(254), nullable=False),
        sa.Column("server_id", sa.String(36), index=True),
        sa.Column("server_name", sa.String(100), nullable=False),
        sa.Column("tool_name", sa.String(150), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("request_id", sa.String(100), nullable=False),
    )
    op.create_table(
        "confirmations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("login_sessions.id"), nullable=False),
        sa.Column("tool_name", sa.String(150), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
    )


def downgrade():
    for table in (
        "confirmations",
        "audit",
        "upstreams",
        "invites",
        "api_keys",
        "login_sessions",
        "users",
    ):
        op.drop_table(table)

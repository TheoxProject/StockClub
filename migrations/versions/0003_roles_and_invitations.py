"""Add user roles and one-use invitations.

Revision ID: 0003_roles_invitations
Revises: 0002_logins_pin_unit
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_roles_invitations"
down_revision = "0002_logins_pin_unit"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=20), server_default="member", nullable=False),
    )
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    # The oldest existing account becomes the initial administrator. Remaining
    # existing accounts keep the default member role.
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE users SET role = 'admin' WHERE id = (SELECT MIN(id) FROM users)"
    ))

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invitations_token_hash", "invitations", ["token_hash"], unique=True)
    op.create_index("ix_invitations_created_by_id", "invitations", ["created_by_id"], unique=False)
    op.create_index("ix_invitations_expires_at", "invitations", ["expires_at"], unique=False)


def downgrade():
    op.drop_index("ix_invitations_expires_at", table_name="invitations")
    op.drop_index("ix_invitations_created_by_id", table_name="invitations")
    op.drop_index("ix_invitations_token_hash", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "role")

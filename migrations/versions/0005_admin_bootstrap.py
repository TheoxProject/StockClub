"""Add singleton lock for safe first-administrator initialization.

Revision ID: 0005_admin_bootstrap
Revises: 0004_live_search_loans
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_admin_bootstrap"
down_revision = "0004_live_search_loans"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bootstrap_locks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("id = 1", name="ck_bootstrap_locks_singleton_id"),
    )
    locks = sa.table(
        "bootstrap_locks",
        sa.column("id", sa.Integer()),
        sa.column("lock_version", sa.Integer()),
    )
    op.bulk_insert(locks, [{"id": 1, "lock_version": 0}])


def downgrade():
    op.drop_table("bootstrap_locks")

"""Replace email login with username, add PIN lockout and fix inventory unit.

Revision ID: 0002_logins_pin_unit
Revises: 0001_initial
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa
import re

revision = "0002_logins_pin_unit"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the old non-unique lookup index before the column rename.
    op.drop_index("ix_users_email", table_name="users")

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "email",
            new_column_name="login",
            existing_type=sa.String(length=254),
            type_=sa.String(length=80),
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default="0"
        ))
        batch_op.add_column(sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))

    op.create_index("ix_users_login", "users", ["login"], unique=False)

    # Existing accounts used email addresses. Convert them to a practical login
    # using the local part (before @), resolving duplicates deterministically.
    connection = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Integer),
        sa.column("login", sa.String),
    )
    rows = connection.execute(sa.select(users.c.id, users.c.login).order_by(users.c.id)).all()
    if rows:
        originals = [(row.id, row.login or "") for row in rows]
        # Temporarily move every value away from the target namespace so unique
        # constraints never collide while rows are being normalized.
        for user_id, _ in originals:
            connection.execute(users.update().where(users.c.id == user_id).values(login=f"migrating-user-{user_id}"))

        used = set()
        for user_id, old_login in originals:
            candidate = old_login.split("@", 1)[0].strip().casefold()
            candidate = re.sub(r"\s+", "-", candidate)[:80].strip("-.")
            if not candidate:
                candidate = f"benevole-{user_id}"
            base = candidate
            suffix = 2
            while candidate in used:
                ending = f"-{suffix}"
                candidate = base[: 80 - len(ending)] + ending
                suffix += 1
            used.add(candidate)
            connection.execute(users.update().where(users.c.id == user_id).values(login=candidate))

    # The application now uses individual units only. Normalize old records.
    connection.execute(sa.text("UPDATE items SET unit = 'unité'"))


def downgrade():
    # Restore an email-named column, but cannot reconstruct the original full
    # email values because upgrade intentionally reduced them to login names.
    op.drop_index("ix_users_login", table_name="users")
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE users SET login = login || '@migrated.invalid'"))
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_attempts")
        batch_op.alter_column(
            "login",
            new_column_name="email",
            existing_type=sa.String(length=80),
            type_=sa.String(length=254),
            existing_nullable=False,
        )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

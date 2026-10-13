"""Track individual loans/returns and preserve movement history after item deletion.

Revision ID: 0004_live_search_loans
Revises: 0003_roles_invitations
Create Date: 2026-10-13
"""
from collections import defaultdict
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0004_live_search_loans"
down_revision = "0003_roles_invitations"
branch_labels = None
depends_on = None


def _as_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def upgrade():
    # Keep the item's former name when a permanent admin deletion detaches history.
    op.add_column("stock_movements", sa.Column("item_name_snapshot", sa.String(length=160), nullable=True))
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE stock_movements SET item_name_snapshot = "
        "(SELECT items.name FROM items WHERE items.id = stock_movements.item_id) "
        "WHERE item_name_snapshot IS NULL"
    ))

    # Recreate the FK with SET NULL and make the snapshot required. Naming convention
    # lets Alembic also handle SQLite's commonly unnamed reflected foreign keys.
    inspector = sa.inspect(connection)
    old_fk = next(
        (fk for fk in inspector.get_foreign_keys("stock_movements") if fk.get("constrained_columns") == ["item_id"]),
        None,
    )
    fk_name = old_fk.get("name") if old_fk else None
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table("stock_movements", naming_convention=naming) as batch_op:
        batch_op.drop_constraint(fk_name or "fk_stock_movements_item_id_items", type_="foreignkey")
        batch_op.alter_column("item_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("item_name_snapshot", existing_type=sa.String(length=160), nullable=False)
        batch_op.create_foreign_key(
            "fk_stock_movements_item_id_items", "items", ["item_id"], ["id"], ondelete="SET NULL"
        )

    op.create_table(
        "borrowings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("item_name_snapshot", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("quantity_out", sa.Integer(), nullable=False),
        sa.Column("quantity_returned", sa.Integer(), server_default="0", nullable=False),
        sa.Column("note", sa.String(length=240), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_borrowings_item_id", "borrowings", ["item_id"], unique=False)
    op.create_index("ix_borrowings_user_id", "borrowings", ["user_id"], unique=False)
    op.create_index("ix_borrowings_created_at", "borrowings", ["created_at"], unique=False)

    op.create_table(
        "borrowing_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("borrowing_id", sa.Integer(), nullable=True),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("item_name_snapshot", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=240), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["borrowing_id"], ["borrowings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_borrowing_events_borrowing_id", "borrowing_events", ["borrowing_id"], unique=False)
    op.create_index("ix_borrowing_events_item_id", "borrowing_events", ["item_id"], unique=False)
    op.create_index("ix_borrowing_events_user_id", "borrowing_events", ["user_id"], unique=False)
    op.create_index("ix_borrowing_events_created_at", "borrowing_events", ["created_at"], unique=False)

    # Best-effort migration of old movement records. Older versions kept only a
    # signed movement ledger, so they cannot identify an exact open loan per person.
    # Create one aggregate historical loan per item and preserve known event dates/users.
    movement_rows = connection.execute(sa.text(
        "SELECT id, item_id, user_id, delta, note, created_at "
        "FROM stock_movements WHERE item_id IS NOT NULL ORDER BY created_at, id"
    )).mappings().all()
    by_item = defaultdict(list)
    for row in movement_rows:
        note = (row["note"] or "").strip()
        lowered = note.casefold()
        delta = int(row["delta"] or 0)
        if lowered.startswith("emprunt") and delta < 0:
            by_item[row["item_id"]].append((row, "sortie", -delta))
        elif lowered.startswith("retour") and delta > 0:
            by_item[row["item_id"]].append((row, "retour", delta))

    borrowings_table = sa.Table("borrowings", sa.MetaData(), autoload_with=connection)
    events_table = sa.Table("borrowing_events", sa.MetaData(), autoload_with=connection)
    item_names = dict(connection.execute(sa.text("SELECT id, name FROM items")).all())
    for item_id, records in by_item.items():
        outgoing = sum(quantity for _, event_type, quantity in records if event_type == "sortie")
        returned = sum(quantity for _, event_type, quantity in records if event_type == "retour")
        if outgoing <= 0:
            continue
        returned_for_total = min(outgoing, returned)
        first_out = _as_datetime(next(row["created_at"] for row, event_type, _ in records if event_type == "sortie"))
        last_return = _as_datetime(next((row["created_at"] for row, event_type, _ in reversed(records) if event_type == "retour"), None))
        closed_at = last_return if returned_for_total >= outgoing else None
        name = item_names.get(item_id, "Objet supprimé")
        result = connection.execute(borrowings_table.insert().values(
            item_id=item_id,
            item_name_snapshot=name,
            user_id=None,
            quantity_out=outgoing,
            quantity_returned=returned_for_total,
            note="Emprunts antérieurs à la version 1.4 ; responsable exact non reconstituable.",
            created_at=first_out,
            closed_at=closed_at,
        ))
        borrowing_id = result.inserted_primary_key[0]
        for row, event_type, quantity in records:
            connection.execute(events_table.insert().values(
                borrowing_id=borrowing_id,
                item_id=item_id,
                item_name_snapshot=name,
                user_id=row["user_id"],
                event_type=event_type,
                quantity=quantity,
                note=(row["note"] or "")[:240],
                created_at=_as_datetime(row["created_at"]),
            ))


def downgrade():
    # The upgrade intentionally preserves data. Downgrading would require deleting
    # the new loan ledger and restoring a cascading FK, so only schema objects added
    # here are removed; never use this downgrade as a data-recovery mechanism.
    op.drop_index("ix_borrowing_events_created_at", table_name="borrowing_events")
    op.drop_index("ix_borrowing_events_user_id", table_name="borrowing_events")
    op.drop_index("ix_borrowing_events_item_id", table_name="borrowing_events")
    op.drop_index("ix_borrowing_events_borrowing_id", table_name="borrowing_events")
    op.drop_table("borrowing_events")
    op.drop_index("ix_borrowings_created_at", table_name="borrowings")
    op.drop_index("ix_borrowings_user_id", table_name="borrowings")
    op.drop_index("ix_borrowings_item_id", table_name="borrowings")
    op.drop_table("borrowings")

    inspector = sa.inspect(op.get_bind())
    fk = next(
        (value for value in inspector.get_foreign_keys("stock_movements") if value.get("constrained_columns") == ["item_id"]),
        None,
    )
    fk_name = fk.get("name") if fk else None
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table("stock_movements", naming_convention=naming) as batch_op:
        batch_op.drop_constraint(fk_name or "fk_stock_movements_item_id_items", type_="foreignkey")
        batch_op.alter_column("item_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("item_name_snapshot")
        batch_op.create_foreign_key(
            "fk_stock_movements_item_id_items", "items", ["item_id"], ["id"], ondelete="CASCADE"
        )

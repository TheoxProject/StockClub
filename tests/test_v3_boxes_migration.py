"""Real populated 0006-era upgrade, preserving partial returns and losses."""
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from flask_migrate import downgrade, upgrade

from app.extensions import db
from app.models import Borrowing, BoxContent, BoxEvent, Item, LoanGroup, StockMovement, StorageBox
from test_v3_stock_migration import migration_app, old_insert
from test_reliability_concurrency import authenticated


def legacy_snapshot():
    result = {}
    for table in ("items", "borrowings", "borrowing_events", "stock_movements"):
        columns = [c["name"] for c in sa.inspect(db.engine).get_columns(table)
                   if c["name"] not in {"box_id", "box_name_snapshot", "group_id", "is_consumable", "consumption_threshold", "quantity_consumed"}]
        result[table] = [tuple(row) for row in db.session.execute(sa.text(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"))]
    return result


@pytest.mark.parametrize("populated", [False, True])
def test_boxes_upgrade_real_0006_and_downgrade_guards(migration_app, populated):
    app = migration_app
    with app.app_context():
        upgrade(revision="0006_stock_adjustments")
        now = datetime(2026, 2, 3, 12, tzinfo=timezone.utc)
        if populated:
            with db.engine.begin() as conn:
                old_insert(conn, "users", id=1, name="Fictitious migration", login="migration", password_hash="fictitious", is_active_user=True, role="admin", failed_login_attempts=0, created_at=now)
                old_insert(conn, "categories", id=1, name="Fictitious migration", created_at=now)
                old_insert(conn, "items", id=1, name="Legacy cone", description="", category_id=1, quantity=7, unit="unit", location="", condition="OK", photo_key="items/fictitious.webp", created_at=now, updated_at=now)
                old_insert(conn, "borrowings", id=1, item_id=1, item_name_snapshot="Legacy cone", user_id=1, quantity_out=5, quantity_returned=2, quantity_lost=1, note="Partial return and loss", created_at=now)
                for ident, kind, qty, delta, total_delta, after in [(1, "sortie", 5, -5, 0, 5), (2, "retour", 2, 2, 0, 7), (3, "casse", 1, 0, -1, 7)]:
                    old_insert(conn, "borrowing_events", id=ident, borrowing_id=1, item_id=1, item_name_snapshot="Legacy cone", user_id=1, event_type=kind, quantity=qty, note="Legacy event", created_at=now)
                    old_insert(conn, "stock_movements", id=ident, item_id=1, item_name_snapshot="Legacy cone", user_id=1, borrowing_id=1, movement_type=kind, reason="casse" if ident == 3 else "", total_delta=total_delta, delta=delta, quantity_after=after, note="Legacy movement", created_at=now)
                if conn.dialect.name == "postgresql":
                    for table in ("users", "categories", "items", "borrowings", "borrowing_events", "stock_movements"):
                        conn.execute(sa.text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))"))
        before = legacy_snapshot()
        db.session.remove()
        upgrade(revision="head")
        assert legacy_snapshot() == before
        assert StorageBox.query.count() == BoxContent.query.count() == BoxEvent.query.count() == LoanGroup.query.count() == 0
        assert all(l.box_id is None and l.group_id is None and l.box_name_snapshot == "" for l in Borrowing.query.all())
        assert all(m.box_id is None and m.box_name_snapshot == "" for m in StockMovement.query.all())
        if populated:
            assert db.session.get(Item, 1).total_quantity == 9
            assert db.session.get(Borrowing, 1).remaining_quantity == 2
        db.session.remove()
        # Legacy V3 movements do not themselves prevent return to the previous V3 schema.
        downgrade(revision="0006_stock_adjustments")
        assert "box_id" not in {c["name"] for c in sa.inspect(db.engine).get_columns("borrowings")}
        assert legacy_snapshot() == before
        db.session.remove()
        upgrade(revision="head")
    if populated:
        assert authenticated(app).post("/loans/1/return", data={"quantity": "2"}).status_code == 302
        assert authenticated(app).post("/boxes/new", data={"name": "Migration box"}).status_code == 302
        with app.app_context():
            loan = db.session.get(Borrowing, 1)
            assert (loan.quantity_returned, loan.quantity_lost, loan.remaining_quantity) == (4, 1, 0)
            assert loan.box_id is None and db.session.get(Item, 1).quantity == 9
            box_id = StorageBox.query.one().id
        assert authenticated(app).post(f"/boxes/{box_id}/delete").status_code == 302
        with app.app_context():
            db.session.remove()
            with pytest.raises(RuntimeError, match="historique|boîtes|boites|provenance"):
                command.downgrade(app.extensions["migrate"].migrate.get_config(), "0006_stock_adjustments")
            db.session.remove()
            assert StorageBox.query.count() == 1
            # The later migration may already be downgraded when 0007's guard
            # refuses rollback. Read the historical column without the new ORM.
            assert db.session.execute(sa.text("SELECT quantity_lost FROM borrowings WHERE id = 1")).scalar_one() == 1

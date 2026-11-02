"""Real blank and populated 0008 upgrades with unchanged historical values."""
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from flask_migrate import downgrade, upgrade

from app.extensions import db
from test_v3_stock_migration import migration_app, old_insert


def snapshot():
    tables = ("users", "categories", "items", "storage_boxes", "box_contents", "box_events",
              "borrowings", "borrowing_events", "stock_movements", "box_fill_receipts")
    result = {}
    for name in tables:
        rows = db.session.execute(sa.text(f"SELECT * FROM {name} ORDER BY id")).mappings()
        result[name] = [{k: v for k, v in row.items() if k not in {"is_consumable", "consumption_threshold", "quantity_consumed"}} for row in rows]
    return result


@pytest.mark.parametrize("populated", [False, True])
def test_upgrade_blank_and_populated_0008_preserves_every_legacy_value(migration_app, populated):
    with migration_app.app_context():
        upgrade(revision="0008_box_fill_receipts")
        now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        if populated:
            with db.engine.begin() as conn:
                old_insert(conn, "users", id=1, name="Fictitious admin", login="migration", password_hash="fictitious", role="admin", is_active_user=True, failed_login_attempts=0, created_at=now)
                old_insert(conn, "categories", id=1, name="Fictitious category", created_at=now)
                old_insert(conn, "items", id=1, name="Fictitious rolls", description="", category_id=1, quantity=7, unit="rouleaux", location="", condition="Bon état", photo_key="", created_at=now, updated_at=now)
                old_insert(conn, "storage_boxes", id=1, name="Migration box", name_key="migration box", description="", is_active=True, created_at=now)
                old_insert(conn, "box_contents", id=1, box_id=1, item_id=1, present_quantity=4)
                old_insert(conn, "box_events", id=1, item_id=1, item_name_snapshot="Fictitious rolls", quantity=4, source_box_id=None, destination_box_id=1, source_name_snapshot="Hors boîte", destination_name_snapshot="Migration box", user_id=1, created_at=now)
                old_insert(conn, "borrowings", id=1, item_id=1, item_name_snapshot="Fictitious rolls", user_id=1, quantity_out=5, quantity_returned=1, quantity_lost=1, box_id=1, box_name_snapshot="Migration box", note="Partially returned and lost", created_at=now)
                for ident, kind, qty, delta, total_delta, after in [(1,"sortie",5,-5,0,5),(2,"retour",1,1,0,6),(3,"perte",1,0,-1,6)]:
                    old_insert(conn, "borrowing_events", id=ident, borrowing_id=1, item_id=1, item_name_snapshot="Fictitious rolls", user_id=1, event_type=kind, quantity=qty, note="Fictitious historical event", created_at=now)
                    old_insert(conn, "stock_movements", id=ident, item_id=1, item_name_snapshot="Fictitious rolls", user_id=1, delta=delta, total_delta=total_delta, quantity_after=after, movement_type=kind, reason="", note="Fictitious historical movement", created_at=now)
        before = snapshot()
        db.session.remove()
        upgrade(revision="head")

        assert snapshot() == before
        assert db.session.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0009_consumables"
        assert db.session.execute(sa.text("SELECT COUNT(*) FROM consumptions")).scalar_one() == 0
        assert db.session.execute(sa.text("SELECT COUNT(*) FROM consumable_receipts")).scalar_one() == 0
        if populated:
            row = db.session.execute(sa.text("SELECT is_consumable, consumption_threshold FROM items WHERE id=1")).one()
            assert not row.is_consumable and row.consumption_threshold is None
            assert db.session.execute(sa.text("SELECT quantity_consumed FROM borrowings WHERE id=1")).scalar_one() == 0
        db.session.remove()
        downgrade(revision="0008_box_fill_receipts")
        assert snapshot() == before
        assert "consumptions" not in sa.inspect(db.engine).get_table_names()
        db.session.remove()
        upgrade(revision="head")


@pytest.mark.parametrize("guard", ["parameters", "history"])
def test_downgrade_refuses_consumable_configuration_or_history(migration_app, guard):
    with migration_app.app_context():
        upgrade(revision="head")
        now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        with db.engine.begin() as conn:
            old_insert(conn, "categories", id=1, name="Guard fixtures", created_at=now)
            old_insert(conn, "items", id=1, name="Guard item", description="", category_id=1, quantity=1, unit="unité", location="", condition="Bon état", photo_key="", created_at=now, updated_at=now, is_consumable=guard == "parameters")
            if guard == "history":
                old_insert(conn, "consumptions", id=1, kind="direct", item_id=1, item_name_snapshot="Guard item", box_name_snapshot="", quantity=1, note="Fictitious history", created_at=now, restored_outside=False)
        db.session.remove()
        with pytest.raises(RuntimeError, match="Retour impossible"):
            command.downgrade(migration_app.extensions["migrate"].migrate.get_config(), "0008_box_fill_receipts")
        db.session.remove()
        assert db.session.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0009_consumables"

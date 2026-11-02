"""Real populated 0007 upgrade and idempotency-receipt downgrade protection."""
from datetime import datetime, timezone
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from flask_migrate import downgrade, upgrade

from app.extensions import db
from test_v3_stock_migration import migration_app, old_insert


def snapshot():
    # PostgreSQL rolls back the entire transactional downgrade when 0008's
    # receipt guard refuses it, so newer columns may still exist. Compare every
    # historical value by name rather than including later schema additions.
    later_columns = {"is_consumable", "consumption_threshold", "quantity_consumed"}
    return {name: [{key: value for key, value in row.items() if key not in later_columns}
                   for row in db.session.execute(sa.text(f"SELECT * FROM {name} ORDER BY id")).mappings()]
            for name in ("users", "items", "storage_boxes", "box_contents", "box_events", "borrowings", "stock_movements")}


def test_populated_0007_upgrade_preserves_stock_history_and_receipt_guard(migration_app):
    with migration_app.app_context():
        upgrade(revision="0007_stock_boxes")
        now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        with db.engine.begin() as conn:
            old_insert(conn, "users", id=1, name="Fictitious migration admin", login="migration", password_hash="fictitious", role="admin", is_active_user=True, failed_login_attempts=0, created_at=now)
            old_insert(conn, "categories", id=1, name="Migration category", created_at=now)
            old_insert(conn, "items", id=1, name="Migration cones", description="", category_id=1, quantity=7, unit="unit", location="", condition="OK", photo_key="", created_at=now, updated_at=now)
            old_insert(conn, "storage_boxes", id=1, name="Migration box", name_key="migration box", description="", is_active=True, created_at=now)
            old_insert(conn, "box_contents", id=1, box_id=1, item_id=1, present_quantity=4)
            old_insert(conn, "box_events", id=1, item_id=1, item_name_snapshot="Migration cones", quantity=4, source_box_id=None, destination_box_id=1, source_name_snapshot="Outside", destination_name_snapshot="Migration box", user_id=1, created_at=now)
            old_insert(conn, "borrowings", id=1, item_id=1, item_name_snapshot="Migration cones", user_id=1, quantity_out=4, quantity_returned=1, quantity_lost=1, box_id=1, box_name_snapshot="Migration box", note="Partially returned/lost", created_at=now)
        before = snapshot()
        db.session.remove()
        upgrade(revision="0008_box_fill_receipts")
        assert snapshot() == before
        assert db.session.execute(sa.text("SELECT COUNT(*) FROM box_fill_receipts")).scalar_one() == 0
        assert db.session.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0008_box_fill_receipts"
        db.session.remove()
        downgrade(revision="0007_stock_boxes")
        assert "box_fill_receipts" not in sa.inspect(db.engine).get_table_names()
        assert snapshot() == before
        db.session.remove()
        upgrade(revision="head")
        with db.engine.begin() as conn:
            old_insert(conn, "box_fill_receipts", id=str(uuid.uuid4()), user_id=1, box_id=1, payload_hash="0" * 64, reference_count=1, unit_count=4, created_at=now)
        db.session.remove()
        with pytest.raises(RuntimeError, match="doublons"):
            command.downgrade(migration_app.extensions["migrate"].migrate.get_config(), "0007_stock_boxes")
        db.session.remove()
        assert db.session.execute(sa.text("SELECT COUNT(*) FROM box_fill_receipts")).scalar_one() == 1
        assert snapshot() == before

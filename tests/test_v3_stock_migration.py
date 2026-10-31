"""Exercise real 0005 -> 0006 migration with SQL-era fictitious records."""
from datetime import datetime, timezone
import pytest
import sqlalchemy as sa
from flask_migrate import upgrade, downgrade
from alembic import command
from app.extensions import db
from app.models import Borrowing, Item, StockMovement
from test_reliability_concurrency import authenticated


@pytest.fixture()
def migration_app(app):
    with app.app_context():
        db.session.remove()
        db.drop_all()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            with db.engine.begin() as conn:
                conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))


def old_insert(connection, table_name, **values):
    table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
    connection.execute(table.insert().values(**values))


def sql_snapshot():
    return {table: [dict(row) for row in db.session.execute(sa.text(query)).mappings()]
        for table, query in {
            "items": "SELECT id, name, quantity FROM items ORDER BY id",
            "loans": "SELECT id, quantity_out, quantity_returned, note, created_at, closed_at FROM borrowings ORDER BY id",
            "events": "SELECT id, borrowing_id, event_type, quantity, user_id, note, created_at FROM borrowing_events ORDER BY id",
            "movements": "SELECT id, delta, quantity_after, user_id, note, created_at FROM stock_movements ORDER BY id",
        }.items()}


@pytest.mark.parametrize("populated", [False, True])
def test_migration_fresh_and_populated_0005_preserves_legacy_and_enables_operations(migration_app, populated):
    app = migration_app
    with app.app_context():
        upgrade(revision="0005_admin_bootstrap")
        now = datetime(2026, 1, 2, 12, 30, tzinfo=timezone.utc)
        if populated:
            with db.engine.begin() as conn:
                old_insert(conn, "users", id=1, name="Fictitious migration admin", login="migration-admin", password_hash="fictitious", is_active_user=True, role="admin", failed_login_attempts=0, created_at=now)
                old_insert(conn, "categories", id=1, name="Fictitious migration", created_at=now)
                old_insert(conn, "items", id=1, name="Historical fictitious item", description="", category_id=1, quantity=7, unit="unit", location="", condition="OK", photo_key="items/fictitious.webp", created_at=now, updated_at=now)
                old_insert(conn, "borrowings", id=1, item_id=1, item_name_snapshot="Historical fictitious item", user_id=1, quantity_out=5, quantity_returned=2, note="Historical loan", created_at=now)
                for ident, kind, qty, delta, after in [(1, "sortie", 5, -5, 5), (2, "retour", 2, 2, 7)]:
                    old_insert(conn, "borrowing_events", id=ident, borrowing_id=1, item_id=1, item_name_snapshot="Historical fictitious item", user_id=1, event_type=kind, quantity=qty, note="Historical event", created_at=now)
                    old_insert(conn, "stock_movements", id=ident, item_id=1, item_name_snapshot="Historical fictitious item", user_id=1, delta=delta, quantity_after=after, note="Historical movement", created_at=now)
                # Explicit historical IDs must not collide with later PostgreSQL sequences.
                if conn.dialect.name == "postgresql":
                    for table in ("users", "categories", "items", "borrowings", "borrowing_events", "stock_movements"):
                        conn.execute(sa.text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))"))
        before = sql_snapshot()
        db.session.remove()
        upgrade(revision="head")
        assert sql_snapshot() == before
        assert db.session.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0006_stock_adjustments"
        if populated:
            loan = db.session.get(Borrowing, 1)
            assert loan.quantity_lost == 0 and loan.remaining_quantity == 3
            assert db.session.get(Item, 1).total_quantity == 10
            assert all(m.movement_type == "legacy" and m.total_delta is None and m.reason == "" and m.borrowing_id is None for m in StockMovement.query.all())
        db.session.remove()
    if populated:
        client = authenticated(app)
        assert client.post("/items/1/stock-adjustment", data=dict(operation="add", quantity="1", reason="achat")).status_code == 302
        assert client.post("/loans/1/write-off", data=dict(quantity="1", reason="casse")).status_code == 302
        assert client.post("/loans/1/return", data=dict(quantity="2")).status_code == 302
        with app.app_context():
            assert db.session.get(Item, 1).quantity == 10
            loan = db.session.get(Borrowing, 1)
            assert (loan.quantity_out, loan.quantity_returned, loan.quantity_lost, loan.remaining_quantity) == (5, 4, 1, 0)
            assert loan.closed_at is not None
            original = StockMovement.query.filter(StockMovement.id <= 2).all()
            assert [(m.delta, m.quantity_after, m.note) for m in original] == [(-5, 5, "Historical movement"), (2, 7, "Historical movement")]
            db.session.remove()
            with pytest.raises(RuntimeError, match="historique"):
                command.downgrade(app.extensions["migrate"].migrate.get_config(), "0005_admin_bootstrap")
            db.session.remove()
            assert db.session.get(Borrowing, 1).quantity_lost == 1
            assert StockMovement.query.count() == 5
    else:
        with app.app_context():
            downgrade(revision="0005_admin_bootstrap")
            assert "quantity_lost" not in {c["name"] for c in sa.inspect(db.engine).get_columns("borrowings")}
            upgrade(revision="head")

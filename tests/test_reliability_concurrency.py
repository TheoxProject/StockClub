"""Real requests, independent scoped sessions and connections; no production data."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Event, current_thread
from time import monotonic

import pytest
from PIL import Image
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import Borrowing, BorrowingEvent, Category, Item, StockMovement, User


def authenticated(app, user_id=1):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=str(user_id), _fresh=True)
    return client


def seed(app, quantity=6):
    with app.app_context():
        user = db.session.get(User, 1)
        user.role = "admin"
        category = Category(name="Fictitious concurrency")
        item = Item(name="Fictitious item", category=category, quantity=quantity,
                    photo_key="items/fictitious.webp")
        db.session.add(item)
        db.session.commit()
        return item.id


def outing(item_id, amount=1):
    return f"/items/{item_id}/adjust-stock", {"quantity": str(amount)}


def returning(loan_id):
    return f"/loans/{loan_id}/return", {"quantity": "1"}


def pair(app, first, second, users=(1, 1)):
    """Hold op1 after its SQL flush, observe op2 reach lock, then release commit.

    Synchronizing after both locked reads would manufacture a deadlock. Instead,
    op2 is observed immediately before its lock SQL and cannot finish while op1
    holds the transaction. The two DBAPI connections must be distinct.
    """
    held, release, attempted, completed = Event(), Event(), Event(), Event()
    connections, sessions = {}, {}
    with app.app_context():
        engine = db.engine

    def sql(conn, cursor, statement, parameters, context, many):
        name = current_thread().name
        if name in {"reliability-first", "reliability-second"} and statement.lstrip().upper().startswith("UPDATE BOOTSTRAP_LOCKS"):
            connections.setdefault(name, conn.connection.driver_connection)
            if name == "reliability-second":
                attempted.set()

    def commit(session):
        name = current_thread().name
        sessions.setdefault(name, session)
        if name == "reliability-first" and not held.is_set():
            session.flush()
            held.set()
            assert release.wait(15), "test coordinator did not release first transaction"

    def worker(index, operation):
        current_thread().name = "reliability-first" if index == 0 else "reliability-second"
        try:
            return authenticated(app, users[index]).post(operation[0], data=operation[1]).status_code
        finally:
            if index == 1:
                completed.set()

    event.listen(engine, "before_cursor_execute", sql)
    event.listen(Session, "before_commit", commit)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(worker, 0, first)
            try:
                assert held.wait(10), "first operation never reached commit"
                b = pool.submit(worker, 1, second)
                assert attempted.wait(10), "second mutation did not acquire common SQL lock"
                assert not completed.wait(.25), "second mutation finished before first commit"
                assert connections["reliability-first"] is not connections["reliability-second"]
            finally:
                release.set()
            results = [a.result(15), b.result(15)]
        assert all(code in {302, 403, 404, 409} for code in results), results
        if "reliability-second" in sessions:
            assert sessions["reliability-first"] is not sessions["reliability-second"]
        return results
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", sql)
        event.remove(Session, "before_commit", commit)


@pytest.mark.parametrize("scenario", ["last-unit", "outing-return", "same-loan", "distinct-loans", "outing-delete", "return-delete"])
def test_stock_concurrent_invariants(app, scenario):
    initial = 1 if scenario == "last-unit" else 6
    item_id = seed(app, initial)
    client = authenticated(app)
    loans = []
    if scenario in {"outing-return", "same-loan", "distinct-loans", "return-delete"}:
        for _ in range(2 if scenario == "distinct-loans" else 1):
            route, data = outing(item_id)
            assert client.post(route, data=data).status_code == 302
        with app.app_context():
            loans = [loan.id for loan in Borrowing.query.order_by(Borrowing.id).all()]
    delete = (f"/items/{item_id}/delete", {})
    operations = {
        "last-unit": (outing(item_id), outing(item_id)),
        "outing-return": (outing(item_id, 2), returning(loans[0]) if loans else None),
        "same-loan": (returning(loans[0]), returning(loans[0])) if loans else (None, None),
        "distinct-loans": (returning(loans[0]), returning(loans[-1])) if loans else (None, None),
        "outing-delete": (outing(item_id), delete),
        "return-delete": (returning(loans[0]), delete) if loans else (None, None),
    }
    pair(app, *operations[scenario])
    with app.app_context():
        item = db.session.get(Item, item_id)
        all_loans = Borrowing.query.all()
        events = BorrowingEvent.query.all()
        movements = StockMovement.query.order_by(StockMovement.id).all()
        assert all(0 <= loan.quantity_returned <= loan.quantity_out for loan in all_loans)
        assert len(events) == len(movements)
        balance = initial
        for movement in movements:
            balance += movement.delta
            assert movement.quantity_after == balance
        assert sum(e.quantity for e in events if e.event_type == "retour") == sum(l.quantity_returned for l in all_loans)
        if scenario == "return-delete":
            assert item is None
            assert all(loan.remaining_quantity == 0 for loan in all_loans)
        else:
            assert item is not None
            assert item.quantity == balance >= 0
            assert item.quantity + sum(l.remaining_quantity for l in all_loans) == initial
        if scenario == "last-unit":
            assert len(all_loans) == 1
        if scenario == "same-loan":
            assert len(events) == 2
        if scenario == "outing-return":
            assert item.quantity == 4
        if scenario == "distinct-loans":
            assert item.quantity == initial


@pytest.mark.parametrize("first_kind,second_kind", [("role", "role"), ("toggle-active", "toggle-active"), ("role", "toggle-active"), ("toggle-active", "role")])
def test_reciprocal_admin_changes_keep_authorized_active_admin(app, first_kind, second_kind):
    with app.app_context():
        a = db.session.get(User, 1)
        a.role = "admin"
        b = User(name="Fictitious admin B", login="fictitious-b", role="admin")
        b.set_password("123456")
        db.session.add(b)
        db.session.commit()
        b_id = b.id
    first = (f"/admin/members/{b_id}/{first_kind}", {"role": "member"})
    second = (f"/admin/members/1/{second_kind}", {"role": "member"})
    results = pair(app, first, second, users=(1, b_id))
    with app.app_context():
        assert User.query.filter_by(role="admin", is_active_user=True).count() >= 1
        a = db.session.get(User, 1)
        assert a.role == "admin" and a.is_active_user, "requester rights were not rechecked after lock wait"
        assert results[1] == 403


@pytest.mark.parametrize("operation", ["outing", "return"])
def test_deletion_committed_before_competing_operation_is_controlled(app, operation):
    item_id = seed(app)
    client = authenticated(app)
    if operation == "return":
        route, data = outing(item_id)
        assert client.post(route, data=data).status_code == 302
        with app.app_context():
            loan_id = Borrowing.query.one().id
        route, data = returning(loan_id)
        assert client.post(route, data=data).status_code == 302
        second = returning(loan_id)
    else:
        second = outing(item_id)
    result = pair(app, (f"/items/{item_id}/delete", {}), second)
    assert result[1] == (404 if operation == "outing" else 302)
    with app.app_context():
        assert db.session.get(Item, item_id) is None
        assert all(loan.remaining_quantity == 0 for loan in Borrowing.query.all())
        assert BorrowingEvent.query.count() == StockMovement.query.count() == (2 if operation == "return" else 0)


@pytest.mark.parametrize("sqlstate,message", [(None, "database is locked"), ("40001", "serialization conflict"), ("40P01", "deadlock detected"), ("55P03", "lock unavailable")])
def test_operational_lock_conflict_rolls_back_and_returns_business_response(app, logged_in_client, monkeypatch, sqlstate, message):
    item_id = seed(app)
    rollbacks = []
    class FictitiousDriverError(Exception):
        pass
    original = FictitiousDriverError(message)
    original.sqlstate = sqlstate
    def conflict():
        # Establish a transaction before injecting the failed lock statement.
        db.session.query(User).first()
        raise OperationalError("UPDATE bootstrap_locks", {}, original)
    monkeypatch.setattr("app.transactions.acquire_lock", conflict)
    def rolled_back(session):
        rollbacks.append(session)
    event.listen(Session, "after_rollback", rolled_back)
    try:
        route, data = outing(item_id)
        response = logged_in_client.post(route, data=data)
    finally:
        event.remove(Session, "after_rollback", rolled_back)
    assert response.status_code == 302
    assert rollbacks
    with logged_in_client.session_transaction() as session:
        assert any("concurrente" in text for category, text in session.get("_flashes", []))
    with app.app_context():
        assert db.session.get(Item, item_id).quantity == 6
        assert Borrowing.query.count() == BorrowingEvent.query.count() == StockMovement.query.count() == 0


def test_photo_item_creation_serializes_with_initially_empty_category_deletion(app):
    with app.app_context():
        category = Category(name="Initially empty fictitious category")
        db.session.add(category)
        db.session.commit()
        category_id = category.id
        assert Item.query.filter_by(category_id=category_id).count() == 0
    photo = BytesIO()
    Image.new("RGB", (16, 16), "green").save(photo, "PNG")
    photo.seek(0)
    first = ("/items/new", dict(name="Concurrent fictitious photo item", category_id=str(category_id),
                               quantity="1", condition="Bon état", photo=(photo, "fictitious.png")))
    second = (f"/categories/{category_id}/delete", {})
    assert pair(app, first, second) == [302, 302]
    with app.app_context():
        assert db.session.get(Category, category_id) is not None
        item = Item.query.filter_by(name="Concurrent fictitious photo item").one()
        assert item.category_id == category_id and item.quantity == 1
        movement = StockMovement.query.filter_by(item_id=item.id).one()
        assert movement.delta == movement.quantity_after == 1
        photo_path = Path(app.config["UPLOAD_FOLDER"]) / item.photo_key
        assert photo_path.is_file()
        with Image.open(photo_path) as stored:
            assert stored.format == "WEBP"


def test_real_database_lock_timeout_returns_business_conflict_without_mutation(app):
    """Hold an actual uncommitted UPDATE until op2 has timed out in the driver."""
    from app.transactions import acquire_lock
    item_id = seed(app)
    held, attempted, completed, release = Event(), Event(), Event(), Event()
    connections, driver_errors = {}, []
    with app.app_context():
        engine = db.engine
        dialect = engine.dialect.name
        timeout = 5.0 if dialect == "postgresql" else db.session.execute(text("PRAGMA busy_timeout")).scalar_one() / 1000
    assert 0 < timeout <= 10, "test requires a bounded database lock timeout"

    def observe(conn, cursor, statement, parameters, context, many):
        if current_thread().name == "reliability-timeout-request" and statement.lstrip().upper().startswith("UPDATE BOOTSTRAP_LOCKS"):
            connections["request"] = conn.connection.driver_connection
            attempted.set()

    def failed(context):
        if current_thread().name == "reliability-timeout-request":
            driver_errors.append(context.original_exception)

    def holder():
        with app.app_context():
            try:
                acquire_lock()
                connections["holder"] = db.session.connection().connection.driver_connection
                held.set()
                assert release.wait(timeout + 15), "coordinator did not release held database lock"
            finally:
                db.session.rollback()

    def requester():
        current_thread().name = "reliability-timeout-request"
        client = authenticated(app)
        started = monotonic()
        try:
            route, data = outing(item_id)
            response = client.post(route, data=data)
            with client.session_transaction() as session:
                flashes = list(session.get("_flashes", []))
            return response.status_code, flashes, monotonic() - started
        finally:
            completed.set()

    event.listen(engine, "before_cursor_execute", observe)
    event.listen(engine, "handle_error", failed)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(holder)
            try:
                assert held.wait(5), "holder did not acquire the real SQL lock"
                second = pool.submit(requester)
                assert attempted.wait(5), "competing request did not reach the lock UPDATE"
                assert completed.wait(timeout + 5), "lock wait exceeded bounded database timeout"
                status, flashes, elapsed = second.result(1)
                assert not release.is_set(), "holder was released before observing database timeout"
                assert status == 302
                assert any("concurrente" in message for category, message in flashes)
                assert elapsed >= timeout * .7
                assert connections["holder"] is not connections["request"]
                assert driver_errors, "no real DBAPI lock error was observed"
                if dialect == "postgresql":
                    assert getattr(driver_errors[-1], "sqlstate", None) == "55P03"
                else:
                    assert "database is locked" in str(driver_errors[-1]).lower()
            finally:
                release.set()
            first.result(5)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe)
        event.remove(engine, "handle_error", failed)
    with app.app_context():
        assert db.session.get(Item, item_id).quantity == 6
        assert Borrowing.query.count() == BorrowingEvent.query.count() == StockMovement.query.count() == 0

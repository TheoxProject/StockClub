"""Independent V3 regression scenarios; all data is fictitious."""
from datetime import datetime, timezone
from threading import Event, current_thread
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from app.extensions import db
from app.models import Borrowing, BorrowingEvent, Category, Item, StockMovement, User
from test_reliability_concurrency import authenticated, outing, returning


def seed(app, total=10, out=3):
    with app.app_context():
        admin = db.session.get(User, 1)
        admin.role = "admin"
        member = User(name="Fictitious borrower", login="v3-borrower", role="member")
        member.set_password("123456")
        item = Item(name="V3 fictitious item", category=Category(name="V3 fictitious category"),
                    quantity=total, photo_key="items/fictitious-v3.webp")
        db.session.add_all([member, item])
        db.session.commit()
        item_id, member_id = item.id, member.id
    if out:
        route, data = outing(item_id, out)
        assert authenticated(app, member_id).post(route, data=data).status_code == 302
    with app.app_context():
        loan = Borrowing.query.filter_by(item_id=item_id).first()
        return item_id, loan.id if loan else None, member_id


def adjustment(item, operation="remove", quantity=1, reason=None, detail=""):
    return f"/items/{item}/stock-adjustment", dict(operation=operation, quantity=str(quantity),
         reason=reason or ("achat" if operation == "add" else "perte"), detail=detail)


def loss(loan, quantity=1, reason="perte", detail=""):
    return f"/loans/{loan}/write-off", dict(quantity=str(quantity), reason=reason, detail=detail)


def post(app, operation, user=1):
    return authenticated(app, user).post(operation[0], data=operation[1])


def state(app, item_id, loan_id=None):
    with app.app_context():
        item = db.session.get(Item, item_id)
        loan = db.session.get(Borrowing, loan_id) if loan_id else None
        return (item.quantity, item.outstanding_quantity, item.total_quantity,
                (loan.quantity_out, loan.quantity_returned, loan.quantity_lost,
                 loan.remaining_quantity, loan.closed_at is not None) if loan else None,
                BorrowingEvent.query.count(), StockMovement.query.count())


@pytest.mark.parametrize("operation,expected", [("remove", (6, 3, 9)), ("loss", (7, 2, 9)), ("add", (8, 3, 11))])
def test_distinct_available_outstanding_and_total(app, operation, expected):
    item, loan, _ = seed(app)
    action = loss(loan) if operation == "loss" else adjustment(item, operation)
    assert post(app, action).status_code == 302
    assert state(app, item, loan)[:3] == expected
    with app.app_context():
        movement = StockMovement.query.order_by(StockMovement.id.desc()).first()
        assert movement.total_delta == (1 if operation == "add" else -1)
        assert movement.delta == (0 if operation == "loss" else movement.total_delta)
        assert movement.user_id == 1 and movement.created_at is not None
        assert movement.item_name_snapshot == "V3 fictitious item"
        assert movement.borrowing_id == (loan if operation == "loss" else None)


@pytest.mark.parametrize("operation,reason", [("add", "achat"), ("add", "don"), ("add", "correction"), ("add", "autre"), ("remove", "perte"), ("remove", "casse"), ("remove", "correction"), ("remove", "autre")])
def test_adjustment_reasons_and_zero_record_refill(app, operation, reason):
    item, _, _ = seed(app, total=2, out=0)
    assert post(app, adjustment(item, operation, 2, reason, "Fictitious detail")).status_code == 302
    assert state(app, item)[0] == (4 if operation == "add" else 0)
    with app.app_context():
        m = StockMovement.query.one()
        assert m.reason == reason and m.note == "Fictitious detail"
        assert m.movement_type == ("ajout" if operation == "add" else "retrait")
    assert post(app, adjustment(item, "add", 1)).status_code == 302
    assert state(app, item)[0] == (5 if operation == "add" else 1)


@pytest.mark.parametrize("kind,patch", [
    ("adjust", {"quantity": value}) for value in ["0", "-1", "1.5", "1000001", "9" * 100, ""]
] + [("adjust", p) for p in [{"operation": "bad"}, {"reason": "achat"}, {"reason": "bad"}, {"reason": "autre", "detail": "  "}, {"detail": "x" * 241}]] + [
    ("loss", {"quantity": value}) for value in ["0", "-1", "1.5", "1000001", "9" * 100, "", "4"]
] + [("loss", p) for p in [{"reason": "achat"}, {"reason": "autre"}, {"reason": "bad"}, {"detail": "x" * 241}]])
def test_invalid_adjustment_and_loss_are_atomic(app, kind, patch):
    item, loan, _ = seed(app)
    path, data = adjustment(item) if kind == "adjust" else loss(loan)
    data.update(patch)
    before = state(app, item, loan)
    assert post(app, (path, data)).status_code == 400
    assert state(app, item, loan) == before


@pytest.mark.parametrize("total,out,operation,amount,status", [(1000000, 3, "add", 1, 400), (999999, 3, "add", 1, 302), (10, 3, "remove", 8, 400), (10, 3, "remove", 7, 302)])
def test_limits_include_outstanding_before_arithmetic(app, total, out, operation, amount, status):
    item, loan, _ = seed(app, total, out)
    before = state(app, item, loan)
    assert post(app, adjustment(item, operation, amount)).status_code == status
    if status == 400:
        assert state(app, item, loan) == before
    else:
        assert state(app, item, loan)[2] <= 1000000


@pytest.mark.parametrize("reason", ["perte", "casse"])
@pytest.mark.parametrize("close_with", ["loss", "return"])
def test_partial_loss_then_closure_and_immutable_ledger(app, reason, close_with):
    item, loan, borrower = seed(app)
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    assert post(app, loss(loan, 1, reason, "Fictitious breakage")).status_code == 302
    assert state(app, item, loan)[3] == (3, 0, 1, 2, False)
    closing = loss(loan, 2, reason) if close_with == "loss" else (f"/loans/{loan}/return", {"quantity": "2"})
    assert post(app, closing).status_code == 302
    snapshot = state(app, item, loan)
    assert snapshot[3] == (3, 0 if close_with == "loss" else 2, 3 if close_with == "loss" else 1, 0, True)
    assert post(app, loss(loan)).status_code == 400
    assert post(app, (f"/loans/{loan}/return", {"quantity": "1"})).status_code == 302
    assert state(app, item, loan) == snapshot
    with app.app_context():
        events = BorrowingEvent.query.order_by(BorrowingEvent.id).all()
        assert events[0].event_type == "sortie" and events[0].quantity == 3 and events[0].user_id == borrower
        assert events[1].event_type == reason and events[1].quantity == 1
        assert events[1].user_id == 1 and events[1].note == "Fictitious breakage"
        assert events[1].created_at.replace(tzinfo=None) >= before
        assert db.session.get(Borrowing, loan).closed_at is not None


def test_loss_prevents_return_exceeding_remaining(app):
    item, loan, _ = seed(app)
    assert post(app, loss(loan, 2)).status_code == 302
    before = state(app, item, loan)
    assert post(app, (f"/loans/{loan}/return", {"quantity": "2"})).status_code == 302
    assert state(app, item, loan) == before


@pytest.mark.parametrize("kind", ["adjust", "loss"])
def test_admin_authorization_and_csrf(app, kind):
    item, loan, member = seed(app)
    operation = adjustment(item) if kind == "adjust" else loss(loan)
    before = state(app, item, loan)
    assert authenticated(app, member).get(operation[0]).status_code == 403
    assert post(app, operation, member).status_code == 403
    page = authenticated(app, member).get(f"/items/{item}" if kind == "adjust" else "/loans")
    assert operation[0].encode() not in page.data
    app.config["WTF_CSRF_ENABLED"] = True
    assert post(app, operation).status_code == 400
    assert state(app, item, loan) == before


def test_delete_blocks_open_but_preserves_closed_loss_history(app):
    item, loan, _ = seed(app)
    assert post(app, loss(loan, 1)).status_code == 302
    assert post(app, (f"/items/{item}/delete", {})).status_code == 302
    assert state(app, item, loan)[3][-1] is False
    assert post(app, loss(loan, 2)).status_code == 302
    assert post(app, (f"/items/{item}/delete", {})).status_code == 302
    with app.app_context():
        assert db.session.get(Item, item) is None
        l = db.session.get(Borrowing, loan)
        assert l.item_id is None and l.quantity_out == l.quantity_lost == 3
        assert len(l.events) == 3
        assert all(e.item_id is None and e.item_name_snapshot == "V3 fictitious item" for e in l.events)
        assert StockMovement.query.count() == 3


@pytest.mark.parametrize("kind", ["adjust", "loss"])
def test_fault_before_commit_rolls_back_all_domain_records(app, kind):
    item, loan, _ = seed(app)
    before = state(app, item, loan)
    def fail(session):
        session.flush()
        raise RuntimeError("fictitious before commit failure")
    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="fictitious before commit"):
            post(app, adjustment(item) if kind == "adjust" else loss(loan, 3))
    finally:
        event.remove(Session, "before_commit", fail)
    assert state(app, item, loan) == before


def test_unflushed_loan_defaults_are_safe():
    loan = Borrowing(quantity_out=3, quantity_returned=0)
    assert loan.remaining_quantity == 3 and loan.is_open


# Adapted from test_reliability_concurrency.pair; 400 is a V3 validation refusal.
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
        assert all(code in {302, 400, 403, 404, 409} for code in results), results
        if "reliability-second" in sessions:
            assert sessions["reliability-first"] is not sessions["reliability-second"]
        return results
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", sql)
        event.remove(Session, "before_commit", commit)



@pytest.mark.parametrize("scenario", ["remove-borrow", "two-removes", "add-return-cap", "loss-return", "two-losses", "adjust-delete"])
def test_v3_concurrency_serializes_independent_connections(app, scenario):
    total = 999999 if scenario == "add-return-cap" else 4
    out = 1 if scenario in {"add-return-cap", "loss-return", "two-losses"} else 0
    item, loan, _ = seed(app, total, out)
    ops = {
        "remove-borrow": (adjustment(item, "remove", 4), outing(item)),
        "two-removes": (adjustment(item, "remove", 3), adjustment(item, "remove", 3)),
        "add-return-cap": (adjustment(item, "add", 1), returning(loan)),
        "loss-return": (loss(loan), returning(loan)),
        "two-losses": (loss(loan), loss(loan)),
        "adjust-delete": (adjustment(item, "add", 1), (f"/items/{item}/delete", {})),
    }
    results = pair(app, *ops[scenario])
    with app.app_context():
        obj = db.session.get(Item, item)
        loans = Borrowing.query.all()
        movements = StockMovement.query.order_by(StockMovement.id).all()
        assert all(l.quantity_returned + l.quantity_lost <= l.quantity_out for l in loans)
        balance = total
        total_balance = total
        for m in movements:
            balance += m.delta
            total_balance += m.total_delta
            assert m.quantity_after == balance >= 0
        if scenario == "adjust-delete":
            assert obj is None and len(movements) == 1 and movements[0].item_id is None
        else:
            assert obj is not None and obj.quantity == balance
            assert obj.total_quantity == total_balance <= 1000000
        if scenario == "remove-borrow":
            assert not loans and obj.quantity == 0
        if scenario == "two-removes":
            assert results == [302, 400] and obj.quantity == 1 and len(movements) == 1
        if scenario == "add-return-cap":
            assert obj.quantity == obj.total_quantity == 1000000 and loans[0].quantity_returned == 1
        if scenario in {"loss-return", "two-losses"}:
            assert loans[0].quantity_out == loans[0].quantity_lost == 1
            assert loans[0].quantity_returned == loans[0].remaining_quantity == 0
            assert loans[0].closed_at is not None and obj.quantity == 3
            assert len(movements) == BorrowingEvent.query.count() == 2
            if scenario == "two-losses":
                assert results == [302, 400]


@pytest.mark.parametrize("kind", ["adjust", "loss"])
def test_admin_revocation_while_waiting_is_rechecked(app, kind):
    item, loan, second_admin = seed(app)
    with app.app_context():
        db.session.get(User, second_admin).role = "admin"
        db.session.commit()
    before = state(app, item, loan)
    action = adjustment(item) if kind == "adjust" else loss(loan)
    assert pair(app, (f"/admin/members/{second_admin}/role", {"role": "member"}), action,
                users=(1, second_admin)) == [302, 403]
    assert state(app, item, loan) == before


@pytest.mark.parametrize("kind", ["adjust", "loss"])
def test_maximum_detail_length_is_accepted(app, kind):
    item, loan, _ = seed(app)
    action = adjustment(item, detail="x" * 240, reason="autre") if kind == "adjust" else loss(loan, detail="x" * 240)
    assert post(app, action).status_code == 302
    with app.app_context():
        assert StockMovement.query.order_by(StockMovement.id.desc()).first().note == "x" * 240


@pytest.mark.parametrize("return_first", [False, True])
def test_fully_out_item_retains_zero_available_and_can_be_refilled(app, return_first):
    item, loan, _ = seed(app, total=3, out=3)
    if return_first:
        assert post(app, (f"/loans/{loan}/return", {"quantity": "1"})).status_code == 302
    lost = 2 if return_first else 3
    assert post(app, loss(loan, lost, "casse")).status_code == 302
    available = 1 if return_first else 0
    assert state(app, item, loan)[:3] == (available, 0, available)
    assert state(app, item, loan)[3] == (3, available, lost, 0, True)
    assert post(app, adjustment(item, "add", 2, "don")).status_code == 302
    assert state(app, item, loan)[:3] == (available + 2, 0, available + 2)

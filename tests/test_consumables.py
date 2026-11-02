"""Independent consumable acceptance checks, fictitious isolated records only."""
from html import unescape
from io import BytesIO
import re

import pytest
from PIL import Image
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (Borrowing, BorrowingEvent, BoxContent, Category, Consumption,
                        Item, StockMovement, StorageBox, User)
from test_reliability_concurrency import authenticated
from test_v3_boxes import borrow, grouped, transfer, present
from test_v3_stock_adjustments import pair


def seed(app, total=10):
    with app.app_context():
        db.session.get(User, 1).role = "admin"
        member = User(name="Fictitious consumer", login="consumer", role="member")
        member.set_password("123456")
        item = Item(name="Fictitious rolls", category=Category(name="Consumable fixtures"),
                    quantity=total, unit="rouleaux", photo_key="", is_consumable=True)
        box = StorageBox(name="Consumable box", name_key="consumable box")
        db.session.add_all([member, item, box])
        db.session.commit()
        return item.id, box.id, member.id


def form(app, path, data=None, user=1):
    client = authenticated(app, user)
    page = client.get(path)
    assert page.status_code == 200, (path, page.status_code)
    match = re.search(r'name="operation_token"[^>]*value="([^"]+)"', page.get_data(as_text=True))
    assert match, path
    return path, dict(operation_token=unescape(match.group(1)), **(data or {}))


def post(app, action, user=1):
    return authenticated(app, user).post(action[0], data=action[1])


def direct(app, item, amount=1, box=None, user=1):
    return form(app, f"/items/{item}/consume", dict(quantity=str(amount), source_box=str(box or ""), note="Fictitious consumption"), user)


def consume_loan(app, loan, amount=1, user=1):
    return form(app, f"/loans/{loan}/consume", dict(quantity=str(amount), note="Fictitious loan consumption"), user)


def replenish(app, item, amount=1, user=1):
    return form(app, f"/items/{item}/replenish", dict(quantity=str(amount), note="Fictitious refill"), user)


def cancel(app, ident, **extra):
    return form(app, f"/consumptions/{ident}/cancel", dict(note="Fictitious correction", **extra))


def latest(app):
    with app.app_context():
        return Consumption.query.order_by(Consumption.id.desc()).first().id


def snapshot(app):
    with app.app_context():
        return {t.name: [tuple(row) for row in db.session.execute(t.select().order_by(*t.primary_key.columns))]
                for t in db.metadata.sorted_tables if t.name != "bootstrap_locks"}


def balance(app, item, available, outstanding, total):
    with app.app_context():
        obj = db.session.get(Item, item)
        assert (obj.quantity, obj.outstanding_quantity, obj.total_quantity) == (available, outstanding, total)
        assert sum(c.present_quantity for c in BoxContent.query.filter_by(item_id=item)) <= available
        for loan in Borrowing.query.filter_by(item_id=item):
            assert loan.quantity_out == loan.quantity_returned + loan.quantity_consumed + loan.quantity_lost + loan.remaining_quantity
            assert loan.remaining_quantity >= 0
            assert (loan.closed_at is None) == (loan.remaining_quantity > 0)


@pytest.mark.parametrize("boxed", [False, True])
def test_direct_consumption_preserves_loans_and_origin_and_actor(app, boxed):
    item, box, member = seed(app)
    if boxed:
        assert post(app, transfer(box, item, 8, destination=box)).status_code == 302
    assert post(app, borrow(item, 2, box if boxed else None)).status_code == 302
    assert post(app, direct(app, item, 3, box if boxed else None, member), member).status_code == 302
    balance(app, item, 5, 2, 7)
    with app.app_context():
        assert Borrowing.query.count() == 1 and BorrowingEvent.query.count() == 1
        record = Consumption.query.one()
        assert record.kind == "direct" and record.quantity == 3 and record.user_id == member
        assert record.created_at and record.note == "Fictitious consumption"
        assert record.box_id == (box if boxed else None)
        assert present(box, item) == (3 if boxed else 0)
    assert post(app, cancel(app, latest(app))).status_code == 302
    balance(app, item, 8, 2, 10)


@pytest.mark.parametrize("boxed", [False, True])
def test_ten_roll_example_reliquat_group_return_and_reopening(app, boxed):
    item, box, member = seed(app)
    if boxed:
        assert post(app, transfer(box, item, 6, destination=box)).status_code == 302
    assert post(app, borrow(item, 6, box if boxed else None)).status_code == 302
    with app.app_context():
        loan = Borrowing.query.one().id
    assert post(app, consume_loan(app, loan, 4, member), member).status_code == 302
    consumed = latest(app)
    balance(app, item, 4, 2, 6)
    operation = grouped(box, [(loan, 2)], True) if boxed else (f"/loans/{loan}/return", {"quantity": "2"})
    assert post(app, operation).status_code == 302
    balance(app, item, 6, 0, 6)
    with app.app_context():
        l = db.session.get(Borrowing, loan)
        assert (l.quantity_out, l.quantity_returned, l.quantity_consumed) == (6, 2, 4)
        assert Consumption.query.one().user_id == member
        assert present(box, item) == (2 if boxed else 0)
    assert post(app, cancel(app, consumed)).status_code == 302
    balance(app, item, 6, 4, 10)
    before = snapshot(app)
    assert post(app, (f"/consumptions/{consumed}/cancel", {"note": "Second attempt"})).status_code in {400, 409}
    assert snapshot(app) == before


@pytest.mark.parametrize("user", [1, 2])
def test_replenishment_goes_outside_and_respects_global_cap(app, user):
    item, box, _ = seed(app, 999999)
    assert post(app, transfer(box, item, 2, destination=box)).status_code == 302
    assert post(app, replenish(app, item, 1, user), user).status_code == 302
    balance(app, item, 1000000, 0, 1000000)
    with app.app_context():
        assert present(box, item) == 2
        movement = StockMovement.query.order_by(StockMovement.id.desc()).first()
        assert movement.user_id == user and movement.delta == movement.total_delta == 1
        assert movement.box_id is None
    before = snapshot(app)
    assert post(app, replenish(app, item, 1, user), user).status_code in {302, 400}
    assert snapshot(app) == before


@pytest.mark.parametrize("kind", ["direct", "loan", "refill"])
@pytest.mark.parametrize("value", ["0", "-1", "1.2", "1000001", "9" * 100, "", "11"])
def test_invalid_quantities_atomic(app, kind, value):
    item, _, _ = seed(app)
    if kind == "loan":
        assert post(app, borrow(item, 3)).status_code == 302
        with app.app_context():
            loan = Borrowing.query.one().id
    action = consume_loan(app, loan, value) if kind == "loan" else (direct(app, item, value) if kind == "direct" else replenish(app, item, value))
    if kind == "refill" and value == "11":
        return  # Refill does not use the available-stock bound.
    before = snapshot(app)
    assert post(app, action).status_code in {302, 400}
    assert snapshot(app) == before


@pytest.mark.parametrize("kind", ["direct", "loan", "refill", "cancel"])
def test_csrf_and_failure_after_flush_are_atomic(app, kind):
    item, _, _ = seed(app)
    assert post(app, borrow(item, 3)).status_code == 302
    with app.app_context():
        loan = Borrowing.query.one().id
    if kind == "cancel":
        assert post(app, direct(app, item)).status_code == 302
    action = {"direct": lambda: direct(app, item), "loan": lambda: consume_loan(app, loan),
              "refill": lambda: replenish(app, item), "cancel": lambda: cancel(app, latest(app))}[kind]()
    before = snapshot(app)
    app.config["WTF_CSRF_ENABLED"] = True
    assert post(app, action).status_code == 400
    app.config["WTF_CSRF_ENABLED"] = False
    assert snapshot(app) == before
    def fail(session):
        session.flush()
        raise RuntimeError("fictitious consumable commit failure")
    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="fictitious consumable"):
            post(app, action)
    finally:
        event.remove(Session, "before_commit", fail)
    assert snapshot(app) == before


@pytest.mark.parametrize("kind", ["direct", "loan", "refill"])
def test_server_replay_is_idempotent_and_token_required(app, kind):
    item, _, member = seed(app)
    assert post(app, borrow(item, 3)).status_code == 302
    with app.app_context():
        loan = Borrowing.query.one().id
    action = consume_loan(app, loan, user=member) if kind == "loan" else (direct(app, item, user=member) if kind == "direct" else replenish(app, item, user=member))
    assert post(app, action, member).status_code == 302
    before = snapshot(app)
    assert post(app, action, member).status_code == 302
    assert snapshot(app) == before
    altered = dict(action[1], quantity="2")
    assert post(app, (action[0], altered), member).status_code in {400, 409}
    assert post(app, (action[0], {"quantity": "1"}), member).status_code == 400
    assert snapshot(app) == before


def test_deleted_box_requires_explicit_outside_restitution_deleted_item_blocks(app):
    item, box, _ = seed(app)
    assert post(app, transfer(box, item, 4, destination=box)).status_code == 302
    assert post(app, direct(app, item, 2, box)).status_code == 302
    ident = latest(app)
    assert post(app, (f"/boxes/{box}/delete", {})).status_code == 302
    before = snapshot(app)
    assert post(app, cancel(app, ident)).status_code in {302, 400}
    assert snapshot(app) == before
    assert post(app, cancel(app, ident, restore_outside="1")).status_code == 302
    balance(app, item, 10, 0, 10)
    with app.app_context():
        correction = db.session.get(Consumption, ident).cancellation
        assert correction.original_id == ident and correction.restored_outside
        assert correction.box_id is None
        assert StockMovement.query.order_by(StockMovement.id.desc()).first().reason == "hors_boite"
    assert post(app, direct(app, item)).status_code == 302
    ident = latest(app)
    assert post(app, (f"/items/{item}/delete", {})).status_code == 302
    before = snapshot(app)
    assert post(app, (f"/consumptions/{ident}/cancel", {"note": "Cannot restore deleted item"})).status_code in {302, 400, 404, 409}
    assert snapshot(app) == before


def edit_data(app, item, **extra):
    with app.app_context():
        obj = db.session.get(Item, item)
        return dict(name=obj.name, category_id=str(obj.category_id), condition=obj.condition,
                    quantity=str(obj.quantity), unit=obj.unit, **extra)


@pytest.mark.parametrize("user,enabled", [(1, True), (2, False)])
def test_creation_and_edit_admin_fields_are_server_protected(app, user, enabled):
    item, _, _ = seed(app)
    data = edit_data(app, item, is_consumable="1", consumption_threshold="3")
    assert post(app, (f"/items/{item}/edit", data), user).status_code == 302
    with app.app_context():
        assert db.session.get(Item, item).consumption_threshold == (3 if enabled else None)
    photo = BytesIO()
    Image.new("RGB", (16, 16), "green").save(photo, "PNG")
    photo.seek(0)
    data.update(name="Created fictitious consumable", photo=(photo, "fixture.png"))
    assert post(app, ("/items/new", data), user).status_code == 302
    with app.app_context():
        created = Item.query.filter_by(name="Created fictitious consumable").one()
        assert created.is_consumable == enabled
        assert created.consumption_threshold == (3 if enabled else None)


def test_member_cannot_disable_and_admin_disable_blocked_by_open_loan(app):
    item, _, member = seed(app)
    assert post(app, (f"/items/{item}/edit", edit_data(app, item)), member).status_code == 302
    with app.app_context():
        assert db.session.get(Item, item).is_consumable
    assert post(app, borrow(item, 2)).status_code == 302
    before = snapshot(app)
    assert post(app, (f"/items/{item}/edit", edit_data(app, item))).status_code in {302, 400}
    assert snapshot(app) == before
    with app.app_context():
        loan = Borrowing.query.one().id
    assert post(app, consume_loan(app, loan, 2)).status_code == 302
    assert post(app, (f"/items/{item}/edit", edit_data(app, item))).status_code == 302
    with app.app_context():
        assert not db.session.get(Item, item).is_consumable
        assert Consumption.query.count() == 1


@pytest.mark.parametrize("kind", ["direct", "loan", "refill"])
def test_non_consumable_refused_with_previously_valid_token(app, kind):
    item, _, member = seed(app)
    assert post(app, borrow(item, 2)).status_code == 302
    with app.app_context():
        loan = Borrowing.query.one().id
    action = consume_loan(app, loan, user=member) if kind == "loan" else (direct(app, item, user=member) if kind == "direct" else replenish(app, item, user=member))
    with app.app_context():
        db.session.get(Item, item).is_consumable = False
        db.session.commit()
    before = snapshot(app)
    assert authenticated(app, member).get(action[0]).status_code == 404
    assert post(app, action, member).status_code in {302, 400, 404}
    assert snapshot(app) == before


@pytest.mark.parametrize("threshold,total,flag", [(None, 0, False), (0, 0, True), (0, 1, False), (3, 3, True), (3, 4, False)])
def test_attention_threshold_empty_zero_equality_and_existing_condition(app, threshold, total, flag):
    item, _, _ = seed(app, total)
    with app.app_context():
        db.session.get(Item, item).consumption_threshold = threshold
        db.session.commit()
    page = authenticated(app).get("/?attention=1").get_data(as_text=True)
    assert ("Fictitious rolls" in page) == flag
    with app.app_context():
        db.session.get(Item, item).condition = "À réparer"
        db.session.commit()
    assert "Fictitious rolls" in authenticated(app).get("/?attention=1").get_data(as_text=True)


def test_outing_does_not_trigger_total_threshold_but_consumption_and_refill_do(app):
    item, _, _ = seed(app)
    with app.app_context():
        db.session.get(Item, item).consumption_threshold = 3
        db.session.commit()
    assert post(app, borrow(item, 8)).status_code == 302
    assert "Fictitious rolls" not in authenticated(app).get("/?attention=1").get_data(as_text=True)
    with app.app_context():
        loan = Borrowing.query.one().id
    assert post(app, consume_loan(app, loan, 7)).status_code == 302
    page = authenticated(app).get("/?attention=1").get_data(as_text=True)
    assert "Fictitious rolls" in page and "Stock bas" in page
    assert post(app, replenish(app, item, 1)).status_code == 302
    assert "Fictitious rolls" not in authenticated(app).get("/?attention=1").get_data(as_text=True)


def test_correction_admin_rights_required_reason_and_global_cap(app):
    item, _, member = seed(app)
    assert post(app, direct(app, item, 2)).status_code == 302
    ident = latest(app)
    action = cancel(app, ident)
    before = snapshot(app)
    assert authenticated(app, member).get(action[0]).status_code == 403
    assert post(app, action, member).status_code == 403
    assert post(app, (action[0], dict(action[1], note="  "))).status_code in {302, 400}
    assert snapshot(app) == before
    assert post(app, replenish(app, item, 999992)).status_code == 302
    before = snapshot(app)
    assert post(app, action).status_code in {302, 400}
    assert snapshot(app) == before


def test_consumed_loan_deleted_box_or_item_cannot_create_orphan(app):
    item, box, _ = seed(app)
    assert post(app, transfer(box, item, 4, destination=box)).status_code == 302
    assert post(app, borrow(item, 4, box)).status_code == 302
    with app.app_context():
        loan = Borrowing.query.one().id
    assert post(app, consume_loan(app, loan, 4)).status_code == 302
    ident = latest(app)
    action = cancel(app, ident)
    assert post(app, (f"/boxes/{box}/delete", {})).status_code == 302
    before = snapshot(app)
    assert post(app, action).status_code in {302, 400, 409}
    assert snapshot(app) == before
    assert post(app, (f"/items/{item}/delete", {})).status_code == 302
    before = snapshot(app)
    assert post(app, action).status_code in {302, 400, 409}
    assert snapshot(app) == before


def test_cancellation_exact_replay_and_second_distinct_submission(app):
    item, _, _ = seed(app)
    assert post(app, direct(app, item, 2)).status_code == 302
    ident = latest(app)
    first, second = cancel(app, ident), cancel(app, ident)
    assert post(app, first).status_code == 302
    before = snapshot(app)
    assert post(app, first).status_code == 302
    assert snapshot(app) == before
    assert post(app, second).status_code in {302, 400, 409}
    assert snapshot(app) == before


@pytest.mark.parametrize("misuse", ["actor", "target", "operation", "tampered", "invalid"])
def test_tokens_bound_to_actor_target_operation(app, misuse):
    item, _, member = seed(app)
    action = direct(app, item)
    user = 1
    if misuse == "actor":
        user = member
    elif misuse == "target":
        action = (f"/items/{item + 999}/consume", action[1])
    elif misuse == "operation":
        action = (f"/items/{item}/replenish", action[1])
    else:
        action[1]["operation_token"] = (action[1]["operation_token"] + "tampered") if misuse == "tampered" else "invalid"
    before = snapshot(app)
    assert post(app, action, user).status_code == 400
    assert snapshot(app) == before


@pytest.mark.parametrize("scenario", ["two-direct", "direct-borrow", "direct-transfer", "direct-admin-remove", "loan-return", "loan-group-return", "two-loan", "loan-loss", "two-cancel", "refill-cap", "disable-borrow", "disable-consume", "cancel-delete-item", "cancel-delete-box"])
def test_concurrency_independent_sessions_and_connections(app, scenario):
    item, box, _ = seed(app, 999999 if scenario == "refill-cap" else 4)
    if scenario in {"direct-transfer", "loan-group-return", "cancel-delete-box"}:
        assert post(app, transfer(box, item, 4, destination=box)).status_code == 302
    loan = None
    if scenario in {"loan-return", "loan-group-return", "two-loan", "loan-loss"}:
        assert post(app, borrow(item, 4, box if scenario == "loan-group-return" else None)).status_code == 302
        with app.app_context():
            loan = Borrowing.query.one().id
    if scenario in {"two-cancel", "cancel-delete-item", "cancel-delete-box"}:
        assert post(app, direct(app, item, 4, box if scenario == "cancel-delete-box" else None)).status_code == 302
        ident = latest(app)
        first = cancel(app, ident)
    elif scenario == "refill-cap":
        first = replenish(app, item)
    elif scenario.startswith("disable-"):
        with app.app_context():
            category = db.session.get(Item, item).category_id
        first = (f"/items/{item}/edit", dict(name="Fictitious rolls", category_id=str(category), quantity="4", condition="Bon état", unit="rouleaux"))
    elif loan:
        first = consume_loan(app, loan, 4)
    else:
        first = direct(app, item, 4, box if scenario == "direct-transfer" else None)
    second = {
        "two-direct": lambda: direct(app, item, 4), "direct-borrow": lambda: borrow(item, 1),
        "direct-transfer": lambda: transfer(box, item, 1, source=box),
        "direct-admin-remove": lambda: (f"/items/{item}/stock-adjustment", dict(operation="remove", quantity="1", reason="perte")),
        "loan-return": lambda: (f"/loans/{loan}/return", {"quantity": "1"}),
        "loan-group-return": lambda: grouped(box, [(loan, 1)], True),
        "two-loan": lambda: consume_loan(app, loan, 1),
        "loan-loss": lambda: (f"/loans/{loan}/write-off", dict(quantity="1", reason="perte")),
        "two-cancel": lambda: cancel(app, ident), "refill-cap": lambda: replenish(app, item),
        "disable-borrow": lambda: borrow(item, 1), "disable-consume": lambda: direct(app, item, 1),
        "cancel-delete-item": lambda: (f"/items/{item}/delete", {}),
        "cancel-delete-box": lambda: (f"/boxes/{box}/delete", {}),
    }[scenario]()
    results = pair(app, first, second)
    with app.app_context():
        obj = db.session.get(Item, item)
        if obj:
            assert 0 <= obj.quantity <= obj.total_quantity <= 1000000
            assert sum(c.present_quantity for c in BoxContent.query.filter_by(item_id=item)) <= obj.quantity
        for l in Borrowing.query.all():
            assert l.quantity_out == l.quantity_returned + l.quantity_lost + l.quantity_consumed + l.remaining_quantity
        if scenario in {"two-direct", "two-loan", "two-cancel", "refill-cap", "disable-consume"}:
            assert results[1] in {302, 400, 409}
        if scenario in {"two-direct", "direct-borrow", "direct-transfer", "direct-admin-remove"}:
            assert obj.quantity == 0 and not Borrowing.query.count()
        if loan:
            l = db.session.get(Borrowing, loan)
            assert l.quantity_consumed == 4 and l.quantity_returned == l.quantity_lost == l.remaining_quantity == 0
        if scenario == "refill-cap":
            assert obj.total_quantity == 1000000


@pytest.mark.parametrize("scenario", ["borrow-disable", "consume-disable", "delete-item-cancel", "delete-box-cancel", "revoke-cancel"])
def test_reverse_order_sensitive_mutations(app, scenario):
    item, box, member = seed(app)
    with app.app_context():
        db.session.get(User, member).role = "admin"
        db.session.commit()
    if scenario in {"delete-item-cancel", "delete-box-cancel", "revoke-cancel"}:
        if scenario == "delete-box-cancel":
            assert post(app, transfer(box, item, 2, destination=box)).status_code == 302
        assert post(app, direct(app, item, 2, box if scenario == "delete-box-cancel" else None)).status_code == 302
        ident = latest(app)
        second = form(app, f"/consumptions/{ident}/cancel", {"note": "Fictitious correction"}, member)
        first = (f"/items/{item}/delete", {}) if scenario == "delete-item-cancel" else ((f"/boxes/{box}/delete", {}) if scenario == "delete-box-cancel" else (f"/admin/members/{member}/role", {"role": "member"}))
    else:
        first = borrow(item, 2) if scenario == "borrow-disable" else direct(app, item, 2)
        second = (f"/items/{item}/edit", edit_data(app, item))
    results = pair(app, first, second, users=(1, member))
    with app.app_context():
        if scenario == "borrow-disable":
            assert db.session.get(Item, item).is_consumable
        elif scenario == "consume-disable":
            assert not db.session.get(Item, item).is_consumable
            assert db.session.get(Item, item).quantity == 8
        else:
            assert Consumption.query.filter_by(kind="cancellation").count() == 0
            if scenario == "revoke-cancel":
                assert results[1] == 403


def test_expired_operation_token_is_refused_without_mutation(app, monkeypatch):
    from itsdangerous import TimestampSigner
    item, _, _ = seed(app)
    with monkeypatch.context() as mocked:
        mocked.setattr(TimestampSigner, "get_timestamp", lambda self: 1000000000)
        action = direct(app, item)
    before = snapshot(app)
    assert post(app, action).status_code == 400
    assert snapshot(app) == before

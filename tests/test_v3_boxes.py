"""Independent physical-box acceptance tests, using fictitious isolated data only."""
import re

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import (BoxContent, BoxEvent, Borrowing, BorrowingEvent, Category,
                        Item, LoanGroup, StockMovement, StorageBox, User)
from test_reliability_concurrency import authenticated
from test_v3_stock_adjustments import pair


def post(app, operation, user=1):
    return authenticated(app, user).post(operation[0], data=operation[1])


def seed(app):
    with app.app_context():
        db.session.get(User, 1).role = "admin"
        member = User(name="Fictitious box member", login="box-member", role="member")
        member.set_password("123456")
        category = Category(name="Fictitious boxes")
        items = [Item(name=name, category=category, quantity=10,
                      photo_key="items/fictitious-box.webp") for name in ("Cone", "Plastic crate")]
        db.session.add_all([member, *items])
        db.session.commit()
        ids, member_id = [i.id for i in items], member.id
    boxes = []
    for name in ("Arrival", "Signs"):
        assert post(app, ("/boxes/new", {"name": name, "description": "Fictitious"})).status_code == 302
        with app.app_context():
            boxes.append(StorageBox.query.filter_by(name=name).one().id)
    return ids, boxes, member_id


def transfer(box, item, amount, source=None, destination=None):
    return f"/boxes/{box}/transfer", dict(item_id=str(item), quantity=str(amount),
        source_box_id=str(source or ""), destination_box_id=str(destination or ""))


def grouped(box, amounts, returning=False):
    data = MultiDict()
    for ident, amount in amounts:
        data.add("selected", str(ident))
        data.add(f"quantity_{ident}", str(amount))
    return f"/boxes/{box}/{'return' if returning else 'checkout'}", data


def borrow(item, amount, box=None):
    return f"/items/{item}/adjust-stock", dict(quantity=str(amount), source_box_id=str(box or ""))


def present(box, item):
    row = BoxContent.query.filter_by(box_id=box, item_id=item).first()
    return row.present_quantity if row else 0


def snapshot(app):
    """Compare every domain write, including audit records, after refused mutations."""
    with app.app_context():
        result = {}
        for cls in (Item, StorageBox, BoxContent, Borrowing, LoanGroup, BorrowingEvent, StockMovement, BoxEvent):
            result[cls.__tablename__] = [tuple(getattr(row, col.name) for col in cls.__table__.columns)
                for row in cls.query.order_by(cls.id).all()]
        return result


def invariants(app):
    with app.app_context():
        for item in Item.query.all():
            allocated_present = sum(c.present_quantity for c in BoxContent.query.filter_by(item_id=item.id))
            assert 0 <= allocated_present <= item.quantity
            loans = Borrowing.query.filter_by(item_id=item.id).all()
            assert all(l.quantity_out >= l.quantity_returned + l.quantity_lost >= 0 for l in loans)
            assert item.total_quantity == item.quantity + sum(l.remaining_quantity for l in loans)
        assert all(c.present_quantity >= 0 for c in BoxContent.query.all())
        assert not any(c.present_quantity for c in BoxContent.query.join(StorageBox).filter(StorageBox.is_active.is_(False)))


def test_box_names_normalization_rename_and_search(app):
    items, boxes, member = seed(app)
    assert post(app, ("/boxes/new", {"name": "  ARriVal  "})).status_code in {200, 400}
    assert post(app, ("/boxes/new", {"name": " \t "})).status_code in {200, 400}
    assert post(app, ("/boxes/new", {"name": "  Start   Line  "})).status_code == 302
    assert post(app, ("/boxes/new", {"name": "start line"})).status_code in {200, 400}
    assert post(app, ("/boxes/new", {"name": "Straße"})).status_code == 302
    assert post(app, ("/boxes/new", {"name": "STRASSE"})).status_code in {200, 400}
    before = snapshot(app)
    assert post(app, (f"/boxes/{boxes[0]}/edit", {"name": "SIGNS"})).status_code in {200, 400}
    assert snapshot(app) == before
    assert post(app, (f"/boxes/{boxes[0]}/edit", {"name": "Finish", "description": "Updated"})).status_code == 302
    with app.app_context():
        assert StorageBox.query.count() == 4
    page = authenticated(app, member).get("/boxes/?q=Finish")
    assert page.status_code == 200 and b"Finish" in page.data and b"Signs" not in page.data


@pytest.mark.parametrize("action", ["create", "edit", "transfer", "delete"])
def test_box_admin_rights_direct_and_csrf(app, action):
    items, boxes, member = seed(app)
    box, item = boxes[0], items[0]
    operations = dict(create=("/boxes/new", {"name": "Forbidden"}),
        edit=(f"/boxes/{box}/edit", {"name": "Forbidden"}),
        transfer=transfer(box, item, 1, destination=box), delete=(f"/boxes/{box}/delete", {}))
    before = snapshot(app)
    assert post(app, operations[action], member).status_code == 403
    app.config["WTF_CSRF_ENABLED"] = True
    assert post(app, operations[action]).status_code == 400
    assert snapshot(app) == before


def test_distribution_transfer_and_immutable_history(app):
    items, boxes, _ = seed(app)
    item, (a, b) = items[0], boxes
    for amount, destination in [(4, a), (2, b)]:
        assert post(app, transfer(destination, item, amount, destination=destination)).status_code == 302
    assert post(app, transfer(a, item, 1, a, b)).status_code == 302
    assert post(app, transfer(b, item, 1, b)).status_code == 302
    with app.app_context():
        assert (present(a, item), present(b, item), db.session.get(Item, item).quantity) == (3, 2, 10)
        events = BoxEvent.query.order_by(BoxEvent.id).all()
        assert len(events) == 4
        assert (events[2].source_box_id, events[2].destination_box_id, events[2].quantity) == (a, b, 1)
        assert all(e.user_id == 1 and e.created_at and e.item_name_snapshot == "Cone" for e in events)
        assert StockMovement.query.count() == 0
    before = snapshot(app)
    for op in [transfer(a, item, 6, destination=a), transfer(a, item, 4, a, b), transfer(a, item, 1, a, a), transfer(a, item, -1, destination=a)]:
        assert post(app, op).status_code in {302, 400}
        assert snapshot(app) == before
    invariants(app)


def test_individual_origin_return_and_outside_limit(app):
    items, boxes, member = seed(app)
    item, box = items[0], boxes[0]
    assert post(app, transfer(box, item, 8, destination=box)).status_code == 302
    before = snapshot(app)
    # Classic missing-origin compatibility must not borrow units physically in boxes.
    assert post(app, (f"/items/{item}/adjust-stock", {"quantity": "3"}), member).status_code in {302, 400}
    assert snapshot(app) == before
    assert post(app, borrow(item, 3, box), member).status_code == 302
    with app.app_context():
        loan = Borrowing.query.one()
        loan_id = loan.id
        assert (loan.box_id, loan.box_name_snapshot, loan.user_id, loan.group_id) == (box, "Arrival", member, None)
        assert present(box, item) == 5 and db.session.get(Item, item).quantity == 7
    assert post(app, (f"/loans/{loan_id}/return", {"quantity": "2"})).status_code == 302
    with app.app_context():
        assert present(box, item) == 7 and db.session.get(Item, item).quantity == 9
        assert BorrowingEvent.query.order_by(BorrowingEvent.id.desc()).first().user_id == 1
    invariants(app)


@pytest.mark.parametrize("full", [False, True])
def test_group_checkout_selection_then_mixed_partial_return(app, full):
    items, boxes, member = seed(app)
    box = boxes[0]
    for item in items:
        assert post(app, transfer(box, item, 4, destination=box)).status_code == 302
    selected = [(items[0], 4), (items[1], 4)] if full else [(items[0], 2)]
    before = snapshot(app)
    assert authenticated(app, member).get(f"/boxes/{box}/checkout").status_code == 200
    assert snapshot(app) == before
    assert post(app, grouped(box, selected), member).status_code == 302
    assert post(app, borrow(items[1], 1, box)).status_code in ({302, 400} if full else {302})
    with app.app_context():
        group = LoanGroup.query.one()
        assert group.user_id == member and group.box_id == box and group.created_at
        loans = Borrowing.query.order_by(Borrowing.id).all()
        assert len(group.borrowings) == len(selected)
        first_id = loans[0].id
        others = {l.id: l.remaining_quantity for l in loans[1:]}
    before = snapshot(app)
    assert authenticated(app).get(f"/boxes/{box}/return").status_code == 200
    assert snapshot(app) == before
    assert post(app, grouped(box, [(first_id, 1)], True)).status_code == 302
    with app.app_context():
        assert db.session.get(Borrowing, first_id).quantity_returned == 1
        assert {l.id: l.remaining_quantity for l in Borrowing.query.filter(Borrowing.id != first_id)} == others
        assert BorrowingEvent.query.order_by(BorrowingEvent.id.desc()).first().user_id == 1
    invariants(app)


@pytest.mark.parametrize("returning", [False, True])
@pytest.mark.parametrize("invalid", ["empty", "zero", "overflow", "foreign", "duplicate"])
def test_group_validation_refuses_all_rows(app, returning, invalid):
    items, boxes, _ = seed(app)
    box = boxes[0]
    for item in items:
        assert post(app, transfer(box, item, 3, destination=box)).status_code == 302
    identifiers = items
    if returning:
        assert post(app, grouped(box, [(i, 2) for i in items])).status_code == 302
        with app.app_context():
            identifiers = [l.id for l in Borrowing.query.order_by(Borrowing.id)]
    amounts = {"empty": [], "zero": [(identifiers[0], 1), (identifiers[1], 0)],
        "overflow": [(identifiers[0], 1), (identifiers[1], 4)],
        "foreign": [(identifiers[0], 1), (999999, 1)],
        "duplicate": [(identifiers[0], 1), (identifiers[0], 1)]}[invalid]
    before = snapshot(app)
    assert post(app, grouped(box, amounts, returning)).status_code == 400
    assert snapshot(app) == before


@pytest.mark.parametrize("returning", [False, True])
def test_group_failure_after_sql_flush_rolls_back_everything(app, returning):
    items, boxes, _ = seed(app)
    box = boxes[0]
    for item in items:
        assert post(app, transfer(box, item, 3, destination=box)).status_code == 302
    identifiers = items
    if returning:
        assert post(app, grouped(box, [(i, 2) for i in items])).status_code == 302
        with app.app_context():
            identifiers = [l.id for l in Borrowing.query.order_by(Borrowing.id)]
    before = snapshot(app)
    written = []
    def fail(session, context):
        # The first line has reached SQL, and the second line has not been applied.
        affected = [obj.id for obj in session.dirty if isinstance(obj, Item)]
        if affected:
            written.extend(affected)
            raise RuntimeError("fictitious group failure after SQL flush")
    event.listen(Session, "after_flush", fail)
    try:
        with pytest.raises(RuntimeError, match="fictitious group failure"):
            post(app, grouped(box, [(i, 1) for i in identifiers], returning))
    finally:
        event.remove(Session, "after_flush", fail)
    assert written == [items[0]]
    assert snapshot(app) == before


def test_loss_withdrawal_and_add_preserve_origin_and_no_fake_return(app):
    items, boxes, _ = seed(app)
    item, box = items[0], boxes[0]
    assert post(app, transfer(box, item, 8, destination=box)).status_code == 302
    assert post(app, borrow(item, 3, box)).status_code == 302
    with app.app_context():
        loan = Borrowing.query.one().id
    assert post(app, (f"/loans/{loan}/write-off", {"quantity": "2", "reason": "casse"})).status_code == 302
    with app.app_context():
        assert present(box, item) == 5
        obj = db.session.get(Item, item)
        assert (obj.quantity, obj.outstanding_quantity, obj.total_quantity) == (7, 1, 8)
        assert db.session.get(Borrowing, loan).quantity_returned == 0
        assert BorrowingEvent.query.filter_by(event_type="retour").count() == 0
        movement = StockMovement.query.order_by(StockMovement.id.desc()).first()
        assert movement.box_id == box and movement.box_name_snapshot == "Arrival"
    adjustment = f"/items/{item}/stock-adjustment"
    before = snapshot(app)
    assert post(app, (adjustment, dict(operation="remove", quantity="3", reason="perte"))).status_code == 400
    assert snapshot(app) == before
    assert post(app, (adjustment, dict(operation="remove", quantity="2", reason="perte", source_box_id=str(box)))).status_code == 302
    assert post(app, (adjustment, dict(operation="add", quantity="2", reason="achat"))).status_code == 302
    with app.app_context():
        assert present(box, item) == 3 and db.session.get(Item, item).quantity == 7
        assert present(box, items[1]) == 0 and db.session.get(Item, items[1]).quantity == 10
    invariants(app)


def test_delete_box_blocks_open_then_preserves_snapshots_and_item_delete_cleans_contents(app):
    items, boxes, _ = seed(app)
    item, box = items[0], boxes[0]
    assert post(app, transfer(box, item, 4, destination=box)).status_code == 302
    assert post(app, grouped(box, [(item, 2)])).status_code == 302
    before = snapshot(app)
    assert post(app, (f"/boxes/{box}/delete", {})).status_code in {302, 400}
    assert snapshot(app) == before
    assert post(app, (f"/items/{item}/delete", {})).status_code == 302
    assert snapshot(app) == before
    with app.app_context():
        loan_id = Borrowing.query.one().id
    assert post(app, (f"/loans/{loan_id}/return", {"quantity": "2"})).status_code == 302
    assert post(app, (f"/boxes/{box}/edit", {"name": "Renamed"})).status_code == 302
    assert post(app, (f"/boxes/{box}/delete", {})).status_code == 302
    archived = authenticated(app).get(f"/boxes/{box}")
    assert archived.status_code == 200
    assert b"Arrival" in archived.data and b"Renamed" in archived.data
    with app.app_context():
        assert not db.session.get(StorageBox, box).is_active
        assert present(box, item) == 0 and db.session.get(Item, item).quantity == 10
        assert Borrowing.query.one().box_name_snapshot == "Arrival"
        assert LoanGroup.query.one().box_name_snapshot == "Arrival"
        assert BoxEvent.query.order_by(BoxEvent.id).first().destination_name_snapshot == "Arrival"
    assert post(app, transfer(boxes[1], item, 2, destination=boxes[1])).status_code == 302
    assert post(app, (f"/items/{item}/delete", {})).status_code == 302
    with app.app_context():
        assert db.session.get(Item, item) is None
        assert BoxContent.query.filter_by(item_id=item).count() == 0
        assert all(e.item_id is None and e.item_name_snapshot == "Cone" for e in BoxEvent.query.all())


@pytest.mark.parametrize("scenario", ["two-checkouts", "individual-group", "checkout-transfer",
    "individual-group-return", "two-group-returns", "loss-return", "delete-box-allocation", "delete-item-checkout",
    "delete-box-checkout", "return-delete-box", "checkout-unassign"])
def test_box_concurrency_independent_connections(app, scenario):
    items, boxes, _ = seed(app)
    item, (a, b) = items[0], boxes
    assert post(app, transfer(a, item, 4, destination=a)).status_code == 302
    loan = None
    if scenario in {"individual-group-return", "two-group-returns", "loss-return", "return-delete-box"}:
        assert post(app, grouped(a, [(item, 4)])).status_code == 302
        with app.app_context():
            loan = Borrowing.query.one().id
    checkout = grouped(a, [(item, 4)])
    returned = grouped(a, [(loan, 4)], True)
    operations = {
        "two-checkouts": (checkout, checkout),
        "individual-group": (borrow(item, 4, a), checkout),
        "checkout-transfer": (checkout, transfer(a, item, 4, a, b)),
        "individual-group-return": ((f"/loans/{loan}/return", {"quantity": "4"}), returned),
        "two-group-returns": (returned, returned),
        "loss-return": ((f"/loans/{loan}/write-off", {"quantity": "4", "reason": "perte"}), returned),
        "delete-box-allocation": ((f"/boxes/{a}/delete", {}), transfer(a, item, 1, destination=a)),
        "delete-item-checkout": ((f"/items/{item}/delete", {}), checkout),
        "delete-box-checkout": ((f"/boxes/{a}/delete", {}), checkout),
        "return-delete-box": (returned, (f"/boxes/{a}/delete", {})),
        "checkout-unassign": (checkout, transfer(a, item, 4, a)),
    }
    pair(app, *operations[scenario])
    invariants(app)
    with app.app_context():
        loans = Borrowing.query.all()
        if scenario in {"two-checkouts", "individual-group", "checkout-transfer", "checkout-unassign"}:
            assert sum(l.quantity_out for l in loans) == 4
            assert present(a, item) == present(b, item) == 0
        elif scenario in {"individual-group-return", "two-group-returns"}:
            assert loans[0].quantity_returned == 4 and present(a, item) == 4
            assert BorrowingEvent.query.filter_by(event_type="retour").count() == 1
        elif scenario == "loss-return":
            assert loans[0].quantity_lost == 4 and loans[0].quantity_returned == 0
            assert present(a, item) == 0 and db.session.get(Item, item).quantity == 6
        elif scenario == "delete-box-allocation":
            assert not db.session.get(StorageBox, a).is_active and present(a, item) == 0
        elif scenario == "delete-box-checkout":
            assert not db.session.get(StorageBox, a).is_active and not loans
            assert present(a, item) == 0 and db.session.get(Item, item).quantity == 10
        elif scenario == "return-delete-box":
            assert not db.session.get(StorageBox, a).is_active
            assert loans[0].quantity_returned == 4 and present(a, item) == 0
            assert db.session.get(Item, item).quantity == 10
        else:
            assert db.session.get(Item, item) is None and not loans


def test_admin_revocation_while_waiting_rechecks_box_rights(app):
    items, boxes, member = seed(app)
    with app.app_context():
        db.session.get(User, member).role = "admin"
        db.session.commit()
    before = snapshot(app)
    assert pair(app, (f"/admin/members/{member}/role", {"role": "member"}),
        transfer(boxes[0], items[0], 1, destination=boxes[0]), users=(1, member)) == [302, 403]
    assert snapshot(app) == before


def test_return_keeps_separate_loans_for_same_item_and_original_box(app):
    items, boxes, member = seed(app)
    item, (a, b) = items[0], boxes
    assert post(app, transfer(a, item, 7, destination=a)).status_code == 302
    assert post(app, grouped(a, [(item, 2)]), member).status_code == 302
    assert post(app, borrow(item, 3, a)).status_code == 302
    with app.app_context():
        first, second = [l.id for l in Borrowing.query.order_by(Borrowing.id)]
    before = snapshot(app)
    assert post(app, grouped(b, [(second, 1)], True), member).status_code == 400
    assert snapshot(app) == before
    assert post(app, grouped(a, [(second, 1)], True), member).status_code == 302
    with app.app_context():
        assert db.session.get(Borrowing, first).remaining_quantity == 2
        assert db.session.get(Borrowing, second).remaining_quantity == 2
        assert present(a, item) == 3 and present(b, item) == 0
        assert BorrowingEvent.query.order_by(BorrowingEvent.id.desc()).first().user_id == member
    assert post(app, grouped(a, [(first, 2), (second, 2)], True), member).status_code == 302
    with app.app_context():
        assert present(a, item) == 7 and db.session.get(Item, item).quantity == 10
    invariants(app)


@pytest.mark.parametrize("returning", [False, True])
def test_grouped_mutations_require_csrf(app, returning):
    items, boxes, member = seed(app)
    item, box = items[0], boxes[0]
    assert post(app, transfer(box, item, 4, destination=box)).status_code == 302
    ident = item
    if returning:
        assert post(app, borrow(item, 2, box), member).status_code == 302
        with app.app_context():
            ident = Borrowing.query.one().id
    before = snapshot(app)
    app.config["WTF_CSRF_ENABLED"] = True
    assert post(app, grouped(box, [(ident, 1)], returning), member).status_code == 400
    assert snapshot(app) == before


def test_server_pages_distinguish_assigned_present_out_and_link_provenance(app):
    items, boxes, member = seed(app)
    item, (a, b) = items[0], boxes
    assert post(app, transfer(a, item, 4, destination=a)).status_code == 302
    assert post(app, transfer(b, item, 2, destination=b)).status_code == 302
    assert post(app, borrow(item, 2, a), member).status_code == 302
    page = authenticated(app, member).get(f"/boxes/{a}")
    assert page.status_code == 200
    row = re.search(rf'<tr data-box-item="{item}">(.*?)</tr>', page.get_data(as_text=True), re.S).group(1)
    for attribute, amount in (("assigned", 4), ("present", 2), ("outstanding", 2)):
        assert re.search(rf'<td data-{attribute}>\s*{amount}\s*</td>', row)
    assert f'href="/items/{item}"' in row
    page = authenticated(app, member).get(f"/items/{item}")
    assert page.status_code == 200
    assert f'href="/boxes/{a}"'.encode() in page.data
    assert f'href="/boxes/{b}"'.encode() in page.data
    assert f'/boxes/{a}/transfer'.encode() not in authenticated(app, member).get(f"/boxes/{a}").data
    invariants(app)


@pytest.mark.parametrize("ajax", [False, True])
def test_general_search_returns_distinct_box_and_item_results(app, ajax):
    items, boxes, member = seed(app)
    assert post(app, (f"/boxes/{boxes[0]}/edit", {"name": "Cone box"})).status_code == 302
    headers = {"X-Requested-With": "XMLHttpRequest"} if ajax else {}
    response = authenticated(app, member).get("/?q=Cone", headers=headers)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    item = re.search(rf'<a class="item-card" href="/items/{items[0]}">(.*?)</a>', html, re.S)
    box = re.search(rf'<a class="panel box-card" href="/boxes/{boxes[0]}">(.*?)</a>', html, re.S)
    assert item and box
    assert ">Objet<" in item.group(1)
    assert ">Boîte<" in box.group(1)
    assert f'href="/boxes/{boxes[1]}"' not in html
    if ajax:
        assert "<html" not in html
    else:
        assert 'id="inventory-results"' in html

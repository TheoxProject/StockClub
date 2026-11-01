"""Independent acceptance tests for grouped outside-to-box allocation."""
import html
import re
import uuid

import pytest
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import event
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import BoxContent, BoxEvent, BoxFillReceipt, Category, Item, StorageBox, User
from test_reliability_concurrency import authenticated
from test_v3_boxes import borrow, invariants, present, seed, snapshot, transfer
from test_v3_stock_adjustments import pair


def token(client, box):
    response = client.get(f"/boxes/{box}/add-material")
    assert response.status_code == 200
    return html.unescape(re.search(r'name="request_token"[^>]*value="([^"]+)"', response.text).group(1))


def operation(box, request_token, amounts):
    data = MultiDict([("request_token", request_token)])
    for ident, amount in amounts:
        data.add("selected", str(ident))
        data.add(f"quantity_{ident}", str(amount))
        data.add(f"label_{ident}", f"Remembered item {ident}")
    return f"/boxes/{box}/add-material", data


def send(client, op):
    return client.post(op[0], data=op[1], headers={"Accept": "application/json"})


def state(app):
    result = snapshot(app)
    with app.app_context():
        result["receipts"] = [(r.id, r.payload_hash, r.reference_count, r.unit_count) for r in BoxFillReceipt.query.all()]
    return result


@pytest.mark.parametrize("next_action", [None, "empty", "add"])
def test_creation_keeps_box_empty_until_explicit_fill(app, next_action):
    seed(app)
    client = authenticated(app)
    data = {"name": "New empty destination"}
    if next_action:
        data["next_action"] = next_action
    before = state(app)
    response = client.post("/boxes/new", data=data)
    assert response.status_code == 302
    assert response.location.endswith("/add-material") == (next_action == "add")
    after = state(app)
    for table in before.keys() - {"storage_boxes"}:
        assert after[table] == before[table]


def test_rights_and_real_csrf(app):
    items, boxes, member = seed(app)
    client = authenticated(app)
    op = operation(boxes[0], token(client, boxes[0]), [(items[0], 1)])
    before = state(app)
    for route in (op[0], f"/boxes/{boxes[0]}/material-options"):
        assert authenticated(app, member).get(route).status_code == 403
        assert app.test_client().get(route).status_code == 302
    assert send(authenticated(app, member), op).status_code == 403
    app.config["WTF_CSRF_ENABLED"] = True
    assert send(client, op).status_code == 400
    assert state(app) == before
    page = client.get(op[0])
    csrf = html.unescape(re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1))
    op[1].add("csrf_token", csrf)
    assert send(client, op).status_code == 200


def test_options_name_category_descendants_pagination_and_outside_only(app):
    items, boxes, _ = seed(app)
    with app.app_context():
        parent = Category(name="Parent filter")
        child = Category(name="Child filter", parent=parent)
        rows = [Item(name=f"Filtered {i:02}", category=child, quantity=3, photo_key="") for i in range(26)]
        db.session.add_all(rows)
        db.session.commit()
        category_id = parent.id
    client = authenticated(app)
    op = transfer(boxes[0], items[0], 4, destination=boxes[0])
    assert client.post(op[0], data=op[1]).status_code == 302
    assert client.post(transfer(boxes[1], items[1], 10, destination=boxes[1])[0], data=transfer(boxes[1], items[1], 10, destination=boxes[1])[1]).status_code == 302
    base = f"/boxes/{boxes[0]}/material-options"
    result = client.get(base + f"?category={category_id}&q=Filtered").json
    assert (result["total"], result["pages"], len(result["rows"])) == (26, 2, 24)
    second = client.get(base + f"?category={category_id}&page=2").json
    assert len(second["rows"]) == 2
    assert not {r["id"] for r in result["rows"]} & {r["id"] for r in second["rows"]}
    found = client.get(base + "?q=Cone").json["rows"]
    assert [(r["id"], r["available"], r["present"]) for r in found] == [(items[0], 6, 4)]
    assert client.get(base + "?q=Plastic").json["total"] == 0
    assert client.get(base + "?q=does-not-exist").json["rows"] == []


def test_existing_quantity_is_added_with_history_and_durable_replay(app):
    items, boxes, _ = seed(app)
    client = authenticated(app)
    op = transfer(boxes[0], items[0], 4, destination=boxes[0])
    client.post(op[0], data=op[1])
    request_token = token(client, boxes[0])
    op = operation(boxes[0], request_token, [(items[0], 2), (items[1], 3)])
    result = send(client, op)
    assert result.status_code == 200 and result.json["ok"]
    assert (result.json["reference_count"], result.json["unit_count"], result.json["replayed"]) == (2, 5, False)
    with app.app_context():
        assert present(boxes[0], items[0]) == 6
        assert db.session.get(Item, items[0]).quantity == db.session.get(Item, items[0]).total_quantity == 10
        records = BoxEvent.query.order_by(BoxEvent.id).all()[-2:]
        assert [(r.item_id, r.quantity, r.source_box_id, r.destination_box_id, r.user_id) for r in records] == [(items[0], 2, None, boxes[0], 1), (items[1], 3, None, boxes[0], 1)]
        assert all(r.item_name_snapshot and r.source_name_snapshot and r.destination_name_snapshot and r.created_at for r in records)
    assert client.get(f"/boxes/{boxes[0]}/material-options?q=Cone").json["rows"][0]["available"] == 4
    borrowing = borrow(items[0], 4)
    assert client.post(borrowing[0], data=borrowing[1]).status_code == 302
    before = state(app)
    replay = send(authenticated(app), operation(boxes[0], request_token, [(items[1], 3), (items[0], "02")]))
    assert replay.status_code == 200 and replay.json["replayed"]
    assert state(app) == before
    mismatch = send(client, operation(boxes[0], request_token, [(items[0], 1)]))
    assert mismatch.status_code == 409 and state(app) == before
    assert client.get(f"/boxes/{boxes[0]}/material-options?q=Cone").json["rows"] == []
    invariants(app)


@pytest.mark.parametrize("raw", ["", "0", "-1", "1.5", "1e2", " 2", "+2", "1000001", "999999999999999999999999999999999999999999", "٢"])
def test_invalid_quantity_preserved_without_consuming_token(app, raw):
    items, boxes, _ = seed(app)
    client = authenticated(app)
    request_token = token(client, boxes[0])
    before = state(app)
    response = send(client, operation(boxes[0], request_token, [(items[0], raw), (items[1], 1)]))
    assert response.status_code == 400
    assert response.json["rows"][0]["quantity"] == raw
    assert response.json["rows"][0]["error"]
    assert state(app) == before
    assert send(client, operation(boxes[0], request_token, [(items[0], 1)])).status_code == 200


@pytest.mark.parametrize("ident", ["0", "-1", "01", "abc", "9223372036854775808", "999999"])
def test_invalid_identifier_no_mutation(app, ident):
    items, boxes, _ = seed(app)
    client = authenticated(app)
    before = state(app)
    response = send(client, operation(boxes[0], token(client, boxes[0]), [(ident, 1), (items[0], 1)]))
    assert response.status_code == 400 and response.json["rows"][0]["error"]
    assert state(app) == before


def test_empty_duplicate_tampered_and_cross_scope_tokens(app):
    items, boxes, member = seed(app)
    client = authenticated(app)
    request_token = token(client, boxes[0])
    with app.app_context():
        db.session.get(User, member).role = "admin"
        db.session.commit()
    before = state(app)
    for actor, box, value, amounts in [(client, boxes[0], request_token, []), (client, boxes[0], request_token, [(items[0], 1), (items[0], 1)]), (client, boxes[0], request_token + "x", [(items[0], 1)]), (client, boxes[0], "", [(items[0], 1)]), (client, boxes[1], request_token, [(items[0], 1)]), (authenticated(app, member), boxes[0], request_token, [(items[0], 1)])]:
        assert send(actor, operation(box, value, amounts)).status_code == 400
        assert state(app) == before


@pytest.mark.parametrize("change", [{"user": True}, {"box": True}, {"id": 123}, {"id": "invalid-uuid"}, {"user": "1"}])
def test_adversarial_signed_token_payload_types(app, change):
    items, boxes, _ = seed(app)
    payload = dict(id=str(uuid.uuid4()), user=1, box=boxes[0])
    payload.update(change)
    signed = URLSafeTimedSerializer(app.secret_key, salt="box-fill-v1").dumps(payload)
    before = state(app)
    assert send(authenticated(app), operation(boxes[0], signed, [(items[0], 1)])).status_code == 400
    assert state(app) == before


def test_missing_and_deactivated_destination(app):
    items, boxes, _ = seed(app)
    client = authenticated(app)
    request_token = token(client, boxes[0])
    with app.app_context():
        db.session.get(StorageBox, boxes[0]).is_active = False
        db.session.commit()
    before = state(app)
    assert client.get(f"/boxes/{boxes[0]}/add-material").status_code == 404
    assert client.get(f"/boxes/{boxes[0]}/material-options").status_code == 404
    assert send(client, operation(boxes[0], request_token, [(items[0], 1)])).status_code == 409
    assert send(client, operation(999999, request_token, [(items[0], 1)])).status_code == 404
    assert state(app) == before


def test_successful_receipt_still_replays_when_destination_is_deactivated(app):
    items, boxes, _ = seed(app)
    client = authenticated(app)
    op = operation(boxes[0], token(client, boxes[0]), [(items[0], 2)])
    assert send(client, op).status_code == 200
    assert client.post(f"/boxes/{boxes[0]}/delete").status_code == 302
    before = state(app)
    response = send(authenticated(app), op)
    assert response.status_code == 200 and response.json["replayed"]
    assert state(app) == before
    invariants(app)


def test_stale_and_deleted_selection_preserves_raw_rows_and_is_atomic(app):
    items, boxes, _ = seed(app)
    client = authenticated(app)
    request_token = token(client, boxes[0])
    op = borrow(items[0], 8)
    assert client.post(op[0], data=op[1]).status_code == 302
    assert client.post(f"/items/{items[1]}/delete").status_code == 302
    before = state(app)
    response = send(client, operation(boxes[0], request_token, [(items[0], "04"), (items[1], "3")]))
    assert response.status_code == 400
    rows = response.json["rows"]
    assert [(r["quantity"], r["available"]) for r in rows] == [("04", 2), ("3", 0)]
    assert rows[1]["name"] == f"Remembered item {items[1]}"
    assert all(r["error"] for r in rows)
    assert state(app) == before
    assert send(client, operation(boxes[0], request_token, [(items[0], 2)])).status_code == 200


def test_sql_failure_after_first_allocation_rolls_back_everything(app):
    items, boxes, _ = seed(app)
    client = authenticated(app)
    op = operation(boxes[0], token(client, boxes[0]), [(items[0], 1), (items[1], 1)])
    before = state(app)
    with app.app_context():
        engine = db.engine
    observed = []
    def fail(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("INSERT INTO BOX_CONTENTS"):
            observed.append(statement)
            if len(observed) == 1:
                raise RuntimeError("fictitious failure after allocation SQL")
    event.listen(engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="fictitious failure"):
            send(client, op)
    finally:
        event.remove(engine, "after_cursor_execute", fail)
    assert observed and state(app) == before
    assert send(client, op).status_code == 200


@pytest.mark.parametrize("competitor", ["borrow", "transfer", "duplicate", "revoke", "borrow-first", "transfer-first"])
def test_fill_competes_on_independent_connections(app, competitor):
    items, boxes, member = seed(app)
    client = authenticated(app)
    fill = operation(boxes[0], token(client, boxes[0]), [(items[0], 7)])
    if competitor.endswith("-first"):
        first = borrow(items[0], 7) if competitor == "borrow-first" else transfer(boxes[1], items[0], 7, destination=boxes[1])
        assert pair(app, first, fill) == [302, 400]
        with app.app_context():
            assert present(boxes[0], items[0]) == 0
            assert BoxFillReceipt.query.count() == 0
    elif competitor == "revoke":
        with app.app_context():
            db.session.get(User, member).role = "admin"
            db.session.commit()
        results = pair(app, ("/admin/members/1/role", {"role": "member"}), fill, users=(member, 1))
        assert results[1] == 403
        with app.app_context():
            assert present(boxes[0], items[0]) == 0
    else:
        other = {"borrow": borrow(items[0], 7), "transfer": transfer(boxes[1], items[0], 7, destination=boxes[1]), "duplicate": fill}[competitor]
        results = pair(app, fill, other)
        assert results[0] == 302
        with app.app_context():
            assert present(boxes[0], items[0]) == 7
            assert BoxFillReceipt.query.count() == 1
            assert BoxEvent.query.count() == 1
        if competitor == "duplicate":
            assert results == [302, 302]
    invariants(app)

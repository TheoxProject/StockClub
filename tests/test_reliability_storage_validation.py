from io import BytesIO
from pathlib import Path
import csv

import pytest
from PIL import Image
from sqlalchemy import event
from sqlalchemy.orm import Session

from app import storage
from app.extensions import db
from app.models import Borrowing, BorrowingEvent, Category, Item, StockMovement, User


OLD = "items/" + "a" * 32 + ".webp"
ORPHAN = "items/" + "b" * 32 + ".webp"


def image():
    result = BytesIO()
    Image.new("RGB", (16, 16), "green").save(result, "PNG")
    result.seek(0)
    return result


def form(category_id, **changes):
    result = dict(name="Fictitious new", category_id=str(category_id), quantity="5",
                  condition="Bon état", location="Fictitious", description="Test only",
                  photo=(image(), "fictitious.png"))
    result.update(changes)
    return result


def seed(app):
    with app.app_context():
        db.session.get(User, 1).role = "admin"
        category = Category(name="Fictitious category")
        item = Item(name="Fictitious existing", category=category, quantity=5, photo_key=OLD)
        db.session.add(item)
        db.session.commit()
        return item.id, category.id


@pytest.fixture(params=["local", "s3-fake"])
def backend(request, app, monkeypatch):
    objects = {}
    state = dict(write_fail=False, cleanup_fail=False)
    root = Path(app.config["UPLOAD_FOLDER"])

    class FakeS3:
        def generate_presigned_url(self, operation, **kw):
            assert operation == "get_object"
            return "https://fictitious.invalid/signed"

        def put_object(self, **kw):
            objects[kw["Key"]] = kw["Body"]
            if state["write_fail"]:
                raise OSError("fictitious ambiguous storage failure")

        def delete_object(self, **kw):
            if state["cleanup_fail"]:
                raise OSError("fictitious cleanup failure")
            objects.pop(kw["Key"], None)

        def get_paginator(self, operation):
            assert operation == "list_objects_v2"
            return self

        def paginate(self, **kw):
            assert kw["Prefix"] == "items/"
            keys = list(objects)
            # Two pages ensure reconciliation does not inspect the first page only.
            yield {"Contents": [{"Key": k} for k in keys[:1]]}
            yield {"Contents": [{"Key": k} for k in keys[1:]]}

    if request.param == "s3-fake":
        app.config.update(S3_BUCKET="fictitious", S3_ENDPOINT_URL="https://fictitious.invalid",
                          S3_ACCESS_KEY_ID="fake", S3_SECRET_ACCESS_KEY="fake")
        monkeypatch.setattr(storage, "_s3_client", lambda: FakeS3())
        put = lambda key: objects.__setitem__(key, b"fictitious")
        keys = lambda: set(objects)
    else:
        original_write, original_unlink = Path.write_bytes, Path.unlink

        def write(path, payload):
            result = original_write(path, payload)
            if state["write_fail"] and path.suffix == ".webp":
                raise OSError("fictitious ambiguous storage failure")
            return result

        def unlink(path, *args, **kw):
            if state["cleanup_fail"] and path.suffix == ".webp":
                raise OSError("fictitious cleanup failure")
            return original_unlink(path, *args, **kw)

        monkeypatch.setattr(Path, "write_bytes", write)
        monkeypatch.setattr(Path, "unlink", unlink)

        def put(key):
            path = root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fictitious")

        keys = lambda: {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    put(OLD)
    return state, put, keys


@pytest.mark.parametrize("operation", ["create", "replace", "delete"])
def test_sql_failure_preserves_database_and_original_photo(app, logged_in_client, backend, operation):
    _, _, keys = backend
    item_id, category_id = seed(app)
    raised = []

    def fail_once(session):
        if not raised:
            raised.append(True)
            session.flush()
            raise RuntimeError("fictitious SQL failure after flush")

    event.listen(Session, "before_commit", fail_once)
    try:
        route = "/items/new" if operation == "create" else f"/items/{item_id}/" + ("edit" if operation == "replace" else "delete")
        with pytest.raises(RuntimeError, match="fictitious SQL"):
            logged_in_client.post(route, data={} if operation == "delete" else form(category_id))
    finally:
        event.remove(Session, "before_commit", fail_once)
    with app.app_context():
        assert Item.query.count() == 1
        item = db.session.get(Item, item_id)
        assert item.photo_key == OLD and item.name == "Fictitious existing"
        assert StockMovement.query.count() == 0
    assert keys() == {OLD}


@pytest.mark.parametrize("operation", ["create", "replace"])
def test_ambiguous_storage_write_is_compensated(app, logged_in_client, backend, operation):
    state, _, keys = backend
    item_id, category_id = seed(app)
    state["write_fail"] = True
    route = "/items/new" if operation == "create" else f"/items/{item_id}/edit"
    assert logged_in_client.post(route, data=form(category_id)).status_code == 503
    with app.app_context():
        assert Item.query.count() == 1
        assert db.session.get(Item, item_id).photo_key == OLD
    assert keys() == {OLD}


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_cleanup_failure_is_recoverable_by_safe_reconciliation(app, logged_in_client, backend, operation):
    state, put, keys = backend
    item_id, category_id = seed(app)
    state["cleanup_fail"] = True
    route = f"/items/{item_id}/" + ("edit" if operation == "replace" else "delete")
    assert logged_in_client.post(route, data=form(category_id) if operation == "replace" else {}).status_code == 302
    assert OLD in keys()
    state["cleanup_fail"] = False
    put(ORPHAN)
    put("items/unmanaged.webp")
    put("keep.txt")
    before = keys()
    runner = app.test_cli_runner()
    dry = runner.invoke(args=["reconcile-photos"])
    assert dry.exit_code == 0, dry.output
    assert keys() == before
    applied = runner.invoke(args=["reconcile-photos", "--apply"])
    assert applied.exit_code == 0, applied.output
    assert OLD not in keys() and ORPHAN not in keys()
    assert {"items/unmanaged.webp", "keep.txt"} <= keys()
    with app.app_context():
        if operation == "replace":
            assert db.session.get(Item, item_id).photo_key in keys()
        else:
            assert db.session.get(Item, item_id) is None


def test_shared_photo_reference_survives_replacement_and_deletion(app, logged_in_client, backend):
    _, _, keys = backend
    item_id, category_id = seed(app)
    with app.app_context():
        other = Item(name="Shared fictitious", category_id=category_id, quantity=0, photo_key=OLD)
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    assert logged_in_client.post(f"/items/{item_id}/edit", data=form(category_id)).status_code == 302
    assert OLD in keys()
    with app.app_context():
        another = Item(name="Shared again", category_id=category_id, quantity=0, photo_key=OLD)
        db.session.add(another)
        db.session.commit()
    assert logged_in_client.post(f"/items/{other_id}/delete").status_code == 302
    assert OLD in keys()


def test_reconciliation_failure_returns_nonzero_and_preserves_referenced_photo(app, backend, caplog):
    state, put, keys = backend
    seed(app)
    put(ORPHAN)
    state["cleanup_fail"] = True
    failed = app.test_cli_runner().invoke(args=["reconcile-photos", "--apply"])
    assert failed.exit_code != 0
    assert ORPHAN in failed.output
    assert keys() == {OLD, ORPHAN}
    assert "fictitious cleanup failure" in caplog.text
    state["cleanup_fail"] = False
    retried = app.test_cli_runner().invoke(args=["reconcile-photos", "--apply"])
    assert retried.exit_code == 0, retried.output
    assert keys() == {OLD}


def test_failed_creation_and_failed_compensation_can_be_reconciled(app, logged_in_client, backend, caplog):
    state, _, keys = backend
    _, category_id = seed(app)
    state["cleanup_fail"] = True
    failed = []

    def fail_once(session):
        if not failed:
            failed.append(True)
            session.flush()
            raise RuntimeError("fictitious SQL failure")

    event.listen(Session, "before_commit", fail_once)
    try:
        with pytest.raises(RuntimeError, match="fictitious SQL"):
            logged_in_client.post("/items/new", data=form(category_id))
    finally:
        event.remove(Session, "before_commit", fail_once)
    with app.app_context():
        assert Item.query.count() == 1
        assert StockMovement.query.count() == 0
    assert len(keys()) == 2 and OLD in keys()
    assert "fictitious cleanup failure" in caplog.text
    state["cleanup_fail"] = False
    result = app.test_cli_runner().invoke(args=["reconcile-photos", "--apply"])
    assert result.exit_code == 0, result.output
    assert keys() == {OLD}


@pytest.mark.parametrize("quantity", ["-1", "1.5", "abc", str(2**100), "1000001"])
def test_invalid_initial_quantity_rejected_before_photo_write(app, logged_in_client, monkeypatch, quantity):
    _, category_id = seed(app)
    def forbidden(*args, **kw):
        pytest.fail("invalid quantity reached photo storage")
    monkeypatch.setattr("app.inventory.routes.save_photo", forbidden)
    assert logged_in_client.post("/items/new", data=form(category_id, quantity=quantity)).status_code == 400
    with app.app_context():
        assert Item.query.count() == 1 and StockMovement.query.count() == 0


@pytest.mark.parametrize("quantity", ["0", "1000000"])
def test_initial_quantity_boundaries(app, logged_in_client, quantity):
    _, category_id = seed(app)
    assert logged_in_client.post("/items/new", data=form(category_id, quantity=quantity)).status_code == 302
    with app.app_context():
        item = Item.query.filter_by(name="Fictitious new").one()
        assert item.quantity == int(quantity)
        assert StockMovement.query.filter_by(item_id=item.id).one().delta == int(quantity)


def test_return_cannot_overflow_available_quantity(app, logged_in_client):
    item_id, _ = seed(app)
    assert logged_in_client.post(f"/items/{item_id}/adjust-stock", data={"quantity": "1"}).status_code == 302
    with app.app_context():
        db.session.get(Item, item_id).quantity = 1000000
        loan_id = Borrowing.query.one().id
        db.session.commit()
    assert logged_in_client.post(f"/loans/{loan_id}/return", data={"quantity": "1"}).status_code == 302
    with app.app_context():
        assert db.session.get(Item, item_id).quantity == 1000000
        assert db.session.get(Borrowing, loan_id).quantity_returned == 0
        assert BorrowingEvent.query.count() == StockMovement.query.count() == 1


@pytest.mark.parametrize("value", ["=1+1", "+1+1", "-1+1", "@SUM(1;1)", "\t=1+1", "  =1+1"])
def test_csv_formula_values_are_neutralized_in_all_text_columns(app, logged_in_client, value):
    item_id, category_id = seed(app)
    with app.app_context():
        item = db.session.get(Item, item_id)
        item.name = item.description = item.location = item.condition = item.unit = value
        db.session.get(Category, category_id).name = value
        db.session.commit()
    response = logged_in_client.get("/items.csv")
    assert response.status_code == 200
    rows = list(csv.reader(BytesIO(response.data).getvalue().decode("utf-8-sig").splitlines(), delimiter=";"))
    cells = rows[1]
    assert value not in cells
    assert [cells[index] for index in (0, 1, 2, 4, 5, 6)] == ["'" + value] * 6


def test_csv_ordinary_text_and_numeric_values_stay_readable(app, logged_in_client):
    seed(app)
    rows = list(csv.reader(logged_in_client.get("/items.csv").data.decode("utf-8-sig").splitlines(), delimiter=";"))
    assert "Fictitious existing" in rows[1]
    assert "Fictitious category" in rows[1]
    assert "5" in rows[1]

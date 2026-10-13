from io import BytesIO

from PIL import Image

from app.extensions import db
from app.models import Category, Item, StockMovement


def make_image():
    image = Image.new("RGB", (32, 32), color=(80, 130, 100))
    data = BytesIO()
    image.save(data, format="PNG")
    data.seek(0)
    return data


def test_inventory_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_login_invalid_password(client):
    response = client.post("/auth/login", data={
        "login": "benevole-test", "password": "999999",
    })
    assert response.status_code == 200
    assert "incorrect" in response.get_data(as_text=True).lower()


def test_login_with_password_and_case_insensitive_login(client):
    response = client.post("/auth/login", data={
        "login": "BENEVOLE-TEST",
        "password": "123456",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_login_locks_after_five_failed_attempts(client, app):
    for _ in range(5):
        response = client.post("/auth/login", data={
            "login": "benevole-test",
            "password": "999999",
        })
        assert response.status_code == 200

    with app.app_context():
        from app.models import User
        user = User.query.filter_by(login="benevole-test").one()
        assert user.failed_login_attempts == 5
        assert user.locked_until is not None

    response = client.post("/auth/login", data={
        "login": "benevole-test",
        "password": "123456",
    })
    assert response.status_code == 200
    assert "quelques minutes" in response.get_data(as_text=True)


def test_create_category_and_inventory_item(logged_in_client, app):
    response = logged_in_client.post("/categories", data={"name": "Signalétique", "parent_id": ""}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        category = Category.query.filter_by(name="Signalétique").one()
        category_id = category.id

    response = logged_in_client.post("/items/new", data={
        "name": "Panneau arrivée 1 km",
        "description": "Panneau de signalisation de course.",
        "category_id": str(category_id),
        "quantity": "6",
        "unit": "lot",
        "location": "Local principal",
        "condition": "Bon état",
        "photo": (make_image(), "panneau.png"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert "Panneau arrivée 1 km" in response.get_data(as_text=True)
    with app.app_context():
        item = Item.query.filter_by(name="Panneau arrivée 1 km").one()
        assert item.quantity == 6
        assert item.unit == "unité"
        assert item.photo_key.endswith(".webp")
        assert StockMovement.query.filter_by(item_id=item.id).one().quantity_after == 6


def test_stock_adjustment_has_audit_record(logged_in_client, app):
    with app.app_context():
        category = Category(name="Signalétique")
        db.session.add(category)
        db.session.flush()
        from app.models import User
        user = User.query.filter_by(login="benevole-test").one()
        item = Item(name="Cône", category=category, quantity=10, unit="unité", condition="Bon état", photo_key="items/example.webp", created_by=user, updated_by=user)
        db.session.add(item)
        db.session.flush()
        item_id = item.id
        db.session.add(StockMovement(item=item, user=user, delta=10, quantity_after=10, note="Stock initial"))
        db.session.commit()

    response = logged_in_client.post(f"/items/{item_id}/adjust-stock", data={"delta": "-2", "note": "Deux cônes abîmés"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 8
        movement = StockMovement.query.filter_by(item_id=item_id).order_by(StockMovement.id.desc()).first()
        assert movement.delta == -2
        assert movement.quantity_after == 8
        assert movement.note == "Deux cônes abîmés"


def test_borrow_and_return_movements(logged_in_client, app):
    with app.app_context():
        category = Category(name="Signalétique")
        db.session.add(category)
        db.session.flush()
        from app.models import User
        user = User.query.filter_by(login="benevole-test").one()
        item = Item(name="Panneau", category=category, quantity=5, unit="unité", condition="Bon état", photo_key="items/example.webp", created_by=user, updated_by=user)
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = logged_in_client.post(
        f"/items/{item_id}/adjust-stock",
        data={"movement_type": "emprunt", "quantity": "2", "note": "Course du club"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 3
        movement = StockMovement.query.filter_by(item_id=item_id).order_by(StockMovement.id.desc()).first()
        assert movement.delta == -2
        assert movement.note == "Emprunt · Course du club"

    response = logged_in_client.post(
        f"/items/{item_id}/adjust-stock",
        data={"movement_type": "retour", "quantity": "4", "note": "Retour au local"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 7
        movement = StockMovement.query.filter_by(item_id=item_id).order_by(StockMovement.id.desc()).first()
        assert movement.delta == 4
        assert movement.note == "Retour · Retour au local"


def test_stock_cannot_become_negative(logged_in_client, app):
    with app.app_context():
        category = Category(name="Signalétique")
        db.session.add(category)
        db.session.flush()
        from app.models import User
        user = User.query.filter_by(login="benevole-test").one()
        item = Item(name="Barrière", category=category, quantity=1, unit="unité", condition="Bon état", photo_key="items/example.webp", created_by=user, updated_by=user)
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = logged_in_client.post(f"/items/{item_id}/adjust-stock", data={"delta": "-3", "note": "Erreur"}, follow_redirects=True)
    assert response.status_code == 200
    assert "ne peut pas devenir négatif" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Item, item_id).quantity == 1


def test_category_tree_supports_many_levels_and_selected_parent(logged_in_client, app):
    parent_id = ""
    names = ["Matériel", "Signalétique", "Panneaux", "Distances", "Panneau arrivée 1 km"]
    for name in names:
        response = logged_in_client.post(
            "/categories",
            data={"name": name, "parent_id": parent_id},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            category = Category.query.filter_by(name=name).one()
            parent_id = str(category.id)

    response = logged_in_client.get(f"/categories?parent_id={parent_id}")
    assert response.status_code == 200
    assert b'value="' + parent_id.encode() + b'" selected' in response.data
    assert "Panneau arrivée 1 km" in response.get_data(as_text=True)

    with app.app_context():
        category = Category.query.filter_by(name="Panneau arrivée 1 km").one()
        assert category.full_path == "Matériel › Signalétique › Panneaux › Distances › Panneau arrivée 1 km"


def test_category_can_be_renamed_and_moved_without_moving_out_its_children(logged_in_client, app):
    with app.app_context():
        root_a = Category(name="Cat A")
        root_b = Category(name="Cat B")
        db.session.add_all([root_a, root_b])
        db.session.flush()
        child = Category(name="Sous-cat aa", parent=root_a)
        db.session.add(child)
        db.session.flush()
        grandchild = Category(name="Branche aaa", parent=child)
        db.session.add(grandchild)
        db.session.commit()
        root_b_id, child_id, grandchild_id = root_b.id, child.id, grandchild.id

    response = logged_in_client.post(
        f"/categories/{child_id}/edit",
        data={"name": "Sous-cat renommée", "parent_id": str(root_b_id)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        child = db.session.get(Category, child_id)
        grandchild = db.session.get(Category, grandchild_id)
        assert child.full_path == "Cat B › Sous-cat renommée"
        assert grandchild.full_path == "Cat B › Sous-cat renommée › Branche aaa"


def test_category_cannot_be_moved_under_its_own_descendant(logged_in_client, app):
    with app.app_context():
        root = Category(name="Racine")
        db.session.add(root)
        db.session.flush()
        child = Category(name="Enfant", parent=root)
        db.session.add(child)
        db.session.commit()
        root_id, child_id = root.id, child.id

    response = logged_in_client.post(
        f"/categories/{root_id}/edit",
        data={"name": "Racine", "parent_id": str(child_id)},
    )
    assert response.status_code == 200
    assert "ne peut pas être placée sous elle-même" in response.get_data(as_text=True)
    with app.app_context():
        root = db.session.get(Category, root_id)
        child = db.session.get(Category, child_id)
        assert root.parent_id is None
        assert child.parent_id == root_id


def test_empty_categories_can_be_deleted_but_nonempty_ones_are_protected(logged_in_client, app):
    with app.app_context():
        empty = Category(name="À supprimer")
        used = Category(name="À conserver")
        db.session.add_all([empty, used])
        db.session.flush()
        from app.models import User
        user = User.query.filter_by(login="benevole-test").one()
        item = Item(
            name="Cône", category=used, quantity=3, unit="unité", condition="Bon état",
            photo_key="items/example.webp", created_by=user, updated_by=user,
        )
        db.session.add(item)
        db.session.commit()
        empty_id, used_id = empty.id, used.id

    response = logged_in_client.post(f"/categories/{empty_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Category, empty_id) is None

    response = logged_in_client.post(f"/categories/{used_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "n’est pas vide" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Category, used_id) is not None


def test_photo_input_remains_visible_on_mobile(logged_in_client, app):
    with app.app_context():
        db.session.add(Category(name="Matériel"))
        db.session.commit()
    page = logged_in_client.get("/items/new").get_data(as_text=True)
    assert 'class="photo-native-input"' in page
    assert 'accept="image/*"' in page
    assert 'id="form-feedback"' in page

def test_password_may_contain_letters(client, app):
    with app.app_context():
        from app.models import User
        u = User.query.filter_by(login="benevole-test").one()
        u.set_password("TestABC123!")
        db.session.commit()
    response = client.post("/auth/login", data={"login":"benevole-test", "password":"TestABC123!"})
    assert response.status_code == 302

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
    from app.models import Borrowing, User
    with app.app_context():
        category = Category(name="Signalétique")
        db.session.add(category)
        db.session.flush()
        user = User.query.filter_by(login="benevole-test").one()
        item = Item(name="Cône", category=category, quantity=10, unit="unité", condition="Bon état", photo_key="items/example.webp", created_by=user, updated_by=user)
        db.session.add(item)
        db.session.flush()
        item_id = item.id
        db.session.add(StockMovement(item=item, user=user, delta=10, quantity_after=10, note="Stock initial"))
        db.session.commit()

    response = logged_in_client.post(f"/items/{item_id}/adjust-stock", data={"movement_type": "emprunt", "quantity": "2", "note": "Sortie de test"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 8
        movement = StockMovement.query.filter_by(item_id=item_id).order_by(StockMovement.id.desc()).first()
        loan = Borrowing.query.filter_by(item_id=item_id).one()
        assert movement.delta == -2
        assert movement.quantity_after == 8
        assert movement.note == "Emprunt · Sortie de test"
        assert movement.item_name_snapshot == "Cône"
        assert loan.quantity_out == 2
        assert loan.remaining_quantity == 2
        assert loan.user.name == "benevole-test"


def test_borrow_partial_and_full_return_preserve_history(logged_in_client, app):
    from app.models import Borrowing, BorrowingEvent, User
    with app.app_context():
        category = Category(name="Signalétique")
        db.session.add(category)
        db.session.flush()
        user = User.query.filter_by(login="benevole-test").one()
        item = Item(name="Panneau", category=category, quantity=5, unit="unité", condition="Bon état", photo_key="items/example.webp", created_by=user, updated_by=user)
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = logged_in_client.post(
        f"/items/{item_id}/adjust-stock",
        data={"movement_type": "emprunt", "quantity": "3", "note": "Course du club"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        item = db.session.get(Item, item_id)
        loan = Borrowing.query.filter_by(item_id=item_id).one()
        loan_id = loan.id
        assert item.quantity == 2
        assert loan.remaining_quantity == 3
        assert loan.user.name == "benevole-test"
        assert [(event.event_type, event.quantity) for event in loan.events] == [("sortie", 3)]

    # A partial return increases the available stock and leaves 2 units outside.
    response = logged_in_client.post(f"/loans/{loan_id}/return", data={"quantity": "1", "note": "Retour partiel"}, follow_redirects=True)
    assert response.status_code == 200
    assert "reste 2 unité(s) à l’extérieur" in response.get_data(as_text=True)
    with app.app_context():
        item = db.session.get(Item, item_id)
        loan = db.session.get(Borrowing, loan_id)
        assert item.quantity == 3
        assert loan.remaining_quantity == 2
        assert loan.closed_at is None

    response = logged_in_client.post(f"/loans/{loan_id}/return", data={"quantity": "2", "note": "Retour final"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        item = db.session.get(Item, item_id)
        loan = db.session.get(Borrowing, loan_id)
        assert item.quantity == 5
        assert loan.remaining_quantity == 0
        assert loan.closed_at is not None
        assert [(event.event_type, event.quantity) for event in loan.events] == [("sortie", 3), ("retour", 1), ("retour", 2)]
        assert BorrowingEvent.query.filter_by(borrowing_id=loan_id).count() == 3

    history = logged_in_client.get("/loans")
    assert history.status_code == 200
    assert "Historique des sorties et retours" in history.get_data(as_text=True)
    assert "benevole-test" in history.get_data(as_text=True)


def test_cannot_return_more_than_still_out(logged_in_client, app):
    from app.models import Borrowing, User
    with app.app_context():
        category = Category(name="Matériel")
        db.session.add(category)
        db.session.flush()
        user = User.query.filter_by(login="benevole-test").one()
        item = Item(name="Barrière", category=category, quantity=1, unit="unité", condition="Bon état", photo_key="items/example.webp", created_by=user, updated_by=user)
        db.session.add(item)
        db.session.commit()
        item_id = item.id
    logged_in_client.post(f"/items/{item_id}/adjust-stock", data={"movement_type": "emprunt", "quantity": "1"})
    with app.app_context():
        loan_id = Borrowing.query.filter_by(item_id=item_id).one().id
    response = logged_in_client.post(f"/loans/{loan_id}/return", data={"quantity": "2"}, follow_redirects=True)
    assert response.status_code == 200
    assert "Tu ne peux pas rendre plus" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Item, item_id).quantity == 0
        assert db.session.get(Borrowing, loan_id).remaining_quantity == 1


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

    response = logged_in_client.post(f"/items/{item_id}/adjust-stock", data={"movement_type": "emprunt", "quantity": "3", "note": "Erreur"}, follow_redirects=True)
    assert response.status_code == 200
    assert "quantité disponible est insuffisante" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Item, item_id).quantity == 1


def test_search_is_ajax_and_filters_only_category_availability_and_quantity(logged_in_client, app):
    with app.app_context():
        cat = Category(name="Signalétique")
        db.session.add(cat)
        db.session.flush()
        item = Item(name="Cône orange", description="Cônes pour entraînement", category=cat, quantity=4, unit="unité", location="Local à l’est", condition="Bon état", photo_key="items/example.webp")
        db.session.add(item)
        db.session.commit()
        item_id = item.id
    response = logged_in_client.get("/?q=orange", headers={"X-Requested-With": "XMLHttpRequest"})
    assert response.status_code == 200
    assert "Cône orange" in response.get_data(as_text=True)
    response = logged_in_client.get("/?q=Local%20%C3%A0%20l%E2%80%99est", headers={"X-Requested-With": "XMLHttpRequest"})
    assert "Cône orange" not in response.get_data(as_text=True)
    response = logged_in_client.get("/?quantity_min=5", headers={"X-Requested-With": "XMLHttpRequest"})
    assert "Cône orange" not in response.get_data(as_text=True)
    page = logged_in_client.get("/").get_data(as_text=True)
    assert 'name="availability"' in page and 'name="quantity_min"' in page
    assert 'name="condition"' not in page
    assert 'name="location"' not in page


def test_admin_only_item_deletion_preserves_history(logged_in_client, app):
    from app.models import Borrowing, BorrowingEvent, User
    with app.app_context():
        user = User.query.filter_by(login="benevole-test").one()
        cat = Category(name="Matériel")
        db.session.add(cat)
        db.session.flush()
        item = Item(name="Ancien panneau", category=cat, quantity=0, unit="unité", condition="Bon état", photo_key="items/example.webp", created_by=user, updated_by=user)
        db.session.add(item)
        db.session.flush()
        db.session.add(StockMovement(item=item, item_name_snapshot=item.name, user=user, delta=3, quantity_after=3, note="Stock initial"))
        loan = Borrowing(item=item, item_name_snapshot=item.name, user=user, quantity_out=2, quantity_returned=2, note="Emprunt clôturé")
        db.session.add(loan)
        db.session.flush()
        db.session.add(BorrowingEvent(borrowing=loan, item=item, item_name_snapshot=item.name, user=user, event_type="sortie", quantity=2, note="Sortie"))
        db.session.add(BorrowingEvent(borrowing=loan, item=item, item_name_snapshot=item.name, user=user, event_type="retour", quantity=2, note="Retour"))
        db.session.commit()
        item_id = item.id
        loan_id = loan.id
        user.role = "member"
        db.session.commit()

    denied = logged_in_client.post(f"/items/{item_id}/delete")
    assert denied.status_code == 403
    with app.app_context():
        assert db.session.get(Item, item_id) is not None
        User.query.filter_by(login="benevole-test").one().role = "admin"
        db.session.commit()

    response = logged_in_client.get(f"/items/{item_id}")
    assert "Supprimer l’objet" in response.get_data(as_text=True)
    response = logged_in_client.post(f"/items/{item_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "historique des mouvements et emprunts est conservé" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Item, item_id) is None
        movement = StockMovement.query.filter_by(item_name_snapshot="Ancien panneau").one()
        assert movement.item_id is None
        historical_loan = db.session.get(Borrowing, loan_id)
        assert historical_loan.item_id is None
        assert historical_loan.item_name_snapshot == "Ancien panneau"
        assert BorrowingEvent.query.filter_by(borrowing_id=loan_id).count() == 2
        assert BorrowingEvent.query.filter_by(item_name_snapshot="Ancien panneau").count() == 2


def test_admin_cannot_delete_item_with_active_loan(logged_in_client, app):
    from app.models import Borrowing, User
    with app.app_context():
        user = User.query.filter_by(login="benevole-test").one()
        user.role = "admin"
        cat = Category(name="Matériel")
        db.session.add(cat)
        db.session.flush()
        item = Item(name="Barrière", category=cat, quantity=0, unit="unité", condition="Bon état", photo_key="items/example.webp", created_by=user, updated_by=user)
        db.session.add(item)
        db.session.flush()
        loan = Borrowing(item=item, item_name_snapshot=item.name, user=user, quantity_out=1, quantity_returned=0)
        db.session.add(loan)
        db.session.commit()
        item_id = item.id
    response = logged_in_client.post(f"/items/{item_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "du matériel est encore à l’extérieur" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Item, item_id) is not None


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


def test_create_item_accepts_stable_camera_and_gallery_photo_fields(logged_in_client, app):
    response = logged_in_client.post(
        "/categories",
        data={"name": "Matériel photo", "parent_id": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        category_id = Category.query.filter_by(name="Matériel photo").one().id

    camera_response = logged_in_client.post(
        "/items/new",
        data={
            "name": "Objet caméra",
            "description": "Capture depuis le champ caméra.",
            "category_id": str(category_id),
            "quantity": "1",
            "location": "Local",
            "condition": "Bon état",
            "photo_camera": (make_image(), "camera.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert camera_response.status_code == 200

    gallery_response = logged_in_client.post(
        "/items/new",
        data={
            "name": "Objet galerie",
            "description": "Capture depuis le champ galerie.",
            "category_id": str(category_id),
            "quantity": "1",
            "location": "Local",
            "condition": "Bon état",
            "photo_gallery": (make_image(), "galerie.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert gallery_response.status_code == 200

    with app.app_context():
        camera_item = Item.query.filter_by(name="Objet caméra").one()
        gallery_item = Item.query.filter_by(name="Objet galerie").one()
        assert camera_item.photo_key and camera_item.photo_key.endswith(".webp")
        assert gallery_item.photo_key and gallery_item.photo_key.endswith(".webp")

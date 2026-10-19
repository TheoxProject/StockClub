import re

from app.extensions import db
from app.models import BootstrapLock, User


def _bootstrap_data(**overrides):
    data = {
        "name": "Camille Martin",
        "login": "camille.admin",
        "password": "MotDePasse123",
        "password_confirmation": "MotDePasse123",
    }
    data.update(overrides)
    return data


def test_login_offers_first_admin_only_when_users_table_is_empty(client, app):
    with app.app_context():
        User.query.delete(synchronize_session=False)
        db.session.commit()
        assert BootstrapLock.query.count() == 1

    response = client.get("/auth/login")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Créer le premier compte administrateur" in html

    response = client.get("/auth/first-admin")
    assert response.status_code == 200
    assert "Créer le premier administrateur" in response.get_data(as_text=True)


def test_first_admin_setup_creates_exactly_one_admin_and_closes_setup(client, app):
    with app.app_context():
        User.query.delete(synchronize_session=False)
        db.session.commit()

    response = client.post("/auth/first-admin", data=_bootstrap_data())
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")

    with app.app_context():
        users = User.query.all()
        assert len(users) == 1
        assert users[0].login == "camille.admin"
        assert users[0].name == "Camille Martin"
        assert users[0].role == "admin"
        assert users[0].check_password("MotDePasse123")

    # A new browser cannot see or reuse first-run setup once a user exists.
    another_browser = app.test_client()
    login_page = another_browser.get("/auth/login").get_data(as_text=True)
    assert "Créer le premier compte administrateur" not in login_page
    response = another_browser.get("/auth/first-admin")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")
    response = another_browser.post("/auth/first-admin", data=_bootstrap_data(login="second.admin"))
    assert response.status_code == 302
    with app.app_context():
        assert User.query.count() == 1


def test_first_admin_rejects_invalid_password(client, app):
    with app.app_context():
        User.query.delete(synchronize_session=False)
        db.session.commit()

    response = client.post(
        "/auth/first-admin",
        data=_bootstrap_data(password="abc", password_confirmation="abc"),
    )
    assert response.status_code == 400
    assert "au moins 6 caractères" in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == 0


def test_first_admin_setup_keeps_csrf_protection_enabled(app):
    with app.app_context():
        User.query.delete(synchronize_session=False)
        db.session.commit()
    app.config["WTF_CSRF_ENABLED"] = True
    browser = app.test_client()
    html = browser.get("/auth/first-admin").get_data(as_text=True)
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert token_match, html

    rejected = browser.post("/auth/first-admin", data=_bootstrap_data())
    assert rejected.status_code == 400
    with app.app_context():
        assert User.query.count() == 0

    accepted = browser.post(
        "/auth/first-admin",
        data=_bootstrap_data(csrf_token=token_match.group(1)),
    )
    assert accepted.status_code == 302
    with app.app_context():
        user = User.query.filter_by(login="camille.admin").one()
        assert user.role == "admin"


def test_invitation_cannot_bypass_first_admin_initialization(client, app):
    import hashlib
    from datetime import datetime, timedelta, timezone
    from app.models import Invitation

    with app.app_context():
        User.query.delete(synchronize_session=False)
        db.session.add(Invitation(
            token_hash=hashlib.sha256(b"residual-invite").hexdigest(),
            created_by_id=None,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        ))
        db.session.commit()

    response = client.get("/auth/register/residual-invite")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/first-admin")
    with app.app_context():
        assert User.query.count() == 0
        assert Invitation.query.one().used_at is None

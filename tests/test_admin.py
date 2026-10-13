from app.models import Invitation, User
from app.extensions import db


def _login(client, login, password="123456"):
    response = client.post("/auth/login", data={"login": login, "password": password})
    assert response.status_code == 302
    return client


def test_admin_can_see_member_panel_and_change_member_role(client, app):
    with app.app_context():
        existing = User.query.filter_by(login="benevole-test").one()
        existing.role = "admin"
        member = User(name="Membre Deux", login="membre2")
        member.set_password("123456")
        db.session.add(member)
        db.session.commit()
        member_id = member.id

    _login(client, "benevole-test")
    response = client.get("/admin/members")
    assert response.status_code == 200
    assert "Gestion des membres" in response.get_data(as_text=True)
    response = client.post(f"/admin/members/{member_id}/role", data={"role": "admin"})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(User, member_id).role == "admin"


def test_last_active_admin_cannot_be_demoted_or_deactivated(client, app):
    with app.app_context():
        admin = User.query.filter_by(login="benevole-test").one()
        admin.role = "admin"
        admin_id = admin.id
        member = User(name="Membre Deux", login="membre2")
        member.set_password("123456")
        db.session.add(member)
        db.session.commit()
    _login(client, "benevole-test")
    response = client.post(f"/admin/members/{admin_id}/role", data={"role": "member"}, follow_redirects=True)
    assert "retirer toi-même" in response.get_data(as_text=True)
    response = client.post(f"/admin/members/{admin_id}/toggle-active", follow_redirects=True)
    assert "ton propre compte" in response.get_data(as_text=True)
    with app.app_context():
        user = db.session.get(User, admin_id)
        assert user.role == "admin" and user.is_active_user


def test_admin_can_deactivate_member_and_member_cannot_log_in(client, app):
    with app.app_context():
        admin = User.query.filter_by(login="benevole-test").one()
        admin.role = "admin"
        member = User(name="Membre Deux", login="membre2")
        member.set_password("123456")
        db.session.add(member)
        db.session.commit()
        member_id = member.id

    _login(client, "benevole-test")
    response = client.post(f"/admin/members/{member_id}/toggle-active")
    assert response.status_code == 302
    with app.app_context():
        assert not db.session.get(User, member_id).is_active_user

    another = app.test_client()
    response = another.post("/auth/login", data={"login": "membre2", "password": "123456"}, follow_redirects=True)
    assert "Login ou mot de passe incorrect" in response.get_data(as_text=True)

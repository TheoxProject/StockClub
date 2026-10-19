import hashlib
import re

from app.extensions import db
from app.models import Invitation, User


def _registration_data(**overrides):
    data = {
        "name": "Camille Martin",
        "login": "camille.martin",
        "password": "MotDePasse123",
        "password_confirmation": "MotDePasse123",
    }
    data.update(overrides)
    return data


def _created_invitation_url(response):
    html = response.get_data(as_text=True)
    match = re.search(r'<textarea[^>]*id="invitation-url"[^>]*>(.*?)</textarea>', html, re.S)
    assert match, html
    return match.group(1).strip()


def test_public_registration_route_does_not_allow_shared_code_registration(client):
    response = client.get("/auth/register")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")


def test_member_cannot_create_invitations_or_open_admin_panel(logged_in_client):
    response = logged_in_client.get("/admin/members")
    assert response.status_code == 403
    response = logged_in_client.post("/admin/invitations")
    assert response.status_code == 403


def test_admin_can_create_one_use_invitation_and_new_user_becomes_member(client, app):
    with app.app_context():
        admin = User.query.filter_by(login="benevole-test").one()
        admin.role = "admin"
        db.session.commit()

    login_response = client.post("/auth/login", data={"login": "benevole-test", "password": "123456"})
    assert login_response.status_code == 302
    response = client.post("/admin/invitations")
    assert response.status_code == 200
    invite_url = _created_invitation_url(response)
    match = re.search(r"/auth/register/([^/?]+)$", invite_url)
    assert match, invite_url
    token = match.group(1)

    with app.app_context():
        invitation = Invitation.query.one()
        assert invitation.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in invitation.token_hash
        assert invitation.used_at is None

    # Use a separate browser session for the invitee, as the admin is still logged in.
    invitee_client = app.test_client()
    get_response = invitee_client.get(f"/auth/register/{token}")
    assert get_response.status_code == 200
    assert "Ce lien ne peut servir qu’une seule fois" in get_response.get_data(as_text=True)

    proof = re.search(r'name="invitation_form_proof" value="([^"]+)"', get_response.get_data(as_text=True)).group(1)
    create_response = invitee_client.post(f"/auth/register/{token}", data=_registration_data(invitation_form_proof=proof))
    assert create_response.status_code == 302
    assert create_response.headers["Location"].endswith("/auth/login")

    with app.app_context():
        member = User.query.filter_by(login="camille.martin").one()
        assert member.role == "member"
        assert member.check_password("MotDePasse123")
        assert Invitation.query.one().used_at is not None

    repeat = app.test_client().post(f"/auth/register/{token}", data=_registration_data(login="camille-2"))
    assert repeat.status_code == 410
    with app.app_context():
        assert User.query.filter_by(login="camille-2").first() is None


def test_invitation_rejects_invalid_password_and_remains_usable(client, app):
    with app.app_context():
        admin = User.query.filter_by(login="benevole-test").one()
        admin.role = "admin"
        db.session.commit()
    client.post("/auth/login", data={"login": "benevole-test", "password": "123456"})
    response = client.post("/admin/invitations")
    invite_url = _created_invitation_url(response)
    token = invite_url.rsplit("/", 1)[-1]
    invitee = app.test_client()
    html = invitee.get(f"/auth/register/{token}").get_data(as_text=True)
    proof = re.search(r'name="invitation_form_proof" value="([^"]+)"', html).group(1)
    response = invitee.post(f"/auth/register/{token}", data=_registration_data(password="abc", password_confirmation="abc", invitation_form_proof=proof))
    assert response.status_code == 200
    assert "au moins 6 caractères" in response.get_data(as_text=True)
    with app.app_context():
        assert Invitation.query.one().used_at is None


def test_registration_rejects_duplicate_login(client, app):
    with app.app_context():
        admin = User.query.filter_by(login="benevole-test").one()
        admin.role = "admin"
        db.session.commit()
    client.post("/auth/login", data={"login": "benevole-test", "password": "123456"})
    invite_url = _created_invitation_url(client.post("/admin/invitations"))
    token = invite_url.rsplit("/", 1)[-1]
    invitee = app.test_client()
    html = invitee.get(f"/auth/register/{token}").get_data(as_text=True)
    proof = re.search(r'name="invitation_form_proof" value="([^"]+)"', html).group(1)
    response = invitee.post(f"/auth/register/{token}", data=_registration_data(login="BENEVOLE-TEST", invitation_form_proof=proof))
    assert response.status_code == 200
    assert "déjà utilisé" in response.get_data(as_text=True)
    with app.app_context():
        assert Invitation.query.one().used_at is None

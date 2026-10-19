"""Tests the invitation flow WITH CSRF protection actually enabled."""
import hashlib
import re
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Invitation, User


def _invite(app, secret="one-use-invitation-for-test"):
    with app.app_context():
        admin = User.query.filter_by(login="benevole-test").one()
        admin.role = "admin"
        db.session.add(Invitation(
            token_hash=hashlib.sha256(secret.encode()).hexdigest(),
            created_by_id=admin.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        ))
        db.session.commit()
    return secret


def _extract_proof(html):
    match = re.search(r'name="invitation_form_proof" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def _data(proof=None):
    result = dict(name="Camille", login="camille", password="SuperSecret123", password_confirmation="SuperSecret123")
    if proof is not None:
        result["invitation_form_proof"] = proof
    return result


def test_signup_works_without_browser_session_cookie(app):
    app.config["WTF_CSRF_ENABLED"] = True
    token = _invite(app)
    browser = app.test_client()
    r = browser.get(f"/auth/register/{token}")
    assert r.status_code == 200
    proof = _extract_proof(r.get_data(as_text=True))

    # Simulate mail-app browser losing every cookie after displaying the form.
    browser.delete_cookie("session")
    r = browser.post(f"/auth/register/{token}", data=_data(proof))
    assert r.status_code == 302
    with app.app_context():
        user = User.query.filter_by(login="camille").one()
        assert user.role == "member"
        assert Invitation.query.one().used_at is not None


def test_missing_or_wrong_invitation_form_proof_is_rejected(app):
    app.config["WTF_CSRF_ENABLED"] = True
    token = _invite(app)
    browser = app.test_client()
    assert browser.post(f"/auth/register/{token}", data=_data()).status_code == 400
    assert browser.post(f"/auth/register/{token}", data=_data("bad-proof")).status_code == 400
    with app.app_context():
        assert User.query.filter_by(login="camille").first() is None
        assert Invitation.query.one().used_at is None


def test_form_proof_cannot_be_used_for_another_invitation(app):
    app.config["WTF_CSRF_ENABLED"] = True
    token = _invite(app)
    browser = app.test_client()
    proof = _extract_proof(browser.get(f"/auth/register/{token}").get_data(as_text=True))
    another_token = "second-valid-invitation"
    with app.app_context():
        admin = User.query.filter_by(login="benevole-test").one()
        db.session.add(Invitation(token_hash=hashlib.sha256(another_token.encode()).hexdigest(), created_by_id=admin.id, expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
        db.session.commit()
    assert browser.post(f"/auth/register/{another_token}", data=_data(proof)).status_code == 400

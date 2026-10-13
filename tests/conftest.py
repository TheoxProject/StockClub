import pytest

from app import create_app
from app.extensions import db
from app.models import User


@pytest.fixture()
def app(tmp_path):
    database = tmp_path / "test.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-that-is-long-enough",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
        "WTF_CSRF_ENABLED": False,
        "UPLOAD_FOLDER": str(uploads),
        "SESSION_COOKIE_SECURE": False,
    })
    with app.app_context():
        db.create_all()
        user = User(name="benevole-test", login="benevole-test")
        user.set_password("123456")
        db.session.add(user)
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def logged_in_client(client):
    response = client.post("/auth/login", data={
        "login": "benevole-test",
        "password": "123456",
    }, follow_redirects=True)
    assert response.status_code == 200
    return client

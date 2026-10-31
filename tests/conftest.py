import os
import re
from urllib.parse import urlparse

import pytest

from app import create_app
from app.extensions import db
from app.models import BootstrapLock, User


def safe_test_database_url(default):
    explicit = os.environ.get("STOCKCLUB_TEST_DATABASE_URL")
    if explicit is None:
        return default
    parsed = urlparse(explicit)
    if (parsed.scheme not in {"postgresql", "postgresql+psycopg"}
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or not re.fullmatch(r"/stockclub_test_[A-Za-z0-9_]+", parsed.path)
            or parsed.query or parsed.fragment):
        raise ValueError("STOCKCLUB_TEST_DATABASE_URL must name a dedicated loopback PostgreSQL stockclub_test_ database")
    return explicit


@pytest.fixture()
def app(tmp_path):
    database = tmp_path / "test.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-that-is-long-enough",
        "SQLALCHEMY_DATABASE_URI": safe_test_database_url(f"sqlite:///{database}"),
        "INSTANCE_PATH": str(tmp_path / "instance"),
        "WTF_CSRF_ENABLED": False,
        "UPLOAD_FOLDER": str(uploads),
        "SESSION_COOKIE_SECURE": False,
        "S3_BUCKET": "",
        "S3_ENDPOINT_URL": "",
        "S3_ACCESS_KEY_ID": "",
        "S3_SECRET_ACCESS_KEY": "",
        "S3_PUBLIC_BASE_URL": "",
    })
    with app.app_context():
        db.create_all()
        db.session.add(BootstrapLock(id=1, lock_version=0))
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

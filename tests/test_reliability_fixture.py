"""Refuse unsafe external database configuration before creating an app."""
import pytest

import conftest


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://127.0.0.1/production",
    "postgresql+psycopg://db.invalid/stockclub_test_fictitious",
    "postgresql+psycopg://127.0.0.1/stockclub_test_",
    "postgresql+psycopg://127.0.0.1/stockclub_test_fake?host=db.invalid",
    "postgresql+psycopg://127.0.0.1/stockclub_test_fake#fragment",
    "sqlite:///production.db", "",
])
def test_fixture_refuses_unsafe_database_before_app_creation(monkeypatch, tmp_path, url):
    monkeypatch.setenv("STOCKCLUB_TEST_DATABASE_URL", url)
    def forbidden(*args, **kw):
        pytest.fail("unsafe database reached create_app")
    monkeypatch.setattr(conftest, "create_app", forbidden)
    with pytest.raises(ValueError, match="dedicated loopback"):
        next(conftest.app.__wrapped__(tmp_path))


@pytest.mark.parametrize("url", ["postgresql+psycopg://127.0.0.1:5432/stockclub_test_fictitious",
                                  "postgresql+psycopg://localhost/stockclub_test_fictitious"])
def test_fixture_accepts_dedicated_loopback_name_without_connecting(monkeypatch, url):
    monkeypatch.setenv("STOCKCLUB_TEST_DATABASE_URL", url)
    assert conftest.safe_test_database_url("unused") == url

"""Deployment checks on empty/current-head fictitious databases only.

Run with tools/run_isolated_tests.py, never against an existing application DB.
The real populated 0005 upgrade is covered by test_v3_stock_migration.py.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import os
import re

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import Borrowing, BorrowingEvent, Category, Item, StockMovement, User
from flask_migrate import upgrade

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("populated", [False, True], ids=["empty", "populated-head"])
def test_existing_render_migration_and_entrypoint(tmp_path, populated, monkeypatch):
    url = os.environ.get("STOCKCLUB_TEST_DATABASE_URL", "sqlite:///" + (tmp_path / "deployment.db").as_posix())
    parsed = make_url(url)
    if parsed.get_backend_name() == "postgresql":
        assert parsed.host in {"127.0.0.1", "localhost"} and re.fullmatch(r"stockclub_test_[A-Za-z0-9_]+", parsed.database or "")
        assert not parsed.query
    else:
        assert Path(parsed.database).resolve().is_relative_to(tmp_path)
    app = create_app({
        "TESTING": True, "INSTANCE_PATH": str(tmp_path / "instance"),
        "SQLALCHEMY_DATABASE_URI": url, "SECRET_KEY": "fictitious-deployment-test-only-123456789",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "S3_BUCKET": "", "S3_ENDPOINT_URL": "", "S3_ACCESS_KEY_ID": "", "S3_SECRET_ACCESS_KEY": "",
    })

    def clear_test_schema():
        with app.app_context():
            db.session.remove()
            metadata = MetaData()
            metadata.reflect(bind=db.engine)
            metadata.drop_all(bind=db.engine)

    clear_test_schema()
    try:
        with app.app_context():
            upgrade(directory=str(ROOT / "migrations"))
            assert db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar_one() == "0006_stock_adjustments"
            assert db.session.execute(db.text("SELECT COUNT(*) FROM bootstrap_locks WHERE id=1")).scalar_one() == 1
            if populated:
                user = User(name="Fictitious previous admin", login="previous-admin", role="admin")
                user.set_password("fictitious-only-123456")
                category = Category(name="Previous fictitious category")
                item = Item(name="Previous fictitious item", category=category, quantity=9, photo_key="")
                loan = Borrowing(item=item, item_name_snapshot=item.name, user=user, quantity_out=2, quantity_returned=1)
                db.session.add_all([user, category, item, loan])
                db.session.flush()
                db.session.add_all([
                    BorrowingEvent(borrowing=loan, item=item, item_name_snapshot=item.name, user=user, event_type="sortie", quantity=2),
                    BorrowingEvent(borrowing=loan, item=item, item_name_snapshot=item.name, user=user, event_type="retour", quantity=1),
                    StockMovement(item=item, item_name_snapshot=item.name, user=user, delta=10, quantity_after=10),
                    StockMovement(item=item, item_name_snapshot=item.name, user=user, delta=-2, quantity_after=8),
                    StockMovement(item=item, item_name_snapshot=item.name, user=user, delta=1, quantity_after=9),
                ])
                db.session.commit()
            before = {
                table: db.session.execute(db.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in ("users", "categories", "items", "borrowings", "borrowing_events", "stock_movements")
            }
            # Existing Render start applies upgrade before loading run:app.
            upgrade(directory=str(ROOT / "migrations"))
            after = {
                table: db.session.execute(db.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in before
            }
            assert before == after
            if populated:
                assert Item.query.one().quantity == 9
                assert Borrowing.query.one().remaining_quantity == 1
                assert User.query.one().role == "admin"

        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("COOKIE_SECURE", "1")
        monkeypatch.setenv("TRUST_PROXY", "1")
        monkeypatch.setenv("REQUIRE_OBJECT_STORAGE", "1")
        for key, value in {
            "S3_BUCKET": "fictitious-only",
            "S3_ENDPOINT_URL": "http://127.0.0.1:1/not-contacted",
            "S3_ACCESS_KEY_ID": "fictitious-only",
            "S3_SECRET_ACCESS_KEY": "fictitious-only",
        }.items():
            monkeypatch.setenv(key, value)
        spec = spec_from_file_location("isolated_render_entrypoint", ROOT / "run.py")
        entry = module_from_spec(spec)
        spec.loader.exec_module(entry)
        assert entry.app.config["SESSION_COOKIE_SECURE"]
        assert entry.app.config["S3_BUCKET"] == "fictitious-only"
        response = entry.app.test_client().get("/health")
        assert response.status_code == 200 and response.json == {"status": "ok"}
        health = entry.app.test_cli_runner().invoke(args=["healthcheck"])
        assert health.exit_code == 0, health.output
        assert "OK" in health.output
        print(f"DEPLOYMENT {parsed.get_backend_name()} populated={populated}: head 0006 preserved, run:app loaded, health 200, CLI OK; S3 not contacted")
    finally:
        clear_test_schema()


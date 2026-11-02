"""Real browser, mobile and desktop consumable checks on isolated fictitious SQLite.

Run python -I tools/check_consumables_browser.py [--chromium].
"""
from pathlib import Path
import json
import logging
import os
import sys
import threading
import uuid


def main():
    root = Path(__file__).resolve().parents[1]
    runtime = root / ".test-artifacts" / ("browser_consumables_" + uuid.uuid4().hex[:10])
    runtime.mkdir(parents=True)
    keep = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP") if k in os.environ}
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", AWS_EC2_METADATA_DISABLED="true")
    if "--chromium" in sys.argv:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root / ".test-artifacts/playwright-browsers")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    libs = root / ".test-artifacts/browser-libs"
    if libs.exists():
        sys.path.insert(0, str(libs))

    def guard(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.name.startswith(".env") or path.is_relative_to(root / "instance"):
                raise RuntimeError("Browser isolation: secret/original instance refused")
        if event == "sqlite3.connect" and str(args[0]) != ":memory:":
            if not Path(args[0]).resolve().is_relative_to(runtime):
                raise RuntimeError("Browser isolation: database outside runtime refused")
        if event == "socket.connect" and not (isinstance(args[1], tuple) and args[1][0] == "127.0.0.1"):
            raise RuntimeError("Browser isolation: external connection refused")
    sys.addaudithook(guard)
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    from app import create_app
    from app.extensions import db
    from app.models import BootstrapLock, Borrowing, Category, Consumption, Item, User
    from playwright.sync_api import expect, sync_playwright
    from werkzeug.serving import make_server
    app = create_app({"TESTING": True, "INSTANCE_PATH": str(runtime / "instance"),
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (runtime / "browser.db").as_posix(),
        "SECRET_KEY": uuid.uuid4().hex + uuid.uuid4().hex, "UPLOAD_FOLDER": str(runtime / "uploads"),
        "SESSION_COOKIE_SECURE": False, "S3_BUCKET": "", "S3_ENDPOINT_URL": "", "S3_ACCESS_KEY_ID": "", "S3_SECRET_ACCESS_KEY": ""})
    with app.app_context():
        db.create_all()
        db.session.add(BootstrapLock(id=1, lock_version=0))
        for login, role in [("consumer-admin", "admin"), ("consumer-member", "member")]:
            user = User(name=login, login=login, role=role)
            user.set_password("fictitious-browser-only")
            db.session.add(user)
        item = Item(name="Rouleaux fictifs navigateur", category=Category(name="Consommables fictifs"),
                    quantity=10, unit="rouleaux", photo_key="", is_consumable=True, consumption_threshold=3)
        db.session.add(item)
        db.session.commit()
        item_id = item.id
    server = make_server("127.0.0.1", 0, app, threaded=True)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = {"passed": False, "runtime": str(runtime), "csrf_enabled": True, "steps": []}

    def record(step):
        result["steps"].append(step)
        print("CONSUMABLES_BROWSER: " + step, flush=True)

    try:
        with sync_playwright() as playwright:
            executable = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" if sys.platform == "win32" and "--chromium" not in sys.argv else None
            context = playwright.chromium.launch_persistent_context(str(runtime / "profile"),
                executable_path=executable, headless=True, viewport={"width": 390, "height": 844},
                is_mobile=True, has_touch=True, service_workers="block",
                args=["--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run", "--disable-gpu"])
            context.set_default_timeout(15000)
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
            page = context.pages[0] if context.pages else context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            def fits():
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.url
            def login(who):
                page.goto(origin + "/auth/login")
                page.locator("[name=login]").fill(who)
                page.locator("[name=password]").fill("fictitious-browser-only")
                page.locator("button[type=submit]").click()
                page.wait_for_url(origin + "/")
            def submit(amount, label):
                page.locator("#quantity").fill(str(amount))
                page.locator("#note").fill("Opération fictive navigateur")
                fits()
                page.get_by_role("button", name=label, exact=True).click()
            login("consumer-member")
            page.goto(origin + f"/items/{item_id}")
            expect(page.get_by_role("link", name="Consommer", exact=True)).to_be_visible()
            page.get_by_role("link", name="Consommer", exact=True).click()
            expect(page.locator("#source_box")).to_be_visible()
            expect(page.locator("#source_box")).to_contain_text("Hors boîte")
            submit(1, "Consommer")
            page.wait_for_url(origin + f"/items/{item_id}")
            page.get_by_role("link", name="Réapprovisionner", exact=True).click()
            expect(page.locator("form.form-card")).to_contain_text("Destination : Hors boîte")
            submit(1, "Réapprovisionner")
            page.wait_for_url(origin + f"/items/{item_id}")
            page.locator("#quantity").fill("6")
            page.get_by_role("button", name="Enregistrer la sortie", exact=True).click()
            page.wait_for_url(origin + f"/items/{item_id}")
            with app.app_context():
                loan = Borrowing.query.one().id
            page.goto(origin + "/loans")
            page.get_by_role("link", name="Consommer", exact=True).click()
            expect(page.locator("#quantity")).to_have_attribute("max", "6")
            submit(4, "Consommer")
            page.wait_for_url(origin + "/loans")
            expect(page.locator(f"#return-quantity-{loan}")).to_have_value("2")
            page.get_by_role("button", name="Enregistrer le retour", exact=True).click()
            page.wait_for_url(origin + "/loans")
            fits()
            page.screenshot(path=str(runtime / "mobile.png"), full_page=True)
            with app.app_context():
                obj = db.session.get(Item, item_id)
                assert (obj.quantity, obj.total_quantity) == (6, 6)
                assert db.session.get(Borrowing, loan).quantity_consumed == 4
                ident = Consumption.query.filter_by(kind="loan").one().id
            record("member consumption, refill, separate loan consumption and return, mobile390")
            page.get_by_role("button", name="Déconnexion", exact=True).click()
            login("consumer-admin")
            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(origin + f"/consumptions/{ident}/cancel")
            page.locator("#note").fill("Correction fictive navigateur")
            fits()
            page.get_by_role("button", name="Annuler la consommation", exact=True).click()
            page.wait_for_url(origin + f"/items/{item_id}")
            fits()
            page.screenshot(path=str(runtime / "desktop.png"), full_page=True)
            with app.app_context():
                assert db.session.get(Item, item_id).total_quantity == 10
                assert db.session.get(Borrowing, loan).remaining_quantity == 4
            record("admin cancellation reopens loan, desktop1280")
            assert not errors, errors
            result.update(passed=True, javascript_errors=errors)
            context.close()
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        server.shutdown()
        thread.join(timeout=5)
        (runtime / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

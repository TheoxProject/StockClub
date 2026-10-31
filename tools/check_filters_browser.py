"""Browser regression for filtered URLs and progressive search, on fictitious local data."""
from pathlib import Path
import json
import os
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
runtime = ROOT / ".test-artifacts" / ("browser_" + uuid.uuid4().hex[:10])
runtime.mkdir(parents=True)
keep = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP") if k in os.environ}
os.environ.clear()
os.environ.update(keep)
os.environ.update(PYTHON_DOTENV_DISABLED="1", AWS_EC2_METADATA_DISABLED="true")
sys.path.insert(0, str(ROOT))
# Optional isolated installation, never part of the Render requirements.
libs = ROOT / ".test-artifacts/browser-libs"
if libs.exists():
    sys.path.insert(0, str(libs))
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False

from app import create_app
from app.extensions import db
from app.models import BootstrapLock, Category, Item, User
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

app = create_app({
    "TESTING": True, "INSTANCE_PATH": str(runtime / "instance"),
    "SECRET_KEY": uuid.uuid4().hex + uuid.uuid4().hex,
    "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (runtime / "browser.db").as_posix(),
    "UPLOAD_FOLDER": str(runtime / "uploads"), "SESSION_COOKIE_SECURE": False,
    "S3_BUCKET": "", "S3_ENDPOINT_URL": "", "S3_ACCESS_KEY_ID": "", "S3_SECRET_ACCESS_KEY": "",
})
with app.app_context():
    db.create_all()
    db.session.add(BootstrapLock(id=1, lock_version=0))
    user = User(name="Browser fictitious", login="browser-fictitious", role="member")
    user.set_password("browser-fictitious-only")
    one, two = Category(name="Balises fictives"), Category(name="Outils fictifs")
    db.session.add_all([user, one, two])
    db.session.flush()
    category_id = one.id
    db.session.add_all([
        Item(name="Balise fictive", category=one, quantity=4, photo_key=""),
        Item(name="Marteau fictif", category=two, quantity=2, photo_key=""),
    ])
    db.session.commit()

server = make_server("127.0.0.1", 0, app, threaded=True)
origin = f"http://127.0.0.1:{server.server_port}"
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
result = {"origin": origin, "runtime": str(runtime), "source": str(ROOT)}
try:
    with sync_playwright() as playwright:
        executable = os.environ.get("STOCKCLUB_BROWSER_EXECUTABLE")
        if not executable and sys.platform == "win32":
            executable = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        browser = playwright.chromium.launch(
            executable_path=executable, headless=True,
            args=["--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"])
        result["browser"] = browser.version
        context = browser.new_context(service_workers="block")
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(origin + "/auth/login")
        page.locator("[name=login]").fill("browser-fictitious")
        page.locator("[name=password]").fill("browser-fictitious-only")
        page.locator("button[type=submit]").click()
        page.wait_for_url(origin + "/")
        page.goto(origin + f"/?q=Balise&category={category_id}&availability=available&quantity_min=2")
        assert page.locator("#inventory-results").inner_text().find("Balise fictive") >= 0
        with page.expect_response(lambda r: r.request.resource_type == "fetch") as response:
            page.locator("#filter-reset").click()
        assert response.value.status == 200
        # The response is complete; wait until the controller has applied it.
        page.wait_for_function("document.querySelector('#inventory-results').getAttribute('aria-busy') === 'false'")
        values = page.locator("#inventory-filters").evaluate(
            "form => Object.fromEntries(Array.from(form.elements).filter(e => e.name).map(e => [e.name,e.value]))")
        result["after_reset"] = {"url": page.url, "fields": values,
                                 "results": page.locator("#inventory-results").inner_text()}
        page.screenshot(path=str(runtime / "filters.png"), full_page=True)
        assert all(value == "" for value in values.values()), f"Filters remain after reset: {values}"
        assert page.url == origin + "/", f"Filtered URL remains: {page.url}"
        assert "Balise fictive" in result["after_reset"]["results"]
        assert "Marteau fictif" in result["after_reset"]["results"]
        with page.expect_response(lambda r: r.request.resource_type == "fetch") as response:
            page.locator("[name=q]").fill("Marteau")
        assert response.value.status == 200
        page.wait_for_function("document.querySelector('#inventory-results').getAttribute('aria-busy') === 'false'")
        assert "Marteau fictif" in page.locator("#inventory-results").inner_text()
        assert "Balise fictive" not in page.locator("#inventory-results").inner_text()
        assert "q=Marteau" in page.url
        assert not errors, errors
        result.update(passed=True, progressive_search=True, javascript_errors=errors)
        context.close()
        browser.close()
except Exception as error:
    result.update(passed=False, error=f"{type(error).__name__}: {error}")
    raise
finally:
    server.shutdown()
    thread.join(timeout=5)
    (runtime / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


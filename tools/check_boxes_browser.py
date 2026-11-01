"""Real-browser box checks on fresh, fictitious SQLite data and a new profile.

Run with python -I tools/check_boxes_browser.py. Requires isolated Playwright
development installation; --chromium uses .test-artifacts/playwright-browsers.
No application environment, original instance, personal browser profile or S3.
"""
from pathlib import Path
import json
import logging
import os
import re
import sys
import threading
import uuid


def main():
    root = Path(__file__).resolve().parents[1]
    runtime = root / ".test-artifacts" / ("browser_boxes_" + uuid.uuid4().hex[:10])
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
                raise RuntimeError("Browser test isolation: secret/original instance refused")
        if event == "sqlite3.connect" and str(args[0]) != ":memory:":
            if not Path(args[0]).resolve().is_relative_to(runtime):
                raise RuntimeError("Browser test isolation: database outside runtime refused")
        if event == "socket.connect" and not (isinstance(args[1], tuple) and args[1][0] == "127.0.0.1"):
            raise RuntimeError("Browser test isolation: external connection refused")

    sys.addaudithook(guard)
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    from app import create_app
    from app.extensions import db
    from app.models import BootstrapLock, Category, Item, User
    from playwright.sync_api import expect, sync_playwright
    from werkzeug.serving import make_server

    app = create_app({
        "TESTING": True, "INSTANCE_PATH": str(runtime / "instance"),
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (runtime / "boxes.db").as_posix(),
        "SECRET_KEY": uuid.uuid4().hex + uuid.uuid4().hex,
        "UPLOAD_FOLDER": str(runtime / "uploads"), "SESSION_COOKIE_SECURE": False,
        "S3_BUCKET": "", "S3_ENDPOINT_URL": "", "S3_ACCESS_KEY_ID": "", "S3_SECRET_ACCESS_KEY": "",
    })
    with app.app_context():
        db.create_all()
        db.session.add(BootstrapLock(id=1, lock_version=0))
        for login, role in [("box-admin", "admin"), ("box-member", "member")]:
            user = User(name=login + " fictif", login=login, role=role)
            user.set_password("fictitious-browser-only")
            db.session.add(user)
        category = Category(name="Catégorie fictive navigateur")
        items = [Item(name=name, category=category, quantity=10, photo_key="")
                 for name in ("Cônes fictifs", "Caisse plastique fictive")]
        db.session.add_all(items)
        db.session.commit()
        item_ids = [item.id for item in items]
    server = make_server("127.0.0.1", 0, app, threaded=True)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = {"passed": False, "runtime": str(runtime), "csrf_enabled": True}

    def record(step):
        result["step"] = step
        (runtime / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("BOX_BROWSER: " + step, flush=True)

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
            result["user_agent"] = page.evaluate("navigator.userAgent")

            def fits():
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Horizontal overflow"

            def login(name):
                page.goto(origin + "/")
                logout = page.get_by_role("button", name="Déconnexion", exact=True)
                if logout.count():
                    logout.click()
                page.locator("[name=login]").fill(name)
                page.locator("[name=password]").fill("fictitious-browser-only")
                page.locator("button[type=submit]").click()
                page.wait_for_url(origin + "/")

            login("box-admin")
            page.goto(origin + "/boxes/new")
            page.locator("#name").fill("Arrivée fictive")
            page.get_by_role("button", name="Créer une boîte vide", exact=True).click()
            box_id = int(re.search(r"/boxes/(\d+)$", page.url).group(1))
            box_url = origin + f"/boxes/{box_id}"
            for item_id, quantity in zip(item_ids, (4, 3)):
                page.locator("#item_id").select_option(str(item_id))
                page.locator("#transfer_quantity").fill(str(quantity))
                page.get_by_role("button", name="Enregistrer le déplacement", exact=True).click()
                page.wait_for_url(box_url)
                assert page.locator(f'[data-box-item="{item_id}"] [data-present]').inner_text() == str(quantity)
            fits()
            record("admin creation and assignments")

            # The first V3 feature must respect physical provenance too.
            page.goto(origin + f"/items/{item_ids[0]}/stock-adjustment")
            page.locator("#operation").select_option("remove")
            page.locator("#source_box_id").select_option(str(box_id))
            page.locator("#quantity").fill("5")
            assert "provenance" in page.locator('[data-preview-error]').inner_text()
            page.locator("#quantity").fill("1")
            page.locator("#reason").select_option("casse")
            assert page.locator('[data-preview-available]').inner_text() == "9"
            page.get_by_role("button", name="Enregistrer l’ajustement", exact=True).click()
            page.wait_for_url(origin + f"/items/{item_ids[0]}")
            page.goto(box_url)
            assert page.locator(f'[data-box-item="{item_ids[0]}"] [data-present]').inner_text() == "3"
            page.goto(origin + f"/items/{item_ids[0]}/stock-adjustment")
            assert page.locator("#source_box_id").is_disabled()
            assert page.locator('[data-add-destination]').is_visible()
            page.locator("#quantity").fill("1")
            page.locator("#reason").select_option("achat")
            assert page.locator('[data-preview-available]').inner_text() == "10"
            page.get_by_role("button", name="Enregistrer l’ajustement", exact=True).click()
            page.goto(box_url)
            assert page.locator(f'[data-box-item="{item_ids[0]}"] [data-present]').inner_text() == "3"
            page.locator("#item_id").select_option(str(item_ids[0]))
            page.locator("#transfer_quantity").fill("1")
            page.get_by_role("button", name="Enregistrer le déplacement", exact=True).click()
            page.wait_for_url(box_url)
            assert page.locator(f'[data-box-item="{item_ids[0]}"] [data-present]').inner_text() == "4"
            record("withdrawal preview and operation at origin, additions outside then assignment")

            login("box-member")
            page.goto(origin + f"/items/{item_ids[0]}")
            page.locator("#borrow-source").select_option(str(box_id))
            page.locator("#quantity").fill("1")
            page.get_by_role("button", name="Enregistrer la sortie", exact=True).click()
            page.goto(box_url)
            assert page.get_by_role("button", name="Enregistrer le déplacement", exact=True).count() == 0
            page.get_by_role("link", name="Sortir la boîte", exact=True).click()
            # Wait for the script's initial summary before exercising controls.
            expect(page.locator('[data-group-summary] li')).to_have_count(2)
            page.get_by_role("button", name="Tout désélectionner", exact=True).click()
            expect(page.locator('input[name="selected"]:checked')).to_have_count(0)
            page.get_by_role("button", name="Tout sélectionner", exact=True).click()
            expect(page.locator('input[name="selected"]:checked')).to_have_count(2)
            page.locator(f'#quantity-{item_ids[0]}').fill("2")
            assert "Cônes fictifs : 2" in page.locator('[data-group-summary]').inner_text()
            fits()
            page.get_by_role("button", name="Confirmer la sortie groupée", exact=True).click()
            page.wait_for_url(box_url)
            assert page.locator(f'[data-box-item="{item_ids[0]}"] [data-present]').inner_text() == "1"
            assert page.locator(f'[data-box-item="{item_ids[0]}"] [data-outstanding]').inner_text() == "3"
            record("individual origin and incomplete grouped outing, select all/none")

            page.get_by_role("link", name="Retourner la boîte", exact=True).click()
            assert page.locator('[data-group-line]').count() == 3
            page.get_by_role("button", name="Tout désélectionner", exact=True).click()
            line = page.locator('[data-group-line]').first
            line.locator('input[type=checkbox]').check()
            line.locator('input[type=number]').fill("1")
            page.get_by_role("button", name="Confirmer les retours sélectionnés", exact=True).click()
            page.wait_for_url(box_url)
            assert page.locator(f'[data-box-item="{item_ids[0]}"] [data-present]').inner_text() == "2"
            assert page.locator(f'[data-box-item="{item_ids[0]}"] [data-outstanding]').inner_text() == "2"
            page.get_by_role("link", name="Retourner la boîte", exact=True).click()
            assert page.locator('[data-group-line]').count() == 2
            page.get_by_role("button", name="Confirmer les retours sélectionnés", exact=True).click()
            page.wait_for_url(box_url)
            assert page.get_by_role("button", name="Aucun objet à retourner", exact=True).is_disabled()
            fits()
            page.screenshot(path=str(runtime / "mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1280, "height": 900})
            fits()
            page.screenshot(path=str(runtime / "desktop.png"), full_page=True)
            record("partial then full mixed returns, mobile and desktop")

            page.goto(origin + "/")
            page.locator('input[name=q]').fill("Arrivée fictive")
            page.wait_for_function("document.querySelector('#inventory-results').textContent.includes('Arrivée fictive')")
            assert page.locator('#inventory-results .box-card').count() == 1
            assert not errors, errors
            result.update(passed=True, select_all_none=True, mixed_returns=True, progressive_search=True, adjustment_origin=True,
                          mobile_emulation=True, desktop=True, javascript_errors=errors)
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

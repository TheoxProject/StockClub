"""Independent grouped-fill browser checks on fake SQLite data and a new profile.

Run with python -I tools/check_box_fill_browser.py. Requires isolated Playwright
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
    runtime = root / ".test-artifacts" / ("browser_fill_" + uuid.uuid4().hex[:10])
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
    from app.models import BootstrapLock, BoxContent, BoxEvent, Category, Item, StorageBox, User
    from playwright.sync_api import expect, sync_playwright
    from werkzeug.serving import make_server

    app = create_app({
        "TESTING": True, "INSTANCE_PATH": str(runtime / "instance"),
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (runtime / "fill.db").as_posix(),
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
        category = Category(name="Browser parent")
        child = Category(name="Browser child", parent=category)
        other = Category(name="Other category")
        items = [Item(name=f"Cone {i:02}", category=child, quantity=10, photo_key="") for i in range(26)]
        items.append(Item(name="Panel hidden", category=other, quantity=8, photo_key=""))
        box = StorageBox(name="Browser destination", name_key="browser destination")
        db.session.add_all([box, *items])
        db.session.flush()
        db.session.add(BoxContent(box_id=box.id, item_id=items[0].id, present_quantity=4))
        db.session.commit()
        item_ids = [item.id for item in items]
        box_id, category_id, other_id = box.id, category.id, other.id
    server = make_server("127.0.0.1", 0, app, threaded=True)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = {"passed": False, "runtime": str(runtime), "csrf_enabled": True}

    def record(step):
        result["step"] = step
        (runtime / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("BOX_FILL_BROWSER: " + step, flush=True)

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
            fill_posts = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on('request', lambda request: fill_posts.append(request.headers.get('content-type', ''))
                    if request.method == 'POST' and '/add-material' in request.url else None)
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
            page.goto(origin + '/boxes/new')
            page.locator('#name').fill('Browser empty creation')
            page.locator('[name=next_action][value=empty]').click()
            page.wait_for_url(re.compile(r'/boxes/\d+$'))
            page.goto(origin + '/boxes/new')
            page.locator('#name').fill('Browser add creation')
            page.locator('[name=next_action][value=add]').click()
            page.wait_for_url(re.compile(r'/boxes/\d+/add-material$'))
            created_box_id = int(re.search(r'/boxes/(\d+)/', page.url).group(1))
            expect(page.locator('[data-box-fill]')).to_have_attribute('data-ready', 'true')
            with app.app_context():
                assert BoxContent.query.filter_by(box_id=created_box_id).count() == 0
                assert BoxEvent.query.count() == 0
            record('empty and add creation paths leave stock untouched before confirmation')
            fill_url = origin + f"/boxes/{box_id}/add-material"
            page.goto(fill_url)
            expect(page.locator('[data-box-fill]')).to_have_attribute('data-ready', 'true')
            def row(ident, summary=False):
                return page.locator(f'[data-{"summary" if summary else "fill"}-item="{ident}"]')
            def choose(ident):
                row(ident).locator('[data-select-item]').check()
            def qty(ident, value, summary=False):
                row(ident, summary).locator('[data-quantity-item]').fill(str(value))
            def count(refs, units):
                expect(page.locator('[data-fill-count]')).to_have_text(str(refs))
                expect(page.locator('[data-fill-units]')).to_have_text(str(units))
            def search(value):
                page.locator('[data-fill-search]').fill(value)
                page.wait_for_timeout(450)
                expect(page.locator('[data-fill-results]')).not_to_have_attribute('aria-busy', 'true')
            expect(page.locator('[data-fill-submit]')).to_be_disabled()
            expect(page.locator('[data-fill-item]')).to_have_count(24)
            choose(item_ids[0])
            expect(row(item_ids[0]).locator('[data-quantity-item]')).to_have_value('1')
            row(item_ids[0]).locator('[data-fill-action="plus"]').click()
            row(item_ids[0]).locator('[data-fill-action="minus"]').click()
            qty(item_ids[0], 2)
            count(1,2)
            row(item_ids[0]).locator('[data-fill-action="all"]').click()
            count(1,6)
            qty(item_ids[0], 2)
            page.locator('[data-page-next]').click()
            expect(page.locator('[data-fill-item]')).to_have_count(3)
            page.locator('[data-select-visible]').click()
            count(4,5)
            page.locator('[data-show-selection]').click()
            expect(page.locator('[data-summary-item]')).to_have_count(4)
            qty(item_ids[0], 3, True)
            count(4,6)
            row(item_ids[-1], True).locator('[data-fill-action="remove"]').click()
            count(3,5)
            page.locator('[data-back-results]').click()
            search('Panel')
            expect(page.locator('[data-fill-item]')).to_have_count(1)
            choose(item_ids[-1])
            count(4,6)
            page.locator('[data-fill-category]').select_option(str(category_id))
            expect(page.locator('[data-fill-item]')).to_have_count(0)
            count(4,6)
            search('Cone')
            expect(page.locator('[data-fill-item]')).to_have_count(24)
            expect(row(item_ids[0]).locator('[data-quantity-item]')).to_have_value('3')
            with page.expect_response(lambda response: '/material-options?' in response.url):
                page.locator('[data-refresh-results]').click()
            expect(page.locator('[data-fill-results]')).to_have_attribute('aria-busy', 'false')
            expect(row(item_ids[0]).locator('[data-quantity-item]')).to_have_value('3')
            count(4,6)
            page.locator('[data-select-visible]').click()
            count(27,29)
            page.locator('[data-select-visible]').click()
            count(27,29)
            page.locator('[data-clear-selection]').click()
            count(0,0)
            expect(page.locator('[data-fill-submit]')).to_be_disabled()
            record('selection, quantities, categories, page-scoped select, hidden totals, summary and clear')

            choose(item_ids[0])
            qty(item_ids[0], 0)
            expect(page.locator('[data-fill-submit]')).to_be_disabled()
            qty(item_ids[0], 2)
            # Failed searches retain the selection. Controlled responses also
            # deliberately arrive out of order, independent of server timing.
            page.route('**/material-options*', lambda route: route.abort())
            search('Network failure')
            expect(page.locator('[data-fill-status]')).to_contain_text('conserv')
            count(1,2)
            page.unroute('**/material-options*')
            search('Cone')
            expect(page.locator('[data-fill-item]')).to_have_count(24)
            pending = []
            page.route('**/material-options*', lambda route: pending.append(route))
            page.locator('[data-fill-search]').fill('Old response')
            page.wait_for_timeout(400)
            page.locator('[data-fill-search]').fill('New response')
            page.wait_for_timeout(400)
            assert len(pending) == 2
            payload = dict(rows=[dict(id=item_ids[0], name='Newest response', category='Browser child', photo_url=None, available=6, present=4)], page=1,pages=1,total=1)
            pending[1].fulfill(json=payload)
            payload['rows'][0]['name'] = 'Obsolete response'
            try:
                pending[0].fulfill(json=payload)
            except Exception:
                pass # AbortController may already have cancelled this request.
            page.wait_for_timeout(100)
            assert 'Newest response' in page.locator('[data-fill-results]').inner_text()
            assert 'Obsolete response' not in page.locator('[data-fill-results]').inner_text()
            page.unroute('**/material-options*')
            record('invalid quantity, failed search and controlled obsolete response')

            # Concurrent changes are made only against this fresh fake database.
            page.locator('[data-fill-category]').select_option('')
            search('Panel')
            choose(item_ids[-1])
            qty(item_ids[-1], 3)
            with app.app_context():
                db.session.get(Item, item_ids[0]).quantity = 5
                db.session.delete(db.session.get(Item, item_ids[-1]))
                db.session.commit()
            page.locator('[data-fill-submit]').click()
            expect(page.locator('[data-fill-error]')).to_be_visible()
            page.locator('[data-show-selection]').click()
            expect(page.locator('[data-summary-item]')).to_have_count(2)
            expect(row(item_ids[0], True).locator('[data-quantity-item]')).to_have_value('2')
            expect(row(item_ids[-1], True)).to_contain_text('Panel hidden')
            with app.app_context():
                assert BoxEvent.query.count() == 0
            row(item_ids[-1], True).locator('[data-fill-action="remove"]').click()
            qty(item_ids[0], 1, True)
            page.locator('[data-back-results]').click()
            search('Cone')
            fits()
            page.locator('[data-fill-item]').last.scroll_into_view_if_needed()
            bounds = page.locator('[data-fill-item]').last.bounding_box()
            bar = page.locator('[data-fill-bar]').bounding_box()
            assert bounds['y'] + bounds['height'] <= bar['y'] + 1, (bounds, bar)
            page.screenshot(path=str(runtime/'mobile.png'), full_page=True)
            # Browser emulation cannot open an OS keyboard: exercise the same
            # visualViewport resize event used when a keyboard reduces it.
            page.evaluate("""() => {
                Object.defineProperty(visualViewport, 'height', {configurable:true,get:()=>440});
                visualViewport.dispatchEvent(new Event('resize'));
            }""")
            row(item_ids[0]).locator('[data-quantity-item]').focus()
            page.wait_for_timeout(300)
            keyboard_bar = page.locator('[data-fill-bar]').bounding_box()
            keyboard_input = row(item_ids[0]).locator('[data-quantity-item]').bounding_box()
            assert keyboard_bar['y'] + keyboard_bar['height'] <= 441, keyboard_bar
            assert keyboard_input['y'] + keyboard_input['height'] <= keyboard_bar['y'] + 1, (keyboard_input, keyboard_bar)
            fits()
            page.screenshot(path=str(runtime/'keyboard-simulation.png'), full_page=True)
            page.evaluate("delete visualViewport.height; visualViewport.dispatchEvent(new Event('resize'))")
            page.set_viewport_size({'width':1280,'height':900})
            page.wait_for_timeout(200)
            desktop_bar = page.locator('[data-fill-bar]').bounding_box()
            desktop_height = page.evaluate('window.innerHeight')
            assert abs(desktop_bar['y'] + desktop_bar['height'] - desktop_height) <= 1, (desktop_bar, desktop_height)
            fits()
            page.screenshot(path=str(runtime/'desktop.png'), full_page=True)
            page.screenshot(path=str(runtime/'desktop-viewport.png'))
            record('stale and deleted selection recovery, mobile/desktop layout and simulated viewport resize')
            # Two DOM clicks in one task exercise the pending-request guard.
            page.locator('[data-fill-submit]').evaluate('(button) => {button.click(); button.click();}')
            page.wait_for_url(origin + f'/boxes/{box_id}')
            with app.app_context():
                assert BoxEvent.query.count() == 1
                assert BoxContent.query.filter_by(box_id=box_id,item_id=item_ids[0]).one().present_quantity == 5
            assert not errors, errors
            assert len(fill_posts) == 2, fill_posts
            assert all(value.startswith('application/x-www-form-urlencoded') for value in fill_posts), fill_posts
            result.update(passed=True, selection_persistence=True, search_ordering=True, stale_deleted_recovery=True,
                          mobile_emulation=True, desktop=True, form_transport=fill_posts,
                          keyboard='visualViewport simulation only; physical keyboard not tested',javascript_errors=errors)
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

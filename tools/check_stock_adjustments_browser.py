"""Real-browser V3 checks with a new profile and fictitious local SQLite data."""
from pathlib import Path
import csv
from io import StringIO
import json
import os
import sys
import threading
import uuid

ROOT=Path(__file__).resolve().parents[1]
runtime=ROOT/".test-artifacts"/("browser_stock_"+uuid.uuid4().hex[:10])
runtime.mkdir(parents=True)
keep={k:os.environ[k] for k in ("SystemRoot","WINDIR","PATH","TEMP","TMP") if k in os.environ}
os.environ.clear()
os.environ.update(keep)
os.environ.update(PYTHON_DOTENV_DISABLED="1",AWS_EC2_METADATA_DISABLED="true")
if "--chromium" in sys.argv:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"]=str(ROOT/".test-artifacts/playwright-browsers")
sys.path.insert(0,str(ROOT))
libs=ROOT/".test-artifacts/browser-libs"
if libs.exists(): sys.path.insert(0,str(libs))
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
from app import create_app
from app.extensions import db
from app.models import BootstrapLock, Borrowing, Category, Item, StockMovement, User
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

app=create_app({
    "TESTING":True,"INSTANCE_PATH":str(runtime/"instance"),
    "SECRET_KEY":uuid.uuid4().hex+uuid.uuid4().hex,
    "SQLALCHEMY_DATABASE_URI":"sqlite:///"+(runtime/"browser.db").as_posix(),
    "UPLOAD_FOLDER":str(runtime/"uploads"),"SESSION_COOKIE_SECURE":False,
    "S3_BUCKET":"","S3_ENDPOINT_URL":"","S3_ACCESS_KEY_ID":"","S3_SECRET_ACCESS_KEY":"",
})
with app.app_context():
    db.create_all()
    db.session.add(BootstrapLock(id=1,lock_version=0))
    admin=User(name="Admin fictif navigateur",login="browser-admin",role="admin")
    member=User(name="Membre fictif navigateur",login="browser-member",role="member")
    for user in (admin,member): user.set_password("browser-fictitious-only")
    category=Category(name="Matériel fictif V3")
    a=Item(name="Cônes disponibles fictifs",category=category,quantity=10,photo_key="")
    b=Item(name="Cônes empruntés fictifs",category=category,quantity=10,photo_key="")
    db.session.add_all([admin,member,category,a,b])
    db.session.flush()
    a_id,b_id=a.id,b.id
    for item in (a,b):
        db.session.add(StockMovement(item=item,item_name_snapshot=item.name,user=admin,delta=10,
                                    quantity_after=10,movement_type="initial",total_delta=10))
    db.session.commit()

server=make_server("127.0.0.1",0,app,threaded=True)
origin=f"http://127.0.0.1:{server.server_port}"
thread=threading.Thread(target=server.serve_forever,daemon=True)
thread.start()
result={"origin":origin,"runtime":str(runtime),"source":str(ROOT)}
def record(step):
    result["step"]=step
    (runtime/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("BROWSER_STOCK: "+step,flush=True)
try:
    with sync_playwright() as playwright:
        executable=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" if sys.platform=="win32" and "--chromium" not in sys.argv else None
        # A single fresh persistent test profile avoids extra incognito renderers
        # on memory-constrained Windows machines. It never uses a personal profile.
        session=playwright.chromium.launch_persistent_context(str(runtime/"profile"),
            executable_path=executable,headless=True,viewport={"width":390,"height":844},
            is_mobile=True,has_touch=True,service_workers="block",
            args=["--disable-background-networking","--disable-component-update","--disable-sync",
                  "--no-first-run","--disable-gpu","--renderer-process-limit=1"])
        result["browser"]=session.browser.version if session.browser else "version non exposée par Playwright"
        errors=[]
        session.route("**/*",lambda route:route.continue_() if route.request.url.startswith(origin+"/") else route.abort())
        def login(p,name):
            p.goto(origin+"/")
            logout=p.get_by_role("button",name="Déconnexion",exact=True)
            if logout.count():
                logout.click()
                p.wait_for_url(origin+"/auth/login")
            p.locator("[name=login]").fill(name)
            p.locator("[name=password]").fill("browser-fictitious-only")
            p.locator("button[type=submit]").click()
            p.wait_for_url(origin+"/")
            return p
        def fits(p):
            assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),"Horizontal overflow"
        def balances(p,expected):
            values=p.locator("[data-current-balances]").first.evaluate(
                "e => [Number(e.querySelector('[data-stock-total]').textContent),Number(e.querySelector('[data-stock-available]').textContent),Number(e.querySelector('[data-stock-out]').textContent)]")
            assert values==list(expected),(values,expected)
            fits(p)
        def preview(p,expected):
            p.wait_for_function("document.querySelector('[data-preview-total]').textContent !== '—'")
            values=p.locator("[data-stock-preview]").evaluate(
                "e => [Number(e.querySelector('[data-preview-total]').textContent),Number(e.querySelector('[data-preview-available]').textContent),Number(e.querySelector('[data-preview-out]').textContent)]")
            assert values==list(expected),(values,expected)
            fits(p)
        def adjust(p,item_id,operation,amount,reason,expected):
            p.goto(origin+f"/items/{item_id}/stock-adjustment")
            p.locator("#operation").select_option(operation)
            p.locator("#quantity").fill(str(amount))
            p.locator("#reason").select_option(reason)
            preview(p,expected)
            p.get_by_role("button",name="Enregistrer l’ajustement",exact=True).click()
            p.wait_for_url(origin+f"/items/{item_id}")
            balances(p,expected)

        member_context=session
        member_page=session.pages[0] if session.pages else session.new_page()
        result["user_agent"]=member_page.evaluate("navigator.userAgent")
        member_page.on("pageerror",lambda error:errors.append(str(error)))
        login(member_page,"browser-member")
        for item_id in (a_id,b_id):
            member_page.goto(origin+f"/items/{item_id}")
            balances(member_page,(10,10,0))
            assert member_page.get_by_role("link",name="Ajuster le stock",exact=True).count()==0
            member_page.locator("#quantity").fill("3")
            member_page.get_by_role("button",name="Enregistrer la sortie",exact=True).click()
            member_page.wait_for_url(origin+f"/items/{item_id}")
            balances(member_page,(10,7,3))
            assert member_page.get_by_role("link",name="Déclarer une perte ou une casse",exact=True).count()==0
            assert member_context.request.get(origin+f"/items/{item_id}/stock-adjustment").status==403
        member_page.goto(origin+"/loans")
        loan_a=int(member_page.locator(".loan-card").filter(has_text="Cônes disponibles fictifs").get_attribute("id").split("-")[-1])
        loan_b=int(member_page.locator(".loan-card").filter(has_text="Cônes empruntés fictifs").get_attribute("id").split("-")[-1])
        record("member rights and two outings verified")
        admin_context=member_context
        admin_page=member_page
        login(admin_page,"browser-admin")
        admin_page.goto(origin+f"/items/{a_id}/stock-adjustment")
        admin_page.locator("#operation").select_option("remove")
        admin_page.locator("#quantity").fill("1")
        admin_page.locator("#reason").select_option("autre")
        assert admin_page.locator("#detail").evaluate("e => e.required")
        assert not admin_page.locator("#detail").evaluate("e => e.checkValidity()")
        admin_page.locator("#reason").select_option("perte")
        assert not admin_page.locator("#detail").evaluate("e => e.required")
        assert admin_page.locator("#reason option[value=achat]").is_disabled()
        preview(admin_page,(9,6,3))
        admin_page.locator("#detail").fill("Un cône fictif détruit")
        admin_page.get_by_role("button",name="Enregistrer l’ajustement",exact=True).click()
        admin_page.wait_for_url(origin+f"/items/{a_id}")
        balances(admin_page,(9,6,3))
        assert "Retrait définitif" in admin_page.locator(".history-panel").inner_text()
        result["available_example"]={"total":9,"available":6,"outstanding":3}
        record("available withdrawal example verified")

        admin_page.goto(origin+f"/loans/{loan_b}/write-off")
        admin_page.locator("#quantity").fill("1")
        admin_page.locator("#reason").select_option("perte")
        preview(admin_page,(9,7,2))
        admin_page.get_by_role("button",name="Enregistrer la perte ou la casse",exact=True).click()
        admin_page.wait_for_url(origin+"/loans")
        admin_page.goto(origin+f"/items/{b_id}")
        balances(admin_page,(9,7,2))
        assert "Perte sur emprunt" in admin_page.locator(".history-panel").inner_text()
        result["borrowed_example"]={"total":9,"available":7,"outstanding":2}
        record("borrowed loss example verified")

        login(member_page,"browser-member")
        member_page.goto(origin+"/loans")
        member_page.locator(f"#return-quantity-{loan_b}").fill("1")
        member_page.once("dialog",lambda dialog:dialog.accept())
        member_page.locator(f"#loan-{loan_b}").get_by_role("button",name="Enregistrer le retour",exact=True).click()
        member_page.wait_for_url(origin+"/loans")
        login(admin_page,"browser-admin")
        admin_page.goto(origin+f"/loans/{loan_b}/write-off")
        admin_page.locator("#quantity").fill("1")
        admin_page.locator("#reason").select_option("casse")
        preview(admin_page,(8,8,0))
        admin_page.get_by_role("button",name="Enregistrer la perte ou la casse",exact=True).click()
        admin_page.wait_for_url(origin+"/loans")
        assert admin_page.locator(f"#loan-{loan_b}").count()==0
        assert "Soldé avec perte/casse" in admin_page.locator(".loan-history-section").inner_text()
        assert "Tout retourné" not in admin_page.locator(".loan-history-section").inner_text()
        record("mixed return and loss settlement verified")

        login(member_page,"browser-member")
        member_page.goto(origin+"/loans")
        member_page.locator(f"#return-quantity-{loan_a}").fill("3")
        member_page.once("dialog",lambda dialog:dialog.accept())
        member_page.locator(f"#loan-{loan_a}").get_by_role("button",name="Enregistrer le retour",exact=True).click()
        member_page.wait_for_url(origin+"/loans")
        login(admin_page,"browser-admin")
        adjust(admin_page,a_id,"remove",9,"casse",(0,0,0))
        assert admin_page.get_by_role("link",name="Ajuster le stock",exact=True).count()==1
        adjust(admin_page,a_id,"add",2,"don",(2,2,0))

        # A preview can become stale: the server must refuse the now-excessive withdrawal.
        admin_page.goto(origin+f"/items/{a_id}/stock-adjustment")
        admin_page.locator("#operation").select_option("remove")
        admin_page.locator("#quantity").fill("2")
        admin_page.locator("#reason").select_option("correction")
        preview(admin_page,(0,0,0))
        token=admin_page.locator("input[name=csrf_token]").input_value()
        competing=admin_context.request.post(origin+f"/items/{a_id}/stock-adjustment",
            form={"csrf_token":token,"operation":"remove","quantity":"2","reason":"casse"})
        assert competing.status==200
        with admin_page.expect_response(lambda response:response.request.method=="POST") as response:
            admin_page.get_by_role("button",name="Enregistrer l’ajustement",exact=True).click()
        assert response.value.status==400
        assert "Le retrait dépasse la quantité disponible" in admin_page.locator(".flash-error").inner_text()
        balances(admin_page,(0,0,0))
        adjust(admin_page,a_id,"add",2,"don",(2,2,0))
        result["stale_preview_rejected"]=True
        record("zero stock, replenishment and stale preview verified")

        csv_response=admin_context.request.get(origin+"/items.csv")
        assert csv_response.status==200
        rows=list(csv.reader(StringIO(csv_response.body().decode("utf-8-sig")),delimiter=";"))
        assert rows[0][3]=="Disponible" and rows[0][8:10]==["Stock total","Sorti"]
        by_name={row[0]:row for row in rows[1:]}
        assert by_name["Cônes disponibles fictifs"][3]=="2"
        assert by_name["Cônes empruntés fictifs"][8:10]==["8","0"]
        admin_page.set_viewport_size({"width":1280,"height":900})
        admin_page.goto(origin+f"/items/{b_id}")
        balances(admin_page,(8,8,0))
        admin_page.screenshot(path=str(runtime/"desktop.png"),full_page=True)
        admin_page.set_viewport_size({"width":390,"height":844})
        admin_page.goto(origin+f"/items/{b_id}")
        balances(admin_page,(8,8,0))
        admin_page.screenshot(path=str(runtime/"mobile.png"),full_page=True)
        assert not errors,errors
        result.update(passed=True,mobile_emulation=True,desktop=True,csrf_enabled=True,
                      mixed_settlement=True,zero_and_refill=True,csv=True,javascript_errors=errors)
        member_context.close()
    with app.app_context():
        loan=db.session.get(Borrowing,loan_b)
        assert (loan.quantity_out,loan.quantity_returned,loan.quantity_lost,loan.remaining_quantity)==(3,1,2,0)
except Exception as error:
    result.update(passed=False,error=f"{type(error).__name__}: {error}")
    raise
finally:
    server.shutdown()
    thread.join(timeout=5)
    (runtime/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=True,indent=2))

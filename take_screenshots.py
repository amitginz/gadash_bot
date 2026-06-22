"""Take screenshots of the gadash app (live or localhost) for the report."""
import os
from playwright.sync_api import sync_playwright

BASE_URL       = os.environ.get("BASE_URL", "http://localhost:8080")
PASSWORD       = os.environ.get("WEB_PASSWORD", "gadash2025")
WORKER_PASS    = os.environ.get("WORKER_PASSWORD", "worker2025")
OUT_DIR        = os.path.dirname(os.path.abspath(__file__))

def save(page, name, full=True):
    path = os.path.join(OUT_DIR, name + ".png")
    page.screenshot(path=path, full_page=full)
    print(f"  saved {name}.png")

def goto(page, path, wait="networkidle"):
    page.goto(BASE_URL + path, wait_until=wait)

def login_manager(page):
    goto(page, "/login")
    page.wait_for_timeout(300)
    # manager is default — just fill password
    page.evaluate("setRole('manager')")
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(BASE_URL + "/", wait_until="networkidle")
    print("  logged in as manager")

def login_worker(ctx):
    page = ctx.new_page()
    goto(page, "/login")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(500)
    # click the worker button (triggers setRole via onclick)
    page.locator("#btnWorker").click()
    page.wait_for_timeout(400)
    page.fill("input[name=name]", "דני")
    page.fill("input[name=password]", WORKER_PASS)
    page.click("button[type=submit]")
    page.wait_for_url(BASE_URL + "/worker", wait_until="networkidle")
    print("  logged in as worker")
    return page

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="he-IL")
    page = ctx.new_page()

    # ── Login screens ─────────────────────────────────────────────────────────
    goto(page, "/login")
    page.wait_for_timeout(400)
    save(page, "scr_login")                            # manager mode (default)

    # switch to worker mode for screenshot
    page.evaluate("setRole('worker')")
    page.wait_for_timeout(400)
    save(page, "scr_login_worker")                     # worker mode

    # reset to manager
    page.evaluate("setRole('manager')")
    page.wait_for_timeout(200)

    # ── Manager login ─────────────────────────────────────────────────────────
    login_manager(page)

    # ── Main dashboard ────────────────────────────────────────────────────────
    page.wait_for_timeout(800)                         # let Chart.js render
    save(page, "scr_main")

    # Table section (scrolled)
    page.evaluate("window.scrollTo(0, 320)")
    page.wait_for_timeout(400)
    save(page, "scr_main_table", full=False)

    # Global search
    page.evaluate("window.scrollTo(0, 320)")
    page.wait_for_timeout(200)
    gs = page.locator("#globalSearch")
    if gs.count():
        gs.fill("כרמל")
        page.wait_for_timeout(400)
        save(page, "scr_global_search", full=False)
        gs.fill("")

    # Quick-Add modal
    goto(page, "/")
    page.wait_for_timeout(500)
    btn = page.locator("button[data-bs-target='#quickAddModal']")
    if btn.count():
        btn.click()
        page.wait_for_timeout(600)
        save(page, "scr_quick_add", full=False)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

    # ── Add / Edit / Delete ───────────────────────────────────────────────────
    goto(page, "/add")
    page.wait_for_timeout(400)
    save(page, "scr_add")

    # ── Summary / Audit / Print / Import / API ────────────────────────────────
    goto(page, "/summary")
    page.wait_for_timeout(900)
    save(page, "scr_summary")

    goto(page, "/audit")
    page.wait_for_timeout(400)
    save(page, "scr_audit")

    goto(page, "/print")
    page.wait_for_timeout(400)
    save(page, "scr_print")

    goto(page, "/import")
    page.wait_for_timeout(300)
    save(page, "scr_import")

    goto(page, "/api/docs")
    page.wait_for_timeout(400)
    save(page, "scr_api_docs")

    # ── New reports ───────────────────────────────────────────────────────────
    goto(page, "/field-report")
    page.wait_for_timeout(900)
    save(page, "scr_field_report")

    goto(page, "/field-report/print")
    page.wait_for_timeout(500)
    save(page, "scr_field_print")

    # Client report — search for first available client
    goto(page, "/client-report")
    page.wait_for_timeout(400)
    client_input = page.locator("input[name=client]")
    if client_input.count():
        # pick first suggestion from datalist
        first_opt = page.locator("#clientListC option").first
        if first_opt.count():
            first_client = first_opt.get_attribute("value") or ""
            if first_client:
                client_input.fill(first_client)
                page.click("button[type=submit]")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(900)
    save(page, "scr_client_report")

    # ── Worker portal ─────────────────────────────────────────────────────────
    worker_page = login_worker(ctx)
    worker_page.wait_for_timeout(500)
    save(worker_page, "scr_worker_main")
    worker_page.close()

    browser.close()

print(f"\nDone. All screenshots saved to {OUT_DIR}")

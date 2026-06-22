"""Take screenshots of the live Fly.io site for the report."""
import os
from playwright.sync_api import sync_playwright

BASE_URL = "https://gadash-bot.fly.dev"
PASSWORD = os.environ.get("WEB_PASSWORD", "gadash2025")
OUT_DIR  = os.path.dirname(os.path.abspath(__file__))

PAGES = [
    ("scr_login",    "/login",           None),
    ("scr_main",     "/",                None),
    ("scr_add",      "/add",             None),
    ("scr_summary",  "/summary",         None),
    ("scr_audit",    "/audit",           None),
    ("scr_print",    "/print",           None),
    ("scr_import",   "/import",          None),
]

def shot(page, name, url, extra=None):
    page.goto(BASE_URL + url, wait_until="networkidle")
    if extra:
        extra(page)
    path = os.path.join(OUT_DIR, name + ".png")
    page.screenshot(path=path, full_page=True)
    print(f"  saved {name}.png")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                              locale="he-IL")
    page = ctx.new_page()

    # ── Log in first ─────────────────────────────────────────────────────────
    page.goto(BASE_URL + "/login", wait_until="networkidle")
    # screenshot before login (the login page itself)
    page.screenshot(path=os.path.join(OUT_DIR, "scr_login.png"), full_page=True)
    print("  saved scr_login.png")

    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(BASE_URL + "/", wait_until="networkidle")
    print("  logged in")

    # ── Main dashboard ────────────────────────────────────────────────────────
    page.screenshot(path=os.path.join(OUT_DIR, "scr_main.png"), full_page=True)
    print("  saved scr_main.png")

    # ── Main — scrolled to show table ────────────────────────────────────────
    page.evaluate("window.scrollTo(0, 400)")
    page.wait_for_timeout(300)
    page.screenshot(path=os.path.join(OUT_DIR, "scr_main_table.png"), full_page=False)
    print("  saved scr_main_table.png")

    # ── Add form ──────────────────────────────────────────────────────────────
    shot(page, "scr_add", "/add")

    # ── Summary ───────────────────────────────────────────────────────────────
    shot(page, "scr_summary", "/summary")

    # ── Audit log ─────────────────────────────────────────────────────────────
    shot(page, "scr_audit", "/audit")

    # ── Print page ────────────────────────────────────────────────────────────
    shot(page, "scr_print", "/print")

    # ── Import page ───────────────────────────────────────────────────────────
    shot(page, "scr_import", "/import")

    browser.close()

print("\nDone. All screenshots saved to", OUT_DIR)

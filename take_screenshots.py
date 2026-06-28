"""
Screenshot script for gadash_report.docx
Takes 5 screenshots from the live site and saves them to the project folder.
Run: python take_screenshots.py
"""
import asyncio
from playwright.async_api import async_playwright

BASE_URL     = "https://gadash-bot.fly.dev"
MANAGER_PASS = "gadash2025"
WORKER_PASS  = "worker2025"
OUT_DIR      = r"C:\Users\amit ginzberg\gadash_bot"
WIDTH, HEIGHT = 1280, 800


async def login_manager(page):
    await page.goto(f"{BASE_URL}/logout")
    await page.wait_for_load_state("networkidle")
    await page.goto(f"{BASE_URL}/login")
    await page.wait_for_load_state("networkidle")
    await page.fill("input[name='password']", MANAGER_PASS)
    await page.click("button[type='submit']")
    await page.wait_for_load_state("networkidle")


async def get_first_worker_name(page):
    """Navigate to /workers as manager and return first worker name, or None."""
    await page.goto(f"{BASE_URL}/workers")
    await page.wait_for_load_state("networkidle")
    name_cell = page.locator("tbody td strong").first
    if await name_cell.count():
        return (await name_cell.inner_text()).strip()
    return None


async def create_worker(page, name, password):
    """Create a worker via the /workers form (must be logged in as manager)."""
    await page.goto(f"{BASE_URL}/workers")
    await page.wait_for_load_state("networkidle")
    # wait for the add-worker form to be visible
    name_input = page.locator("input[placeholder='ישראל ישראלי']").first
    await name_input.wait_for(state="visible", timeout=10000)
    await name_input.fill(name)
    await page.locator("input[placeholder='לפחות 4 תווים']").first.fill(password)
    await page.click("button[type='submit']")
    await page.wait_for_load_state("networkidle")
    print(f"  created worker: {name}")


async def delete_worker(page, name):
    """Delete a worker via the /workers/delete/<name> form."""
    await page.goto(f"{BASE_URL}/workers")
    await page.wait_for_load_state("networkidle")
    # find delete button for this worker and submit its form
    row = page.locator(f"tr:has(td strong:text-is('{name}'))")
    if await row.count():
        await row.locator("button[type='submit']").click()
        await page.wait_for_load_state("networkidle")
        print(f"  deleted worker: {name}")


async def login_worker(page, name, password=None):
    if password is None:
        password = WORKER_PASS
    await page.goto(f"{BASE_URL}/logout")
    await page.wait_for_load_state("networkidle")
    await page.goto(f"{BASE_URL}/login")
    await page.wait_for_load_state("networkidle")
    await page.evaluate("setRole('worker')")
    await page.wait_for_timeout(400)
    await page.fill("input[name='name']", name)
    await page.fill("input[name='password']", password)
    await page.click("button[type='submit']")
    await page.wait_for_load_state("networkidle")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
        page = await context.new_page()

        # -- 1: Login page (manager mode, default) --------------------------------
        await page.goto(f"{BASE_URL}/login")
        await page.wait_for_load_state("networkidle")
        await page.screenshot(path=f"{OUT_DIR}/scr_login.png")
        print("OK scr_login.png")

        # -- 2: Main table --------------------------------------------------------
        await login_manager(page)
        await page.goto(f"{BASE_URL}/")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(800)
        await page.screenshot(path=f"{OUT_DIR}/scr_main_table.png")
        print("OK scr_main_table.png")

        # -- 3: Quick-Add modal ---------------------------------------------------
        # click the "הוסף מהיר" button and wait for the modal to open
        await page.goto(f"{BASE_URL}/")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(500)
        btn = page.locator("button[data-bs-target='#quickAddModal']").first
        await btn.click()
        await page.locator("#quickAddModal.show").wait_for(timeout=5000)
        await page.wait_for_timeout(300)
        await page.screenshot(path=f"{OUT_DIR}/scr_quick_add.png")
        print("OK scr_quick_add.png")

        # -- 4: Worker portal -----------------------------------------------------
        TEMP_WORKER      = "עובד לדוגמה"
        TEMP_WORKER_PASS = "demo1234"

        worker_name = await get_first_worker_name(page)
        worker_pass = WORKER_PASS if worker_name else TEMP_WORKER_PASS
        if not worker_name:
            worker_name = TEMP_WORKER  # already created via Python before this script

        await login_worker(page, worker_name, worker_pass)
        await page.goto(f"{BASE_URL}/worker")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(800)
        await page.screenshot(path=f"{OUT_DIR}/scr_worker_main.png")
        print("OK scr_worker_main.png")

        # re-login as manager for next screenshot
        await login_manager(page)

        # -- 5: Field report ------------------------------------------------------
        await page.goto(f"{BASE_URL}/field-report")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{OUT_DIR}/scr_field_report.png")
        print("OK scr_field_report.png")

        await browser.close()
        print("\nDone! Run: python generate_report.py")


if __name__ == "__main__":
    asyncio.run(main())

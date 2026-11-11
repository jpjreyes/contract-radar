"""README screenshots from the running app (API on 8071, web on 3072). Needs Playwright + Chromium.

Run:  python scripts/screenshots.py <contract id with a signature block>
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
BASE = "http://localhost:3072"


def main(contract_id: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def page_as(user: str):
            ctx = browser.new_context(viewport={"width": 1280, "height": 860}, device_scale_factor=2)
            ctx.add_init_script(f"localStorage.setItem('cc-user', '{user}')")
            return ctx.new_page()

        for user in ("analyst", "legal_admin"):
            pg = page_as(user)
            pg.goto(f"{BASE}/contracts/{contract_id}?hl=17054-17106")
            pg.wait_for_selector("#hl", timeout=60_000)
            pg.wait_for_timeout(1200)
            pg.screenshot(path=OUT / f"viewer-{user}.png")
        pg = page_as("maria")
        pg.goto(f"{BASE}/review")
        pg.get_by_role("button", name="Accept").first.wait_for(timeout=60_000)
        pg.screenshot(path=OUT / "review-queue.png")
        pg = page_as("legal_admin")
        pg.goto(BASE)
        pg.get_by_text("Which contracts auto-renew and need 60 days or less notice to stop it?").click()
        pg.wait_for_selector("text=contracts match", timeout=120_000)
        pg.wait_for_timeout(800)
        pg.screenshot(path=OUT / "portfolio-question.png")
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1])

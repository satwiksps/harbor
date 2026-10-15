"""Capture real dashboard and store layouts without recording sign-in secrets.

Run against the local deployment: uv run python -m tests.browser_visual
Screenshots and reports remain in the Git-excluded .runtime directory.
"""

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
BASE = os.getenv("HARBOR_URL", "http://harbor.localhost:8080").rstrip("/")
STORE = "http://store.localhost:8080"
OUT = ROOT / ".runtime" / "visual-audit"
STAGE = "setup"


async def main():
    global STAGE
    OUT.mkdir(parents=True, exist_ok=True)
    credentials = json.loads((ROOT / ".secrets/development.json").read_text("utf-8-sig"))
    layouts = []
    errors = []
    complete = False
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(reduced_motion="reduce")
        page = await context.new_page()
        page.on("pageerror", lambda _: errors.append(STAGE))

        async def capture(name, width, height):
            await page.set_viewport_size({"width": width, "height": height})
            await page.evaluate("document.fonts.ready")
            await page.screenshot(path=str(OUT / f"{name}-{width}.png"), full_page=True)
            geometry = await page.evaluate("""() => ({
                viewport: innerWidth,
                content: document.documentElement.scrollWidth,
                height: document.documentElement.scrollHeight
            })""")
            layouts.append({"page": name, **geometry})
            print(f"CAPTURE {name} {width}px, document width {geometry['content']}px", flush=True)

        try:
            STAGE = "sign-in layouts"
            await page.goto(BASE + "/login")
            for width, height in [(1440, 1000), (390, 844), (320, 720)]:
                await capture("login", width, height)
            await page.set_viewport_size({"width": 1440, "height": 1000})
            await page.get_by_label("Email address", exact=True).fill(credentials["admin_email"])
            await page.get_by_label("Password", exact=True).fill(credentials["admin_password"])
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await page.locator(".main-shell").wait_for(timeout=45000)
            await page.get_by_role("button", name="Dismiss notification", exact=True).click()
            for route in ["chat", "connections", "team", "audit"]:
                STAGE = f"{route} layouts"
                await page.goto(BASE + "/#" + route)
                await page.locator(".main-shell").wait_for(timeout=45000)
                await expect(page.locator(".loading-panel")).to_have_count(0, timeout=45000)
                if route == "connections":
                    await expect(page.get_by_text("Connected", exact=True).first).to_be_visible(
                        timeout=45000
                    )
                for width, height in [
                    (1440, 1000),
                    (1024, 768),
                    (768, 1024),
                    (390, 844),
                    (320, 720),
                ]:
                    await capture(route, width, height)
                if route in ["connections", "team"]:
                    action = "Add connection" if route == "connections" else "Invite teammate"
                    await page.get_by_role("button", name=action, exact=True).click()
                    await expect(page.get_by_role("dialog")).to_be_visible()
                    for width, height in [(1440, 1000), (390, 844), (320, 720)]:
                        await capture(route + "-dialog", width, height)
                    await page.keyboard.press("Escape")
                    await expect(page.get_by_role("dialog")).to_have_count(0)
                if route == "connections":
                    tools = page.locator(".server-tools").first
                    await tools.locator("summary").first.click()
                    for width, height in [(1440, 1000), (390, 844), (320, 720)]:
                        await capture("tool-permissions", width, height)
                    await tools.locator("summary").first.click()
                if route == "team":
                    await page.get_by_role("button", name="Create key", exact=True).click()
                    for width, height in [(1440, 1000), (390, 844), (320, 720)]:
                        await capture("key-dialog", width, height)
                    await page.keyboard.press("Escape")
                if route == "audit":
                    await page.locator(".audit-table .text-button").first.click()
                    for width, height in [(1440, 1000), (390, 844), (320, 720)]:
                        await capture("audit-dialog", width, height)
                    await page.keyboard.press("Escape")
            STAGE = "mobile navigation"
            await page.get_by_role("button", name="Open navigation", exact=True).click()
            await expect(page.get_by_role("link", name="Chat", exact=True)).to_be_visible()
            await capture("navigation", 390, 844)
            await page.get_by_role("button", name="Close navigation", exact=True).first.click()
            await page.set_viewport_size({"width": 1440, "height": 1000})
            await page.get_by_role("button", name="Sign out", exact=True).click()
            await expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()

            for route in ["/", "/cart/", "/checkout/"]:
                STAGE = "store " + route
                await page.goto(STORE + route, wait_until="networkidle")
                if route == "/checkout/":
                    assert (
                        await page.get_by_role("button", name="Place order", exact=True).evaluate(
                            "node => getComputedStyle(node).backgroundColor"
                        )
                        == "rgb(19, 107, 105)"
                    )
                for width, height in [(1440, 1000), (390, 844), (320, 720)]:
                    await capture("store-" + (route.strip("/") or "catalog"), width, height)
                if route == "/":
                    await page.locator("li.product a.woocommerce-LoopProduct-link").first.click()
                    await expect(
                        page.get_by_role("button", name="Add to cart", exact=True)
                    ).to_be_visible()
                    assert (
                        await page.get_by_role("button", name="Add to cart", exact=True).evaluate(
                            "node => getComputedStyle(node).backgroundColor"
                        )
                        == "rgb(19, 107, 105)"
                    )
                    for width, height in [(1440, 1000), (390, 844), (320, 720)]:
                        await capture("store-product", width, height)
                    await page.get_by_role("button", name="Add to cart", exact=True).click()
            assert not errors, "JavaScript exceptions were observed"
            assert all(row["content"] <= row["viewport"] for row in layouts), "Horizontal overflow"
            complete = True
        finally:
            (OUT / "report.json").write_text(
                json.dumps(
                    {"complete": complete, "layouts": layouts, "javascript_errors": errors},
                    indent=2,
                ),
                encoding="utf-8",
            )
            await context.close()
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"Visual audit failed during {STAGE} ({type(error).__name__}).")
        raise SystemExit(1) from None

"""Check responsive accessible names and rendered text contrast without live APIs.

Run against a dashboard preview: uv run python -m tests.browser_accessibility
All API responses are browser-local fixtures. No credentials or LLM calls are used.
"""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
BASE = os.getenv("COMMERCE_CONSOLE_URL", "http://127.0.0.1:4173").rstrip("/")
OUT = ROOT / ".runtime" / "accessibility"
WIDTHS = [320, 390, 430, 768, 1440]


async def text_contrast(locator, pseudo=None):
    return await locator.evaluate(
        """(node, pseudo) => {
            const channels = (value) => value.match(/[\\d.]+/g).map(Number);
            const style = getComputedStyle(node, pseudo);
            const foreground = channels(style.color);
            let ancestor = node;
            let background;
            do {
                background = channels(getComputedStyle(ancestor).backgroundColor);
                if (background.length === 3 || background[3] === 1) break;
                if (background[3] !== 0) throw Error('Unexpected translucent background');
                ancestor = ancestor.parentElement;
            } while (ancestor);
            if (!ancestor) throw Error('No opaque background found');
            if ((foreground[3] ?? 1) !== 1 || Number(style.opacity) !== 1)
                throw Error('Unexpected translucent foreground');
            const luminance = (rgb) => rgb.slice(0, 3).map(value => {
                const linear = value / 255;
                return linear <= 0.04045 ? linear / 12.92 : ((linear + 0.055) / 1.055) ** 2.4;
            }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
            const levels = [luminance(foreground), luminance(background)].sort((a, b) => a - b);
            return {
                foreground: style.color,
                background: getComputedStyle(ancestor).backgroundColor,
                ratio: (levels[1] + 0.05) / (levels[0] + 0.05)
            };
        }""",
        pseudo,
    )


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"complete": False, "scope": "fixture-accessibility", "checks": []}
    errors = []
    unexpected = []

    async def api_fixture(route):
        path = urlsplit(route.request.url).path
        fixtures = {
            "/api/auth/me": {
                "user": {
                    "id": 1,
                    "email": "reviewer@example.test",
                    "name": "Reviewer",
                    "role": "admin",
                },
                "csrf_token": "fixture-only",
            },
            "/api/health": {
                "status": "healthy",
                "chat_configured": True,
                "store_url": "http://store.example.test",
                "gateway_url": "http://gateway.example.test/mcp",
            },
            "/api/servers": [],
            "/api/chat": {
                "message": "Review the proposed change.",
                "tool_calls": [
                    {
                        "name": "catalog_list_products",
                        "arguments": {},
                        "result": {"products": []},
                        "is_error": False,
                        "duration_ms": 12,
                    }
                ],
                "confirmation": {
                    "id": "fixture-confirmation",
                    "summary": "Create the requested product at the reviewed price.",
                    "tool_name": "catalog_create_product",
                    "arguments": {"name": "Fixture product", "price": "249.00"},
                },
            },
        }
        if path not in fixtures:
            unexpected.append(path)
            await route.fulfill(status=404, json={"detail": "Unexpected test request"})
            return
        await route.fulfill(json=fixtures[path])

    async def check_contrast(page, selector, width, pseudo=None):
        element = page.locator(selector)
        await expect(element).to_be_visible()
        measured = await text_contrast(element, pseudo)
        report["checks"].append({"width": width, "selector": selector, **measured})
        assert measured["ratio"] >= 4.5, f"{selector} at {width}px: {measured}"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(reduced_motion="reduce")
        await context.route("**/api/**", api_fixture)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 1000})
                await page.goto(BASE + "/login")
                await check_contrast(page, 'input[type="email"]', width, "::placeholder")
                await page.goto(BASE)
                store = page.get_by_role("link", name="Open store", exact=True)
                await expect(store).to_be_visible()
                await expect(store).to_have_attribute("href", "http://store.example.test")
                report["checks"].append({"width": width, "accessible_name": "Open store"})
                composer = page.get_by_label("Message the commerce assistant", exact=True)
                await check_contrast(page, ".composer textarea", width, "::placeholder")
                await composer.fill("Prepare the fixture product.")
                await page.get_by_role("button", name="Send message", exact=True).click()
                await check_contrast(page, ".tool-call summary code", width)
                await check_contrast(page, ".write-confirmation > p", width)
                # Nested tool arguments have a darker background than the outer card.
                await check_contrast(page, ".write-confirmation summary", width)
                if width in (320, 1440):
                    await page.screenshot(path=str(OUT / f"chat-{width}.png"), full_page=True)
                print(f"PASS accessible store link and text contrast at {width}px", flush=True)
            assert not errors, errors
            assert not unexpected, unexpected
            report["complete"] = True
        finally:
            report["javascript_errors"] = errors
            report["unexpected_requests"] = unexpected
            (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

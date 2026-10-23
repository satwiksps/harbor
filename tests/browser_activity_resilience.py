"""Activity races and error recovery, with every API request intercepted."""

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright, expect

from tests.browser_observability import metrics
from tests.deployed_config import console_url, output_path

ROOT = Path(__file__).resolve().parents[1]
BASE = console_url("http://127.0.0.1:5176")
OUT = output_path(ROOT, "activity-resilience")


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"complete": False, "checks": []}
    errors, unexpected, queries = [], [], []
    removed, delay, failed = False, False, False
    started, release = asyncio.Event(), asyncio.Event()
    row = {
        "id": "call-1", "user_id": "reader", "user_name": "Reader",
        "user_email": "reader@example.test", "server_name": "Inventory",
        "tool_name": "list_products", "timestamp": "2026-10-23T08:00:00Z",
        "duration_ms": 0, "outcome": "pending", "request_id": "request-1",
    }

    async def fixture(route):
        nonlocal delay
        url = urlsplit(route.request.url)
        params = parse_qs(url.query)
        if url.path == "/api/auth/me":
            body = {"user": {"id": "reader", "name": "Reader", "role": "admin",
                             "email": "reader@example.test"}, "csrf_token": "fixture"}
        elif url.path == "/api/health":
            body = {"status": "ok", "chat_configured": False}
        elif url.path == "/api/servers":
            body = [] if removed else [{"id": "inventory", "name": "Inventory",
                "slug": "inventory", "url": "https://example.test/mcp", "enabled": True,
                "status": "healthy", "tools": []}]
        elif url.path == "/api/members":
            body = []
        elif url.path == "/api/audit/summary":
            body = {"window": params.get("window", ["24h"])[0], "from": "2026-10-22T08:00:00Z",
                    "to": "2026-10-23T08:00:00Z", "scope": "team", "user_id": None,
                    "server_id": params.get("server_id", [None])[0], "totals": metrics(),
                    "servers": []}
        elif url.path == "/api/audit":
            queries.append(params)
            if delay:
                delay = False
                started.set()
                await release.wait()
            if failed:
                await route.fulfill(status=503, json={"detail": "History temporarily unavailable"})
                return
            body = {"items": [dict(row)], "total": 1}
        else:
            unexpected.append(url.path)
            await route.fulfill(status=404)
            return
        await route.fulfill(json=body)

    def check(label):
        report["checks"].append(label)
        print("PASS", label, flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.route("**/api/**", fixture)
        try:
            await page.clock.install()
            await page.goto(BASE + "/#audit")
            await expect(page.locator(".audit-table .duration-cell")).to_have_text("In progress")
            delay = True
            await page.get_by_role("button", name="Refresh", exact=True).click()
            await asyncio.wait_for(started.wait(), 10)
            await page.get_by_role("button", name="View details for list_products").click()
            dialog = page.get_by_role("dialog", name="Tool call details")
            await expect(dialog).to_contain_text("In progress")
            row.update(outcome="success", duration_ms=147)
            release.set()
            await expect(page.locator(".audit-table .duration-cell")).to_have_text("147 ms")
            await expect(dialog).to_contain_text("147 ms")
            await expect(dialog).to_contain_text("Success")
            check("An open detail dialog follows a pending call's completed refresh")
            await page.get_by_role("button", name="Close dialog").click()

            await page.get_by_label("Filter by connection").select_option("inventory")
            await expect(page.get_by_role("button", name="Refresh", exact=True)).to_be_enabled()
            removed = True
            async with page.expect_response(lambda r: urlsplit(r.url).path == "/api/servers"):
                await page.clock.fast_forward(31000)
            select = page.get_by_label("Filter by connection")
            await expect(select).to_have_value("inventory")
            await expect(select.locator("option:checked")).to_contain_text("Unavailable connection")
            assert queries[-1]["server_id"] == ["inventory"]
            check("A removed selected connection stays visibly filtered instead of showing All connections")
            await page.get_by_role("button", name="Clear filters", exact=True).click()
            await expect(select).to_have_value("")
            await expect(page.get_by_role("button", name="Refresh", exact=True)).to_be_enabled()
            assert "server_id" not in queries[-1]

            failed = True
            await page.get_by_role("button", name="Refresh", exact=True).click()
            await expect(page.get_by_role("alert")).to_contain_text("History temporarily unavailable")
            await expect(page.locator(".audit-table")).to_contain_text("list_products")
            failed = False
            await page.get_by_role("button", name="Try again", exact=True).click()
            await expect(page.get_by_role("alert")).to_have_count(0)
            check("A failed history refresh preserves the last result and offers a working retry")
            for width in [320, 390, 768, 1440]:
                await page.set_viewport_size({"width": width, "height": 900})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            check("Activity recovery states fit mobile, tablet and desktop widths")
            assert not errors, errors
            assert not unexpected, unexpected
            report["complete"] = True
        finally:
            release.set()
            report.update(page_errors=errors, unexpected_requests=unexpected)
            (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

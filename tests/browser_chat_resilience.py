"""Chat recovery and integration checks against browser-local API fixtures.

Run with HARBOR_URL pointed at the local dashboard. All API requests are
intercepted, including authentication; no provider quota or real writes are used.
"""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from tests.deployed_config import console_url, output_path

ROOT = Path(__file__).resolve().parents[1]
BASE = console_url("http://127.0.0.1:5176")
OUT = output_path(ROOT, "chat-resilience")
USER = {"id": 1, "name": "Avery Chen", "email": "avery@example.test", "role": "admin"}
SUCCESS = {"message": "The store has three notebooks.", "tool_calls": []}
APPROVAL = {
    "message": "Review the proposed change.",
    "tool_calls": [],
    "confirmation": {
        "id": "fixture-approval",
        "summary": "Create a notebook.",
        "tool_name": "woo_create_product",
        "arguments": {"name": "Notebook"},
    },
}


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"complete": False, "scope": "browser-local fixtures", "checks": []}
    errors, unexpected, sends, decisions = [], [], [], []
    response = SUCCESS
    status = 200
    headers = {}
    decision_status = 200
    decision_response = {"message": "Change cancelled.", "tool_calls": []}
    gate = None
    started = asyncio.Event()

    async def fixture(route):
        path = urlsplit(route.request.url).path
        if path == "/api/chat":
            sends.append(route.request.post_data_json)
            started.set()
            if gate:
                await gate.wait()
            await route.fulfill(
                status=status, headers=headers, content_type="application/json",
                body=json.dumps(response),
            )
            return
        if path == "/api/chat/confirm":
            decisions.append(route.request.post_data_json)
            started.set()
            if gate:
                await gate.wait()
            await route.fulfill(status=decision_status, json=decision_response)
            return
        fixtures = {
            "/api/auth/me": {"user": USER, "csrf_token": "fixture-only"},
            "/api/health": {
                "status": "healthy",
                "chat_configured": True,
                "store_url": "https://store.example.test",
                "gateway_url": "https://harbor.example.test/mcp",
            },
            "/api/servers": [],
        }
        if path not in fixtures or route.request.method != "GET":
            unexpected.append(f"{route.request.method} {path}")
            await route.fulfill(status=400, json={"detail": "Unexpected fixture request"})
            return
        await route.fulfill(json=fixtures[path])

    def check(label):
        report["checks"].append(label)
        print("PASS", label, flush=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        await context.route("**/api/**", fixture)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        composer = page.get_by_label("Message the Harbor assistant", exact=True)
        send_button = page.get_by_role("button", name="Send message", exact=True)
        retry = page.get_by_role("button", name="Retry message", exact=True)

        async def reset():
            await page.reload()
            await expect(composer).to_be_enabled()

        async def send(text):
            await composer.fill(text)
            await send_button.click()

        try:
            await page.goto(BASE)
            await expect(composer).to_be_enabled()

            # Round 1: response boundaries and recoverable provider failures.
            invalid_responses = [
                {"message": {"unexpected": "object"}, "tool_calls": []},
                {"message": "Answer", "tool_calls": {}},
                {"message": "Answer", "tool_calls": [None]},
                {**APPROVAL, "confirmation": {**APPROVAL["confirmation"], "id": None}},
                {**APPROVAL, "confirmation": {**APPROVAL["confirmation"], "summary": {}}},
                None,
            ]
            for response in invalid_responses:
                await reset()
                await send("Keep this prompt")
                await expect(page.get_by_role("alert")).to_contain_text("invalid chat response")
                await expect(composer).to_be_enabled()
                await expect(page.locator(".message.assistant")).to_have_count(0)
                before = sends[-1]
                response = SUCCESS
                await retry.click()
                await expect(page.locator(".message.assistant")).to_have_count(1)
                assert sends[-1] == before
                await expect(page.locator(".message.user")).to_have_count(1)
            check("Malformed chat messages, tool calls and approvals fail safely and retry once")

            for status in [429, 503]:
                response = {"detail": "Chat is temporarily busy. Please retry shortly."}
                headers = {"Retry-After": "3"}
                await reset()
                await send("Find notebooks")
                await expect(retry).to_be_disabled()
                await expect(page.get_by_text("Try again in", exact=False)).to_be_visible()
                await composer.fill("Keep this unsent draft")
                await expect(send_button).to_be_disabled()
                before_count = len(sends)
                await composer.press("Enter")
                assert len(sends) == before_count
                await expect(retry).to_be_enabled(timeout=7000)
                assert len(sends) == before_count, "Cooldown must not automatically replay a request"
                response, status, headers = SUCCESS, 200, {}
                await retry.click()
                await expect(page.locator(".message.assistant")).to_have_count(1)
                await expect(composer).to_have_value("Keep this unsent draft")
                await expect(page.locator(".message.user")).to_have_count(1)
            check("429 and 503 cooldowns block resubmission, preserve drafts and never auto-retry")

            # Round 2: navigation, duplicate submissions and write uncertainty.
            await reset()
            gate, started = asyncio.Event(), asyncio.Event()
            await composer.fill("A single request")
            before_count = len(sends)
            await composer.press("Enter")
            await asyncio.wait_for(started.wait(), timeout=10)
            await composer.dispatch_event("keydown", {"key": "Enter"})
            assert len(sends) == before_count + 1
            await page.get_by_role("navigation", name="Main navigation").locator('a[href="#connections"]').click()
            await expect(page.get_by_role("heading", name="Connections", exact=True)).to_be_visible()
            gate.set()
            await expect(page.locator(".message.assistant")).to_have_count(1)
            await page.get_by_role("link", name="Chat", exact=True).click()
            await expect(page.locator(".message.user")).to_have_count(1)
            await expect(composer).to_be_enabled()
            check("Repeated Enter sends once and navigation preserves the pending conversation")

            gate = None
            for decision_status in [404, 409, 410, 403]:
                await reset()
                response = APPROVAL
                await send("Prepare a notebook")
                heading = page.get_by_role("heading", name="Review this action", exact=True)
                await expect(heading).to_be_focused()
                decision_response = {"detail": "Approval no longer valid"}
                await page.get_by_role("button", name="Approve & run", exact=True).click()
                await expect(heading).to_have_count(0)
                await expect(composer).to_be_enabled()
                await expect(page.get_by_role("alert")).to_contain_text("Check Activity")
            check("Expired, forbidden and changed approvals release the composer with recovery guidance")

            await reset()
            await send("Prepare a notebook")
            approve = page.get_by_role("button", name="Approve & run", exact=True)
            await expect(approve).to_be_enabled()
            gate, started = asyncio.Event(), asyncio.Event()
            decision_status, decision_response = 504, {"detail": "The gateway timed out."}
            before_count = len(decisions)
            await approve.evaluate("button => { button.click(); button.click(); }")
            await asyncio.wait_for(started.wait(), timeout=10)
            assert len(decisions) == before_count + 1
            await expect(approve).to_be_disabled()
            gate.set()
            await expect(approve).to_be_enabled()
            await expect(page.get_by_role("alert")).to_contain_text("Check Activity")
            await expect(composer).to_be_disabled()
            gate = None
            decision_status, decision_response = 404, {"detail": "Confirmation is already used"}
            await approve.click()
            await expect(composer).to_be_enabled()
            await expect(approve).to_have_count(0)
            check("Approval double-click sends once; uncertain results retain guidance and consumed IDs recover")

            # Round 3: rendering, keyboard and bounded history at narrow widths.
            await reset()
            response = {
                "message": "## Summary\n\n| Product | Price |\n| --- | --- |\n| Notebook | 349 |",
                "tool_calls": [{
                    "name": "woo_list_products",
                    "arguments": {"limit": 1},
                    "result": {"content": [{"text": "x" * 1000}]},
                    "is_error": False,
                    "duration_ms": 25,
                }],
            }
            await composer.fill("First line")
            await composer.press("Shift+Enter")
            await expect(composer).to_have_value("First line\n")
            await composer.press("Enter")
            await expect(page.locator(".message.assistant")).to_have_count(1)
            await page.locator(".tool-call summary").click()
            for width in [320, 390, 768, 1440]:
                await page.set_viewport_size({"width": width, "height": 844})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                assert await page.locator(".conversation").evaluate("n => n.scrollWidth <= n.clientWidth"), width
            check("Keyboard newlines, response tables and long tool results fit mobile through desktop")

            response = {"message": "A" * 17000, "tool_calls": []}
            for index in range(4):
                await send(f"Continue {index}")
                await expect(page.locator(".message.assistant")).to_have_count(index + 2)
            payload = sends[-1]["messages"]
            assert len(payload) <= 30
            assert payload[0]["role"] == payload[-1]["role"] == "user"
            assert max(len(item["content"]) for item in payload) <= 16000
            assert sum(len(item["content"]) for item in payload) <= 40000
            await expect(page.get_by_text("Only recent messages are sent.", exact=False)).to_be_visible()
            await expect(page.locator(".message.user")).to_have_count(5)
            await page.get_by_role("button", name="New chat", exact=True).click()
            await page.get_by_role("button", name="Start new chat", exact=True).click()
            await expect(page.locator(".message")).to_have_count(0)
            await expect(composer).to_be_focused()
            check("Large histories stay within backend limits while visible history and new-chat focus work")
            assert not errors, errors
            assert not unexpected, unexpected
            report["complete"] = True
        finally:
            if gate:
                gate.set()
            if not report["complete"]:
                await page.screenshot(path=str(OUT / "failure.png"), full_page=True)
            report["javascript_errors"] = errors
            report["unexpected_requests"] = unexpected
            (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

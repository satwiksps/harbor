"""Exercise console reading, navigation and access controls with isolated fixtures.

Run against a dashboard preview with HARBOR_URL=http://127.0.0.1:4173.
Every API request is intercepted. No credentials, provider calls or real changes.
"""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from tests.deployed_config import console_url, output_path

ROOT = Path(__file__).resolve().parents[1]
BASE = console_url("http://127.0.0.1:4173")
OUT = output_path(ROOT, "console-regressions")

MARKDOWN = """## Order summary

There are **12 orders** ready for packing.

- Check stock.
- Pack the order.

| Status | Orders |
| --- | --- |
| Processing | 12 |

```json
{"status": "processing"}
```

[Store documentation](https://example.test/docs)

[Unsafe link](javascript:alert(1))

[Local reference](#connections)

![Remote tracking image](https://image.example.test/pixel.png)

<script>window.fixtureExecuted = true</script>
<img src="https://image.example.test/html.png" onerror="window.fixtureExecuted = true">
"""


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"complete": False, "base": BASE, "scope": "browser-local fixtures", "checks": []}
    errors = []
    unexpected = []
    mutations = []
    role = "admin"
    chat_mode = "markdown"
    pending_reply = asyncio.Event()
    reply_started = asyncio.Event()
    tools = [
        {
            "name": "list_orders",
            "description": "Find orders by status and date.",
            "permission": "read",
            "inputSchema": {"type": "object"},
        },
        {
            "name": "create_product",
            "description": "Create a catalog product.",
            "permission": "write",
            "inputSchema": {"type": "object"},
        },
    ]
    admin = {"id": 1, "name": "Avery Chen", "email": "avery@example.test", "role": "admin"}
    member = {"id": 2, "name": "Jordan Lee", "email": "jordan@example.test", "role": "member"}

    async def fixture(route):
        path = urlsplit(route.request.url).path
        if path == "/api/chat":
            if chat_mode == "delayed":
                reply_started.set()
                await pending_reply.wait()
            result = {
                "message": MARKDOWN
                if chat_mode == "markdown"
                else "\n\n".join(
                    f"Order {number}: Ready to pack after the stock check." for number in range(24)
                ),
                "tool_calls": [],
            }
            if chat_mode == "approval":
                result = {
                    "message": "Review the proposed product before it is created.",
                    "tool_calls": [],
                    "confirmation": {
                        "id": "fixture-confirmation",
                        "summary": "Create a notebook at INR 349.",
                        "tool_name": "woo_create_product",
                        "arguments": {"name": "Notebook", "price": "349.00"},
                    },
                }
            await route.fulfill(json=result)
            return
        if path == "/api/chat/confirm":
            mutations.append({"path": path, "body": route.request.post_data_json})
            await route.fulfill(json={"message": "Action cancelled.", "tool_calls": []})
            return
        if path == "/api/servers/1/tools/permissions":
            payload = route.request.post_data_json
            mutations.append({"path": path, "body": payload})
            for tool in tools:
                tool["permission"] = payload["permissions"].get(tool["name"], tool["permission"])
            await route.fulfill(json={"status": "updated"})
            return
        fixtures = {
            "/api/auth/me": {
                "user": admin if role == "admin" else member,
                "csrf_token": "fixture-only",
            },
            "/api/health": {
                "status": "healthy",
                "chat_configured": True,
                "store_url": "https://store.example.test",
                "gateway_url": "https://harbor.example.test/mcp",
            },
            "/api/servers": [
                {
                    "id": 1,
                    "name": "WooCommerce",
                    "slug": "woo",
                    "url": "https://tools.example.test/mcp",
                    "enabled": True,
                    "status": "healthy",
                    "last_checked_at": "2026-10-23T09:00:00Z",
                    "tools": tools,
                }
            ],
            "/api/members": [
                {**user, "active": True, "created_at": "2026-10-01T09:00:00Z"}
                for user in [admin, member]
            ],
            "/api/keys": [
                {
                    "id": 1,
                    "name": "Desktop client",
                    "prefix": "fixture_",
                    "user_id": 2,
                    "created_at": "2026-10-01T09:00:00Z",
                    "last_used_at": "2026-10-23T09:00:00Z",
                }
            ],
            "/api/invites": [
                {
                    "id": 1,
                    "email": "sam@example.test",
                    "role": "member",
                    "expires_at": "2099-10-25T09:00:00Z",
                }
            ],
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
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        await context.route("**/api/**", fixture)

        async def block_remote_image(route):
            unexpected.append(route.request.url)
            await route.abort()

        await context.route("https://image.example.test/**", block_remote_image)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        composer = page.get_by_label("Message the Harbor assistant", exact=True)

        async def send(text):
            previous = await page.locator(".message.assistant").count()
            await composer.fill(text)
            await page.get_by_role("button", name="Send message", exact=True).click()
            await expect(page.locator(".message.assistant")).to_have_count(previous + 1)
            if chat_mode != "approval":
                await expect(composer).to_be_enabled()

        try:
            await page.goto(BASE)
            await expect(page.locator(".chat-welcome")).to_be_visible()
            await page.get_by_role("button", name="Explore the catalog", exact=True).click()
            await expect(composer).to_be_focused()
            await expect(composer).not_to_be_empty()
            check("A starter prompt fills and focuses the composer without sending a request")

            await send("**Keep my message literal.**")
            response = page.locator(".message.assistant").last
            await expect(
                response.get_by_role("heading", name="Order summary", exact=True)
            ).to_be_visible()
            await expect(response.locator("strong")).to_have_text("12 orders")
            await expect(response.get_by_role("listitem")).to_have_count(2)
            await expect(response.get_by_role("table")).to_be_visible()
            await expect(response.locator("pre code")).to_contain_text('"status": "processing"')
            await expect(page.locator(".message.user strong")).to_have_count(0)
            await expect(page.locator(".message.user")).to_contain_text(
                "**Keep my message literal.**"
            )
            check(
                "Assistant Markdown renders headings, lists, tables and code; user text stays literal"
            )

            link = response.get_by_role("link", name="Store documentation", exact=True)
            await expect(link).to_have_attribute("href", "https://example.test/docs")
            await expect(link).to_have_attribute("target", "_blank")
            assert "noopener" in (await link.get_attribute("rel") or "")
            assert await response.locator('a[href^="javascript:"], script, img').count() == 0
            assert await page.evaluate("window.fixtureExecuted === undefined")
            check(
                "Markdown rejects active HTML, script links and remote images; safe links isolate their tab"
            )
            original_url = page.url
            await expect(
                response.get_by_role("link", name="Local reference", exact=True)
            ).to_have_count(0)
            await response.get_by_text("Local reference", exact=True).click()
            assert page.url == original_url, "A model-authored fragment changed the console route"
            await expect(page.get_by_role("log", name="Conversation", exact=True)).to_be_visible()
            check("A model-authored fragment cannot redirect the current console route")

            for width in [320, 390, 768, 1440]:
                await page.set_viewport_size({"width": width, "height": 844})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert await page.locator(".conversation").evaluate(
                    "n => n.scrollWidth <= n.clientWidth"
                )
            check("Formatted chat content fits phone, tablet and desktop widths")

            chat_mode = "long"
            await send("Show a longer order list")
            conversation = page.get_by_role("log", name="Conversation", exact=True)
            await expect(conversation).not_to_have_js_property("scrollTop", 0)
            chat_mode = "delayed"
            await composer.fill("Prepare the next page")
            await page.get_by_role("button", name="Send message", exact=True).click()
            await asyncio.wait_for(reply_started.wait(), timeout=10)
            await conversation.evaluate(
                "n => { n.scrollTop = 0; n.dispatchEvent(new Event('scroll')); }"
            )
            await expect(
                page.get_by_role("button", name="Jump to latest", exact=True)
            ).to_be_visible()
            pending_reply.set()
            await expect(composer).to_be_enabled()
            await expect(page.locator(".message.assistant")).to_have_count(3)
            await expect(conversation).to_have_js_property("scrollTop", 0)
            await page.get_by_role("button", name="Jump to latest", exact=True).click()
            await expect(
                page.get_by_role("button", name="Jump to latest", exact=True)
            ).to_have_count(0)
            assert await conversation.evaluate(
                "n => n.scrollHeight - n.scrollTop - n.clientHeight <= 2"
            )
            check(
                "An arriving reply preserves a reader's scroll position; Jump to latest restores the end"
            )

            chat_mode = "approval"
            await send("Prepare a notebook")
            approval = page.get_by_role("heading", name="Review this action", exact=True)
            await expect(approval).to_be_focused()
            await expect(composer).to_be_disabled()
            await page.get_by_role("button", name="Cancel action", exact=True).click()
            await expect(approval).to_have_count(0)
            await expect(composer).to_be_focused()
            assert mutations[-1] == {
                "path": "/api/chat/confirm",
                "body": {"id": "fixture-confirmation", "approve": False},
            }
            check("An approval receives keyboard focus and cancellation returns it to the composer")

            await page.get_by_role("button", name="New chat", exact=True).click()
            await expect(page.get_by_role("dialog")).to_be_visible()
            await page.get_by_role("button", name="Start new chat", exact=True).click()
            await expect(page.get_by_role("dialog")).to_have_count(0)
            await expect(composer).to_be_focused()
            await expect(conversation).to_have_js_property("scrollTop", 0)
            await expect(page.locator(".message")).to_have_count(0)
            check("Starting a new chat clears the transcript and restores composer focus")

            await page.goto(BASE + "/#connections")
            await page.locator(".server-tools summary").first.click()
            search = page.get_by_label("Search tools in WooCommerce", exact=True)
            await search.fill("orders")
            permission = page.get_by_role("combobox", name="Permission for list_orders", exact=True)
            await permission.select_option("write")
            before = len(mutations)
            await search.fill("missing fixture")
            await expect(
                page.get_by_role("button", name="Clear search", exact=True)
            ).to_be_visible()
            await expect(
                page.get_by_role("button", name="Save permissions", exact=True)
            ).to_be_enabled()
            await page.get_by_role("button", name="Clear search", exact=True).click()
            await expect(permission).to_have_value("write")
            assert len(mutations) == before
            await page.get_by_role("button", name="Save permissions", exact=True).click()
            await expect(
                page.get_by_role("button", name="Save permissions", exact=True)
            ).to_have_count(0)
            assert mutations[-1] == {
                "path": "/api/servers/1/tools/permissions",
                "body": {"permissions": {"list_orders": "write"}},
            }
            check(
                "Tool search preserves unsaved permissions across hidden rows and saves only on request"
            )

            await page.goto(BASE + "/#team")
            for width in [320, 390, 768]:
                await page.set_viewport_size({"width": width, "height": 844})
                for name in ["Workspace members", "API keys", "Invitations"]:
                    region = page.get_by_role("region", name=name, exact=True)
                    await expect(region).to_be_visible()
                    assert await region.evaluate("n => n.scrollWidth <= n.clientWidth"), (
                        width,
                        name,
                    )
                select = page.get_by_label("Role for Jordan Lee", exact=True)
                assert await select.evaluate(
                    "n => parseFloat(getComputedStyle(n).fontSize) >= 16 && n.getBoundingClientRect().height >= 44"
                )
                for name in ["Deactivate Jordan Lee", "Revoke Desktop client"]:
                    button = page.get_by_role("button", name=name, exact=True)
                    await button.scroll_into_view_if_needed()
                    bounds = await button.bounding_box()
                    assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
                    assert bounds["height"] >= 44
                await page.screenshot(path=str(OUT / f"team-{width}.png"), full_page=True)
            check(
                "Phone and tablet member, key and invitation cards keep actions visible without sideways scrolling"
            )

            role = "member"
            tools[0]["permission"] = "read"
            await page.goto(BASE + "/#connections")
            await page.reload()
            await expect(
                page.get_by_role("button", name="Add connection", exact=True)
            ).to_have_count(0)
            await expect(page.get_by_role("switch")).to_have_count(0)
            await page.locator(".server-tools summary").first.click()
            await expect(
                page.get_by_role("combobox", name="Permission for list_orders", exact=True)
            ).to_have_count(0)
            await expect(page.get_by_text("create_product", exact=True)).to_be_visible()
            await expect(page.get_by_text("Admin only", exact=True)).to_be_visible()
            await page.goto(BASE + "/#team")
            await expect(
                page.get_by_role("heading", name="My API keys", exact=True)
            ).to_be_visible()
            await expect(
                page.get_by_role("region", name="Workspace members", exact=True)
            ).to_have_count(0)
            await expect(
                page.get_by_role("button", name="Invite teammate", exact=True)
            ).to_have_count(0)
            await expect(page.get_by_role("button", name="Create key", exact=True)).to_be_visible()
            check(
                "Member screens expose personal keys and read-only tool permissions without administrator controls"
            )
            assert not errors, errors
            assert not unexpected, unexpected
            report["complete"] = True
        finally:
            pending_reply.set()
            if not report["complete"]:
                await page.screenshot(path=str(OUT / "failure.png"), full_page=True)
                (OUT / "failure-snapshot.txt").write_text(
                    await page.locator("body").aria_snapshot(), encoding="utf-8"
                )
            report["javascript_errors"] = errors
            report["unexpected_requests"] = unexpected
            (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Dashboard failure and interaction regressions with isolated browser response fixtures.

Authentication is real. Chat, registry mutations and audit responses are intercepted
inside this test browser, so no LLM quota or real connection changes are required.
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect

from tests.deployed_config import console_url, credentials_path, output_path

ROOT = Path(__file__).resolve().parents[1]
BASE = console_url()
STAGE = "setup"


async def main():
    global STAGE
    checks = []
    credentials = json.loads(credentials_path(ROOT).read_text("utf-8-sig"))

    def record(name):
        checks.append(name)
        print("PASS", name, flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda _: errors.append(STAGE))
        try:
            await page.goto(BASE + "/login")
            await page.get_by_label("Email address", exact=True).fill(credentials["admin_email"])
            await page.get_by_label("Password", exact=True).fill(credentials["admin_password"])
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await page.locator(".main-shell").wait_for(timeout=45000)
            composer = page.get_by_label("Message the Harbor assistant", exact=True)
            payloads = []
            mode = "failure"

            async def chat_response(route):
                payloads.append(route.request.post_data_json["messages"])
                if mode == "failure":
                    await route.fulfill(
                        status=503, json={"detail": "Provider temporarily unavailable"}
                    )
                elif mode.startswith("approval"):
                    await route.fulfill(
                        json={
                            "message": "Review the proposed change.",
                            "tool_calls": [],
                            "confirmation": {
                                "id": "browser-fixture",
                                "summary": "Change a test item",
                                "tool_name": "fixture_write",
                                "arguments": {},
                            },
                        }
                    )
                else:
                    await route.fulfill(
                        json={
                            "message": "A" * 17000
                            if mode == "large"
                            else "Test response received.",
                            "tool_calls": [],
                        }
                    )

            async def send(message):
                await composer.fill(message)
                async with page.expect_response(lambda r: r.url.endswith("/api/chat")):
                    await page.get_by_role("button", name="Send message", exact=True).click()
                await expect(composer).to_be_enabled()

            await page.route("**/api/chat", chat_response)
            STAGE = "failed chat retry"
            await send("Keep this prompt exactly once.")
            await expect(
                page.get_by_role("button", name="Retry message", exact=True)
            ).to_be_visible()
            mode = "success"
            await page.get_by_role("button", name="Retry message", exact=True).click()
            await expect(page.get_by_text("Test response received.", exact=True)).to_be_visible()
            assert payloads[0] == payloads[1]
            assert await page.locator(".message.user").count() == 1
            record("A failed chat request can be retried without duplicating its prompt")

            STAGE = "long conversation limits"
            for index in range(17):
                await send(f"Conversation turn {index}")
            assert len(payloads[-1]) <= 30
            assert payloads[-1][-1]["content"] == "Conversation turn 16"
            assert await page.locator(".message.user").count() == 18
            mode = "large"
            for index in range(3):
                await send(f"Long reply turn {index}")
            assert all(len(item["content"]) <= 16000 for item in payloads[-1])
            assert sum(len(item["content"]) for item in payloads[-1]) <= 40000
            await expect(
                page.get_by_text("Only recent messages are sent.", exact=False)
            ).to_be_visible()
            record("Long chats keep visible history while sending bounded recent context")

            STAGE = "expired and changed approvals"
            for status in [404, 409]:
                mode = "approval"
                await composer.fill("Prepare a test change")
                await page.get_by_role("button", name="Send message", exact=True).click()
                await expect(page.get_by_role("heading", name="Review this action")).to_be_visible()

                async def reject(route):
                    await route.fulfill(status=status, json={"detail": "Approval no longer valid"})

                await page.route("**/api/chat/confirm", reject)
                await page.get_by_role("button", name="Approve & run", exact=True).click()
                await expect(composer).to_be_enabled()
                await expect(page.get_by_role("heading", name="Review this action")).to_have_count(
                    0
                )
                await page.unroute("**/api/chat/confirm", reject)
            record("Expired and changed approvals release the composer without a write retry")

            STAGE = "audit filter failure"
            audit_failure = True

            async def audit_response(route):
                if "outcome=error" in route.request.url:
                    if audit_failure:
                        await route.fulfill(status=503, json={"detail": "Audit unavailable"})
                    else:
                        await route.fulfill(json={"items": [], "total": 0})
                else:
                    await route.fulfill(
                        json={
                            "total": 1,
                            "items": [
                                {
                                    "id": "fixture",
                                    "user_id": "fixture",
                                    "user_name": "Fixture member",
                                    "user_email": "fixture@example.test",
                                    "server_name": "Fixture server",
                                    "tool_name": "fixture_old_row",
                                    "timestamp": "2026-10-15T10:00:00Z",
                                    "duration_ms": 10,
                                    "outcome": "success",
                                    "request_id": "fixture",
                                }
                            ],
                        }
                    )

            await page.route("**/api/audit?*", audit_response)
            await page.get_by_role("link", name="Activity", exact=True).click()
            STAGE = "initial audit fixture"
            await expect(page.get_by_text("fixture_old_row", exact=True)).to_be_visible()
            STAGE = "changing audit filter"
            await page.get_by_label("Filter by outcome").select_option("error")
            STAGE = "audit filter error response"
            await expect(page.get_by_text("Audit unavailable", exact=True)).to_be_visible()
            await expect(page.get_by_text("fixture_old_row", exact=True)).to_have_count(0)
            audit_failure = False
            STAGE = "retrying audit filter"
            await page.get_by_role("button", name="Try again", exact=True).click()
            await expect(page.get_by_text("Audit unavailable", exact=True)).to_have_count(0)
            await expect(page.locator(".audit-table tbody tr")).to_have_count(0)
            record("Failed activity filters discard old rows and recover on retry")

            STAGE = "independent connection mutations"
            servers = [
                {
                    "id": name,
                    "name": "Fixture " + name,
                    "slug": name,
                    "url": "https://fixture.example.test/mcp",
                    "enabled": True,
                    "status": "Connected",
                    "tools": [
                        {
                            "name": "read_sample",
                            "description": "Sample",
                            "inputSchema": {"type": "object"},
                            "permission": "read",
                        }
                    ],
                }
                for name in ["alpha", "beta"]
            ]
            gates = {name: asyncio.Event() for name in ["alpha", "beta"]}
            started = {name: asyncio.Event() for name in ["alpha", "beta"]}

            async def registry_response(route):
                if route.request.method == "GET":
                    await route.fulfill(json=servers)
                    return
                name = route.request.url.rsplit("/", 1)[-1]
                started[name].set()
                await gates[name].wait()
                next(item for item in servers if item["id"] == name)["enabled"] = False
                await route.fulfill(json={"status": "updated"})

            await page.route("**/api/servers**", registry_response)
            await page.goto(BASE + "/#connections")
            await page.reload()
            first = page.get_by_role("switch", name="Enable Fixture alpha", exact=True)
            second = page.get_by_role("switch", name="Enable Fixture beta", exact=True)
            await first.click()
            await asyncio.wait_for(started["alpha"].wait(), timeout=10)
            await second.click()
            await asyncio.wait_for(started["beta"].wait(), timeout=10)
            await expect(first).to_be_disabled()
            gates["beta"].set()
            await expect(second).to_be_enabled()
            await expect(first).to_be_disabled()
            gates["alpha"].set()
            await expect(first).to_be_enabled()
            await expect(first).to_have_attribute("aria-checked", "false")
            await expect(second).to_have_attribute("aria-checked", "false")
            record("Concurrent connection updates keep each pending row disabled independently")

            STAGE = "mobile navigation keyboard"
            await page.set_viewport_size({"width": 390, "height": 844})
            trigger = page.get_by_role("button", name="Open navigation", exact=True)
            await trigger.click()
            drawer = page.get_by_role("dialog", name="Workspace navigation")
            await expect(drawer).to_be_visible()
            await expect(
                page.get_by_role("button", name="Close navigation", exact=True)
            ).to_be_focused()
            assert await page.locator(".main-shell").evaluate("node => node.inert")
            for _ in range(12):
                await page.keyboard.press("Tab")
                assert await drawer.evaluate("node => node.contains(document.activeElement)")
            await page.keyboard.press("Escape")
            await expect(drawer).to_have_count(0)
            await expect(trigger).to_be_focused()
            await trigger.click()
            await drawer.locator('.nav-item[aria-current="page"]').click()
            await expect(drawer).to_have_count(0)
            await expect(trigger).to_be_focused()
            assert not await page.locator(".main-shell").evaluate("node => node.inert")
            record("Selecting the current page closes mobile navigation")

            STAGE = "short mobile navigation"
            await page.set_viewport_size({"width": 667, "height": 375})
            await trigger.click()
            profile = drawer.get_by_role("button", name="Sign out", exact=True)
            await profile.scroll_into_view_if_needed()
            bounds = await profile.bounding_box()
            assert bounds and bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 375
            assert await drawer.evaluate("node => node.scrollTop > 0")
            await profile.focus()
            await page.keyboard.press("Tab")
            await expect(
                drawer.get_by_role("button", name="Close navigation", exact=True)
            ).to_be_focused()
            await page.keyboard.press("Escape")
            await expect(drawer).to_have_count(0)
            record("Short mobile navigation scrolls every control into view and keeps focus inside")

            await trigger.click()
            await page.set_viewport_size({"width": 1024, "height": 768})
            await expect(page.locator(".sidebar.is-open")).to_have_count(0)
            assert not await page.locator(".main-shell").evaluate("node => node.inert")
            record("Mobile navigation traps focus, closes with Escape, and resets on desktop")
            assert not errors
            record("No dashboard JavaScript exceptions during failure recovery")
            await page.get_by_role("button", name="Sign out", exact=True).click()
            await expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
        finally:
            await context.close()
            await browser.close()
    output = output_path(ROOT, "browser-ui")
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps(
            {"complete": True, "base": BASE, "response_fixtures": True, "checks": checks}, indent=2
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"Dashboard UI regression failed during {STAGE} ({type(error).__name__}).")
        raise SystemExit(1) from None

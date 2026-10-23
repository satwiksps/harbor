"""Management failure and concurrency checks using intercepted browser-local APIs.

No upstream, credentials, or real workspace mutations are used. Run with
HARBOR_URL=http://127.0.0.1:5176 uv run python -m tests.browser_management_resilience.
"""

import asyncio
import copy
import json
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from tests.deployed_config import console_url, output_path

BASE = console_url("http://127.0.0.1:5176")
OUT = output_path(Path(__file__).resolve().parents[1], "management-resilience")
ADMIN = {"id": "admin", "name": "Alex Admin", "email": "alex@example.test", "role": "admin"}
MEMBER = {"id": "member", "name": "Morgan Member", "email": "morgan@example.test", "role": "member"}
SERVER = {
    "id": "store",
    "name": "Store tools",
    "slug": "woo",
    "url": "https://tools.example.test/mcp",
    "enabled": True,
    "status": "healthy",
    "auth_type": "bearer",
    "header_name": None,
    "tools": [
        {
            "name": name,
            "description": "A fixture tool",
            "permission": "write",
            "inputSchema": {"type": "object"},
        }
        for name in ["list_orders", "__proto__", "constructor"]
    ],
}


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "complete": False,
        "scope": "browser-local fixtures",
        "checks": [],
        "failures": [],
        "layouts": [],
    }
    errors, unexpected = [], []
    state = {}
    invite_started, invite_release = asyncio.Event(), asyncio.Event()

    def reset():
        state.clear()
        state.update(
            server=copy.deepcopy(SERVER),
            role="admin",
            patches=[],
            key_reads_fail=False,
            key_refresh_failure=False,
            key_mode="success",
            invite_mode="success",
            gateway_url=BASE + "/mcp",
            keys=[],
            invites=[],
            key_posts=0,
            invite_posts=0,
            key_gets=0,
            invite_gets=0,
            credential_failure=False,
            permission_started=None,
            permission_release=None,
        )
        invite_started.clear()
        invite_release.clear()

    async def fixture(route):
        path, method = urlsplit(route.request.url).path, route.request.method
        if path == "/api/servers/store" and method == "PATCH":
            payload = route.request.post_data_json
            state["patches"].append(payload)
            if state["credential_failure"]:
                state["server"].update(enabled=False, status="Disabled")
                await route.fulfill(
                    status=503,
                    json={
                        "detail": "Credential update was not confirmed. Connection remains disabled; re-enter the credential and enable it."
                    },
                )
            else:
                state["server"].update(payload)
                await route.fulfill(json=state["server"])
            return
        if path == "/api/servers/store/tools/permissions" and method == "PATCH":
            payload = route.request.post_data_json
            state["patches"].append(payload)
            if state["permission_started"]:
                state["permission_started"].set()
                await state["permission_release"].wait()
            for tool in state["server"]["tools"]:
                tool["permission"] = payload["permissions"].get(tool["name"], tool["permission"])
            await route.fulfill(json=state["server"])
            return
        if path == "/api/invites" and method == "POST":
            payload = route.request.post_data_json
            state["invite_payload"] = payload
            state["invite_posts"] += 1
            state["invites"].append({"id": "new", **payload, "expires_at": "2099-10-25T09:00:00Z"})
            invite_started.set()
            await invite_release.wait()
            if state["invite_mode"] == "network":
                await route.abort("failed")
            elif state["invite_mode"] == "503":
                await route.fulfill(status=503, json={"detail": "Lost response"})
            else:
                link = {
                    "missing": None,
                    "external": "https://unexpected.example.test/invite/fixture-only",
                    "canonical": "http://harbor.localhost:8080/invite/fixture-only",
                }.get(state["invite_mode"], "/invite/fixture-only")
                await route.fulfill(
                    status=201,
                    json={"invite_url": link, "email": payload["email"], "role": payload["role"]},
                )
            return
        if path == "/api/keys" and method == "POST":
            state["key_payload"] = route.request.post_data_json
            state["key_posts"] += 1
            state["keys"].append(
                {
                    "id": "new",
                    "name": state["key_payload"]["name"],
                    "prefix": "fixture",
                    "user_id": "admin",
                    "created_at": "2026-10-01T00:00:00Z",
                }
            )
            state["key_reads_fail"] = state["key_refresh_failure"]
            if state["key_mode"] == "network":
                await route.abort("failed")
            elif state["key_mode"] == "503":
                await route.fulfill(status=503, json={"detail": "Lost response"})
            else:
                await route.fulfill(
                    status=201,
                    json={}
                    if state["key_mode"] == "missing"
                    else {"key": "harbor_fixture_only_do_not_use", "record": {"id": "new"}},
                )
            return
        if path == "/api/keys":
            state["key_gets"] += 1
        if path == "/api/invites":
            state["invite_gets"] += 1
        if path == "/api/keys" and state["key_reads_fail"]:
            await route.fulfill(status=503, json={"detail": "Key list temporarily unavailable"})
            return
        fixtures = {
            "/api/auth/me": {
                "user": ADMIN if state["role"] == "admin" else MEMBER,
                "csrf_token": "fixture-only",
            },
            "/api/health": {
                "status": "healthy",
                "chat_configured": True,
                "gateway_url": state["gateway_url"],
                "store_url": "https://store.example.test",
            },
            "/api/servers": [state["server"]],
            "/api/members": [
                {**person, "active": True, "created_at": "2026-10-01T00:00:00Z"}
                for person in [ADMIN, MEMBER]
            ],
            "/api/keys": state["keys"],
            "/api/invites": state["invites"],
        }
        if path not in fixtures or method != "GET":
            unexpected.append(f"{method} {path}")
            await route.fulfill(status=400, json={"detail": "Unexpected fixture request"})
            return
        await route.fulfill(json=fixtures[path])

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1366, "height": 900}, reduced_motion="reduce"
        )
        await context.route("**/api/**", fixture)

        async def case(name, test):
            reset()
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                await test(page)
                report["checks"].append(name)
                print("PASS", name, flush=True)
            except Exception as exc:
                report["failures"].append(
                    {
                        "case": name,
                        "error": str(exc),
                        "type": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    }
                )
                print("FAIL", name, str(exc)[:250], flush=True)
                await page.screenshot(
                    path=str(OUT / f"failure-{len(report['failures'])}.png"), full_page=True
                )
                (OUT / f"failure-{len(report['failures'])}-snapshot.txt").write_text(
                    await page.locator("body").aria_snapshot(), encoding="utf-8"
                )
            finally:
                invite_release.set()
                if state["permission_release"]:
                    state["permission_release"].set()
                await page.close()

        async def dirty_patch(page):
            await page.goto(BASE + "/#connections")
            await page.get_by_role("button", name="Edit Store tools", exact=True).click()
            state["server"]["enabled"] = False  # Another administrator pauses it.
            await page.get_by_label("Connection name", exact=True).fill("Renamed tools")
            await page.get_by_role("button", name="Save changes", exact=True).click()
            await expect(page.get_by_role("dialog")).to_have_count(0)
            assert state["patches"] == [{"name": "Renamed tools"}], state["patches"]
            assert state["server"]["enabled"] is False

        async def credential_failure(page):
            state["credential_failure"] = True
            await page.goto(BASE + "/#connections")
            await page.get_by_role("button", name="Edit Store tools", exact=True).click()
            await page.get_by_label("Replace credential (optional)", exact=True).fill(
                "fixture-token"
            )
            await page.get_by_role("button", name="Save changes", exact=True).click()
            await expect(page.get_by_role("alert")).to_contain_text("Connection remains disabled")
            await expect(
                page.get_by_label("Enable this connection", exact=True)
            ).not_to_be_checked()
            await page.get_by_role("button", name="Cancel", exact=True).click()
            await expect(
                page.get_by_role("switch", name="Enable Store tools", exact=True)
            ).to_have_attribute("aria-checked", "false")

        async def special_tool_names(page):
            await page.goto(BASE + "/#connections")
            await page.locator(".server-tools summary").first.click()
            for name in ["__proto__", "constructor"]:
                await page.get_by_label(f"Permission for {name}", exact=True).select_option("read")
            await page.get_by_role("button", name="Save permissions", exact=True).click()
            await expect(
                page.get_by_role("button", name="Save permissions", exact=True)
            ).to_have_count(0)
            assert state["patches"] == [
                {"permissions": {"__proto__": "read", "constructor": "read"}}
            ], state["patches"]

        async def endpoint_drafts(page):
            await page.goto(BASE + "/#connections")
            await page.locator(".server-tools summary").first.click()
            await page.get_by_label("Permission for list_orders", exact=True).select_option("read")
            state["server"]["url"] = "https://replacement.example.test/mcp"
            await page.get_by_role("button", name="Refresh view", exact=True).click()
            await expect(page.locator(".server-url")).to_have_text(state["server"]["url"])
            await expect(page.get_by_label("Permission for list_orders", exact=True)).to_have_value(
                "write"
            )
            await expect(
                page.get_by_role("button", name="Save permissions", exact=True)
            ).to_have_count(0)

        async def permission_serialization(page):
            state["permission_started"], state["permission_release"] = (
                asyncio.Event(),
                asyncio.Event(),
            )
            await page.goto(BASE + "/#connections")
            await page.locator(".server-tools summary").first.click()
            await page.get_by_label("Permission for list_orders", exact=True).select_option("read")
            await page.get_by_role("button", name="Save permissions", exact=True).click()
            await asyncio.wait_for(state["permission_started"].wait(), 10)
            for button in [
                "Edit Store tools",
                "Remove Store tools",
                "Check connection & discover tools",
            ]:
                await expect(page.get_by_role("button", name=button, exact=True)).to_be_disabled()
            await expect(page.get_by_role("switch")).to_be_disabled()
            state["permission_release"].set()
            await expect(page.get_by_role("switch")).to_be_enabled()

        async def invitation_lock(page):
            await page.goto(BASE + "/#team")
            await page.get_by_role("button", name="Invite teammate", exact=True).click()
            email = page.get_by_role("textbox", name="Email address", exact=True)
            await email.fill("recipient@example.test")
            await page.get_by_role("button", name="Create invitation", exact=True).click()
            await asyncio.wait_for(invite_started.wait(), 10)
            await expect(email).to_be_disabled()
            await expect(page.get_by_label("Workspace role")).to_be_disabled()
            await page.keyboard.press("Escape")
            await expect(page.get_by_role("dialog")).to_be_visible()
            invite_release.set()
            await expect(
                page.get_by_role("heading", name="Your invitation is ready", exact=True)
            ).to_be_visible()
            await expect(page.get_by_role("dialog")).to_contain_text("recipient@example.test")

        async def key_recovery(page):
            state["key_refresh_failure"] = True
            await page.goto(BASE + "/#team")
            await page.get_by_role("button", name="Create key", exact=True).click()
            await page.get_by_label("Key name", exact=True).fill("Editor")
            await (
                page.get_by_role("dialog")
                .get_by_role("button", name="Create key", exact=True)
                .click()
            )
            await expect(
                page.get_by_role("heading", name="Save your new API key", exact=True)
            ).to_be_visible()
            await expect(page.get_by_role("dialog")).to_contain_text(
                "harbor_fixture_only_do_not_use"
            )
            await expect(page.get_by_role("alert")).to_contain_text(
                "Key list temporarily unavailable"
            )
            assert state["key_payload"] == {"name": "Editor"}

        def uncertain_key(mode):
            async def test(page):
                state["key_mode"] = mode
                await page.goto(BASE + "/#team")
                await page.get_by_role("button", name="Create key", exact=True).click()
                await page.get_by_label("Key name", exact=True).fill("Uncertain editor")
                create = page.get_by_role("dialog").get_by_role(
                    "button", name="Create key", exact=True
                )
                await create.click()
                await expect(page.get_by_role("alert")).to_contain_text("revoke any new key")
                await expect(create).to_be_disabled()
                await expect(
                    page.get_by_role("button", name="Revoke Uncertain editor", exact=True)
                ).to_be_attached()
                assert state["key_posts"] == 1 and state["key_gets"] >= 2

            return test

        def uncertain_invite(mode):
            async def test(page):
                state["invite_mode"] = mode
                state["gateway_url"] = "http://harbor.localhost:8080/mcp"
                invite_release.set()
                await page.goto(BASE + "/#team")
                await page.get_by_role("button", name="Invite teammate", exact=True).click()
                await page.get_by_label("Email address", exact=True).fill("invitee@example.test")
                create = page.get_by_role("button", name="Create invitation", exact=True)
                await create.click()
                await expect(page.get_by_role("alert")).to_contain_text("review invitations")
                await expect(create).to_be_disabled()
                await expect(
                    page.get_by_role("button", name="Copy invitation", exact=True)
                ).to_have_count(0)
                await expect(
                    page.get_by_role("region", name="Invitations", exact=True)
                ).to_contain_text("invitee@example.test")
                assert state["invite_posts"] == 1 and state["invite_gets"] >= 2

            return test

        async def canonical_invitation(page):
            state["gateway_url"] = "http://harbor.localhost:8080/mcp"
            state["invite_mode"] = "canonical"
            invite_release.set()
            await page.goto(BASE + "/#team")
            await page.get_by_role("button", name="Invite teammate", exact=True).click()
            await page.get_by_label("Email address", exact=True).fill("invitee@example.test")
            await page.get_by_role("button", name="Create invitation", exact=True).click()
            await expect(
                page.get_by_role("heading", name="Your invitation is ready", exact=True)
            ).to_be_visible()
            await expect(page.locator(".secret-box code")).to_have_text(
                "http://harbor.localhost:8080/invite/fixture-only"
            )
            assert state["invite_posts"] == 1

        async def assert_mobile_layout(page, view):
            layout = await page.evaluate("""async () => {
                await document.fonts.ready;
                let previous = "", stable = 0;
                const samples = [];
                for (let frame = 0; frame < 12 && stable < 3; frame++) {
                    await new Promise(requestAnimationFrame);
                    const size = [innerWidth, document.documentElement.scrollWidth,
                        document.body.scrollWidth, document.body.scrollHeight];
                    const current = JSON.stringify(size);
                    stable = current === previous ? stable + 1 : 0;
                    previous = current;
                    samples.push(size);
                }
                return {
                    url: location.href,
                    viewport: innerWidth,
                    documentWidth: document.documentElement.scrollWidth,
                    bodyWidth: document.body.scrollWidth,
                    fonts: document.fonts.status,
                    stableFrames: stable,
                    samples,
                    overflowing: [...document.querySelectorAll('body *')].flatMap(element => {
                        const rect = element.getBoundingClientRect();
                        const style = getComputedStyle(element);
                        if (!rect.width || !rect.height || style.visibility === 'hidden' ||
                            rect.right <= innerWidth) return [];
                        return [{tag: element.tagName, classes: element.className,
                            text: (element.textContent || '').trim().slice(0, 120),
                            left: rect.left, right: rect.right, width: rect.width,
                            scrollWidth: element.scrollWidth, clientWidth: element.clientWidth,
                            overflowX: style.overflowX, position: style.position}];
                    }).slice(0, 25)
                };
            }""")
            layout["view"] = view
            report["layouts"].append(layout)
            assert layout["stableFrames"] >= 3, f"Layout did not settle: {json.dumps(layout)}"
            assert layout["documentWidth"] <= layout["viewport"], json.dumps(layout)

        async def member_mobile(page):
            state["role"] = "member"
            await page.set_viewport_size({"width": 320, "height": 812})
            await page.goto(BASE + "/#team")
            await expect(
                page.get_by_role("heading", name="My API keys", exact=True)
            ).to_be_visible()
            await expect(
                page.get_by_role("heading", name="No keys yet.", exact=True)
            ).to_be_visible()
            await expect(
                page.get_by_role("button", name="Invite teammate", exact=True)
            ).to_have_count(0)
            await page.get_by_role("button", name="Create key", exact=True).click()
            await expect(
                page.get_by_role("dialog", name="Create an API key", exact=True)
            ).to_be_visible()
            await expect(page.get_by_label("Key owner", exact=True)).to_have_count(0)
            await assert_mobile_layout(page, "member-key-dialog")
            await page.keyboard.press("Escape")
            await expect(page.get_by_role("dialog")).to_have_count(0)
            await page.goto(BASE + "/#connections")
            await expect(
                page.get_by_role("heading", name="Connections", exact=True)
            ).to_be_visible()
            await expect(
                page.get_by_role("heading", name="Store tools", exact=True)
            ).to_be_visible()
            await expect(page.locator(".server-url")).to_have_text(SERVER["url"])
            await expect(
                page.get_by_role("button", name="Add connection", exact=True)
            ).to_have_count(0)
            await expect(page.get_by_role("switch")).to_have_count(0)
            await assert_mobile_layout(page, "member-connections")

        for name, test in [
            ("Connection edits preserve fields changed elsewhere", dirty_patch),
            ("Failed credential updates show the actual disabled connection", credential_failure),
            ("Prototype-named tools retain explicit permission edits", special_tool_names),
            ("A changed endpoint cannot inherit unsaved permission grants", endpoint_drafts),
            ("Connection operations wait for a permission save", permission_serialization),
            ("Invitation recipient and role stay fixed while saving", invitation_lock),
            (
                "Development origins accept the configured canonical gateway invitation",
                canonical_invitation,
            ),
            ("One-time keys survive a failed follow-up list request", key_recovery),
            *[
                (
                    f"Uncertain key creation ({mode}) refreshes and requires recovery",
                    uncertain_key(mode),
                )
                for mode in ["network", "503", "missing"]
            ],
            *[
                (
                    f"Uncertain invitation ({mode}) cannot expose an invalid link or resubmit",
                    uncertain_invite(mode),
                )
                for mode in ["network", "503", "missing", "external"]
            ],
            ("Member dialogs and connection views stay restricted at 320px", member_mobile),
        ]:
            await case(name, test)
        report.update(javascript_errors=errors, unexpected_requests=unexpected)
        report["complete"] = not (report["failures"] or errors or unexpected)
        (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        await browser.close()
        assert report["complete"], report


if __name__ == "__main__":
    asyncio.run(main())

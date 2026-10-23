"""Exercise authentication failures, session races and shared browser controls.

Run against a dashboard preview with HARBOR_URL=http://127.0.0.1:4173.
All API requests are intercepted. No credentials or live changes are used.
"""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from tests.deployed_config import console_url, output_path

ROOT = Path(__file__).resolve().parents[1]
BASE = console_url("http://127.0.0.1:4173")
OUT = output_path(ROOT, "auth-resilience")
ADMIN = {"id": "1", "name": "Admin", "email": "admin@example.test", "role": "admin"}
MEMBER = {"id": "2", "name": "Member", "email": "member@example.test", "role": "member"}


class Fixture:
    def __init__(self):
        self.user = ADMIN
        self.csrf = "fixture-admin"
        self.me_status = 200
        self.invite_status = 200
        self.logout_status = 200
        self.logout_started = asyncio.Event()
        self.logout_release = asyncio.Event()
        self.logout_release.set()
        self.logout_count = 0
        self.pause_status = 200
        self.pause_started = asyncio.Event()
        self.pause_release = asyncio.Event()
        self.pause_release.set()
        self.unexpected = []
        self.requests = []
        self.server_body = None
        self.me_delay_once = False
        self.me_started = asyncio.Event()
        self.me_release = asyncio.Event()
        self.me_release.set()
        self.delete_started = asyncio.Event()
        self.delete_release = asyncio.Event()
        self.delete_release.set()
        self.auth_body = None

    async def route(self, route):
        path = urlsplit(route.request.url).path
        method = route.request.method
        self.requests.append((method, path, route.request.headers))
        if path == "/api/auth/me":
            identity = {"user": self.user, "csrf_token": self.csrf}
            if self.me_delay_once:
                self.me_delay_once = False
                self.me_started.set()
                await self.me_release.wait()
            if self.me_status != 200:
                await route.fulfill(status=self.me_status, json={"detail": "Session unavailable"})
            else:
                await route.fulfill(json=self.auth_body if self.auth_body is not None else identity)
            return
        if path in {"/api/auth/login", "/api/auth/accept-invite"}:
            self.user, self.csrf = MEMBER, "fixture-member"
            await route.fulfill(
                json=self.auth_body
                if self.auth_body is not None
                else {"user": self.user, "csrf_token": self.csrf}
            )
            return
        if path == "/api/auth/logout":
            self.logout_count += 1
            self.logout_started.set()
            await self.logout_release.wait()
            await route.fulfill(status=self.logout_status, json={"detail": "Sign-out unavailable"})
            return
        if path == "/api/invites/fixture-token":
            if self.invite_status != 200:
                await route.fulfill(
                    status=self.invite_status, json={"detail": "Invitation unavailable"}
                )
            else:
                await route.fulfill(
                    json={
                        "email": MEMBER["email"],
                        "role": "member",
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                )
            return
        if path == "/api/servers/1" and method == "PATCH":
            self.pause_started.set()
            await self.pause_release.wait()
            await route.fulfill(status=self.pause_status, json={"detail": "Expired"})
            return
        if path == "/api/servers/1" and method == "DELETE":
            self.delete_started.set()
            await self.delete_release.wait()
            await route.fulfill(status=503, json={"detail": "Removal unavailable"})
            return
        if path == "/api/servers" and self.server_body is not None:
            await route.fulfill(status=200, content_type="text/html", body=self.server_body)
            return
        fixtures = {
            "/api/health": {
                "status": "ok",
                "chat_configured": True,
                "store_url": "https://store.example.test",
                "gateway_url": "https://gateway.example.test/mcp",
            },
            "/api/servers": [
                {
                    "id": "1",
                    "name": "Private admin catalog",
                    "slug": "catalog",
                    "url": "https://mcp.example.test",
                    "enabled": True,
                    "status": "healthy",
                    "tools": [],
                }
            ],
            "/api/members": [{**ADMIN, "active": True, "created_at": "2026-01-01T00:00:00Z"}],
            "/api/invites": [],
            "/api/keys": [],
        }
        if path in fixtures and method == "GET":
            await route.fulfill(json=fixtures[path])
            return
        self.unexpected.append(f"{method} {path}")
        await route.fulfill(status=500, json={"detail": "Unexpected fixture request"})


async def session_change(page, fixture):
    fixture.user, fixture.csrf = MEMBER, "fixture-member"
    await page.evaluate(
        "window.dispatchEvent(new StorageEvent('storage', {key: 'harbor:session-change', newValue: 'fixture-change'}))"
    )
    await expect(page.locator(".profile-email")).to_have_text(MEMBER["email"])


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"complete": False, "scope": "isolated browser fixtures", "checks": [], "failures": []}
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()

        async def case(name, action, width=1280):
            context = await browser.new_context(viewport={"width": width, "height": 900})
            fixture = Fixture()
            await context.route("**/api/**", fixture.route)
            page = await context.new_page()
            page.set_default_timeout(5000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                await action(page, fixture)
                assert not fixture.unexpected, fixture.unexpected
                report["checks"].append(name)
                print("PASS", name, flush=True)
            except Exception as error:
                report["failures"].append({"check": name, "error": str(error)})
                print("FAIL", name, str(error).splitlines()[0], flush=True)
            finally:
                fixture.logout_release.set()
                fixture.pause_release.set()
                fixture.me_release.set()
                fixture.delete_release.set()
                await context.close()

        async def startup_retry(page, fixture):
            fixture.me_status = 503
            await page.goto(BASE)
            await expect(page.get_by_role("alert")).to_contain_text("Session unavailable")
            fixture.me_status = 200
            await page.get_by_role("button", name="Try again", exact=True).click()
            await expect(page.locator(".profile-email")).to_have_text(ADMIN["email"])

        async def invite_retry(page, fixture):
            fixture.invite_status = 503
            await page.goto(BASE + "/invite/fixture-token")
            await expect(page.get_by_role("alert")).to_contain_text("Invitation unavailable")
            fixture.invite_status = 200
            await page.get_by_role("button", name="Try again", exact=True).click()
            await expect(page.get_by_label("Your name", exact=True)).to_be_visible()

        async def expired_logout(page, fixture):
            fixture.logout_status = 401
            await page.goto(BASE)
            await page.get_by_role("button", name="Sign out", exact=True).click()
            await expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
            await expect(page.locator(".workspace")).to_have_count(0)

        async def failed_logout(page, fixture):
            fixture.logout_status = 503
            await page.goto(BASE)
            await page.get_by_role("button", name="Sign out", exact=True).click()
            await expect(page.get_by_role("alert")).to_have_text("Sign-out unavailable")
            await expect(page.locator(".profile-email")).to_have_text(ADMIN["email"])
            await expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_enabled()

        async def logout_race(page, fixture):
            fixture.logout_release.clear()
            await page.goto(BASE)
            button = page.get_by_role("button", name="Sign out", exact=True)
            await button.click()
            await fixture.logout_started.wait()
            await expect(button).to_be_disabled()
            await session_change(page, fixture)
            fixture.logout_release.set()
            await page.wait_for_timeout(150)
            await expect(page.locator(".profile-email")).to_have_text(MEMBER["email"])
            assert fixture.logout_count == 1

        async def old_mutation_notice(page, fixture):
            fixture.pause_release.clear()
            await page.goto(BASE + "/#connections")
            await page.get_by_role(
                "switch", name="Enable Private admin catalog", exact=True
            ).click()
            await fixture.pause_started.wait()
            await session_change(page, fixture)
            fixture.pause_release.set()
            await page.wait_for_timeout(150)
            await expect(page.locator(".toast")).to_have_count(0)

        async def obsolete_unauthorized(page, fixture):
            fixture.pause_status = 401
            fixture.pause_release.clear()
            await page.goto(BASE + "/#connections")
            await page.get_by_role(
                "switch", name="Enable Private admin catalog", exact=True
            ).click()
            await fixture.pause_started.wait()
            await session_change(page, fixture)
            fixture.pause_release.set()
            await page.wait_for_timeout(150)
            await expect(page.locator(".profile-email")).to_have_text(MEMBER["email"])

        async def explicit_login(page, fixture):
            await page.goto(BASE + "/login")
            await expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
            assert not any(path == "/api/auth/me" for _, path, _ in fixture.requests)
            await page.get_by_label("Email address", exact=True).fill(MEMBER["email"])
            await page.get_by_label("Password", exact=True).fill("fixture-password")
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await expect(page.locator(".profile-email")).to_have_text(MEMBER["email"])
            await page.get_by_role("button", name="Sign out", exact=True).click()
            await expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
            logout = next(
                headers for _, path, headers in fixture.requests if path == "/api/auth/logout"
            )
            assert logout["x-csrf-token"] == "fixture-member"

        async def obsolete_identity(page, fixture):
            await page.goto(BASE)
            await expect(page.locator(".profile-email")).to_have_text(ADMIN["email"])
            fixture.me_delay_once = True
            fixture.me_release.clear()
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await fixture.me_started.wait()
            await session_change(page, fixture)
            fixture.me_release.set()
            await page.wait_for_timeout(150)
            await expect(page.locator(".profile-email")).to_have_text(MEMBER["email"])

        async def unreadable_resource(page, fixture):
            fixture.server_body = ""
            await page.goto(BASE + "/#connections")
            await expect(page.get_by_role("alert")).to_contain_text("unreadable response")
            await expect(page.get_by_text("No connections yet.", exact=True)).to_have_count(0)
            fixture.server_body = "<html>Proxy temporarily unavailable</html>"
            await page.get_by_role("button", name="Try again", exact=True).click()
            await expect(page.get_by_role("alert")).to_contain_text("unreadable response")
            await expect(page.get_by_role("alert")).not_to_contain_text("<html>")
            fixture.server_body = None
            await page.get_by_role("button", name="Try again", exact=True).click()
            await expect(
                page.get_by_role("heading", name="Private admin catalog", exact=True)
            ).to_be_visible()

        async def modal_recovery(page, fixture):
            fixture.delete_release.clear()
            await page.goto(BASE + "/#connections")
            trigger = page.get_by_role("button", name="Remove Private admin catalog", exact=True)
            await trigger.click()
            dialog = page.get_by_role("dialog")
            await expect(dialog).to_be_visible()
            await dialog.get_by_role("button", name="Remove connection", exact=True).click()
            await fixture.delete_started.wait()
            await page.keyboard.press("Escape")
            await expect(dialog).to_be_visible()
            await expect(
                dialog.get_by_role("button", name="Close dialog", exact=True)
            ).to_be_disabled()
            fixture.delete_release.set()
            await expect(dialog.get_by_role("alert")).to_have_text("Removal unavailable")
            await expect(
                dialog.get_by_role("button", name="Remove connection", exact=True)
            ).to_be_enabled()
            await page.keyboard.press("Escape")
            await expect(dialog).to_have_count(0)
            await expect(trigger).to_be_focused()

        async def mobile_navigation(page, fixture):
            await page.goto(BASE)
            trigger = page.get_by_role("button", name="Open navigation", exact=True)
            await trigger.click()
            dialog = page.get_by_role("dialog", name="Workspace navigation")
            await expect(dialog).to_be_visible()
            await expect(page.locator(".main-shell")).to_have_attribute("inert", "")
            await expect(
                page.get_by_role("button", name="Close navigation", exact=True)
            ).to_be_focused()
            await page.keyboard.press("Shift+Tab")
            await expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_focused()
            await page.keyboard.press("Tab")
            await expect(
                page.get_by_role("button", name="Close navigation", exact=True)
            ).to_be_focused()
            await page.keyboard.press("Escape")
            await expect(trigger).to_be_focused()
            await trigger.click()
            await page.set_viewport_size({"width": 1280, "height": 900})
            await expect(dialog).to_have_count(0)
            await expect(page.locator(".main-shell")).not_to_have_attribute("inert", "")
            await expect(page.locator("#main")).to_be_focused()

        async def malformed_auth(page, fixture):
            fixture.auth_body = {"csrf_token": "fixture", "user": None}
            await page.goto(BASE)
            await expect(page.get_by_role("alert")).to_contain_text("invalid sign-in response")
            fixture.auth_body = None
            await page.get_by_role("button", name="Try again", exact=True).click()
            await expect(page.locator(".profile-email")).to_have_text(ADMIN["email"])
            fixture.auth_body = {"csrf_token": "fixture", "user": {**MEMBER, "role": "unknown"}}
            await page.goto(BASE + "/login")
            await page.get_by_label("Email address", exact=True).fill(MEMBER["email"])
            await page.get_by_label("Password", exact=True).fill("fixture-password")
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await expect(page.get_by_role("alert")).to_contain_text("invalid sign-in response")
            await expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_enabled()
            await page.goto(BASE + "/invite/fixture-token")
            await page.get_by_label("Your name", exact=True).fill("Fixture member")
            await page.get_by_label("Create a password").fill("fixture-password")
            await page.get_by_role("button", name="Accept invitation", exact=True).click()
            await expect(page.get_by_role("alert")).to_contain_text("invalid sign-in response")
            await expect(
                page.get_by_role("button", name="Accept invitation", exact=True)
            ).to_be_enabled()

        await case(
            "Round 1: session startup outage retries without re-entering credentials", startup_retry
        )
        await case("Round 1: invitation preview outage retries in place", invite_retry)
        await case("Round 1: expired-session logout clears protected UI", expired_logout)
        await case("Round 1: failed logout retains session and allows retry", failed_logout)
        await case("Round 2: delayed logout cannot clear a newer account", logout_race)
        await case(
            "Round 2: old account mutation cannot notify the newer account", old_mutation_notice
        )
        await case(
            "Round 2: stale unauthorized response cannot expire a newer account",
            obsolete_unauthorized,
        )
        await case("Round 2: explicit account switch uses the new CSRF token", explicit_login)
        await case(
            "Round 2: stale identity refresh cannot restore a previous account", obsolete_identity
        )
        await case(
            "Round 3: empty and HTML API responses report recoverable errors", unreadable_resource
        )
        await case(
            "Round 3: busy dialog prevents dismissal then restores focus after failure",
            modal_recovery,
        )
        await case(
            "Round 3: mobile navigation traps and restores keyboard focus",
            mobile_navigation,
            width=390,
        )
        await case(
            "Round 3: malformed session, login and invitation results remain recoverable",
            malformed_auth,
        )
        report["javascript_errors"] = errors
        report["complete"] = not errors and not report["failures"]
        (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        await browser.close()
        assert report["complete"], report


if __name__ == "__main__":
    asyncio.run(main())

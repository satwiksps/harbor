"""Invitation and account-switching regression against the local deployment.

Run: uv run python -m tests.browser_invitation
Creates two synthetic members, then deactivates them and revokes its temporary key.
Never records passwords, invitation tokens, cookies, or full API keys.
"""

import asyncio
import json
import secrets
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, expect

from tests.deployed_config import console_url, credentials_path, output_path

ROOT = Path(__file__).resolve().parents[1]
BASE = console_url()
STAGE = "setup"


async def sign_in(page, email, password):
    await page.goto(BASE + "/login")
    await page.get_by_label("Email address", exact=True).fill(email)
    await page.get_by_label("Password", exact=True).fill(password)
    await page.get_by_role("button", name="Sign in", exact=True).click()
    await page.locator(".main-shell").wait_for(timeout=30000)


async def accept(page, url, password):
    global STAGE
    scenario = STAGE
    STAGE = scenario + ": open invitation"
    await page.goto(url)
    STAGE = scenario + ": enter name"
    await page.get_by_label("Your name", exact=True).fill("Invitation regression member")
    STAGE = scenario + ": enter password"
    # The wrapping label also contains the password-length hint.
    await page.get_by_label("Create a password").fill(password)
    STAGE = scenario + ": submit acceptance"
    async with page.expect_response(
        lambda r: r.url.endswith("/api/auth/accept-invite") and r.request.method == "POST"
    ) as accepted:
        await page.get_by_role("button", name="Accept invitation", exact=True).click()
    response = await accepted.value
    assert response.ok
    result = await response.json()
    assert result["user"]["role"] == "member" and result["csrf_token"]
    STAGE = scenario + ": enter member workspace"
    await page.locator(".main-shell").wait_for(timeout=30000)
    await expect(page.locator(".topbar").get_by_text("Member access", exact=True)).to_be_visible()
    return result


async def main():
    global STAGE
    credentials = json.loads(credentials_path(ROOT).read_text("utf-8-sig"))
    suffix = secrets.token_hex(5)
    emails = {f"invite-browser-{suffix}-{i}@example.test" for i in range(2)}
    password = secrets.token_urlsafe(24)
    key_ids = []
    checks = []

    def record(name):
        checks.append(name)
        print("PASS", name, flush=True)

    async with httpx.AsyncClient(
        base_url=BASE, timeout=60, trust_env=False, headers={"Origin": BASE}
    ) as admin:
        signed_in = await admin.post(
            "/api/auth/login",
            json={"email": credentials["admin_email"], "password": credentials["admin_password"]},
        )
        signed_in.raise_for_status()
        admin.headers["X-CSRF-Token"] = signed_in.json()["csrf_token"]
        try:
            invitations = []
            for email in sorted(emails):
                response = await admin.post("/api/invites", json={"email": email, "role": "member"})
                response.raise_for_status()
                invitations.append((email, response.json()["invite_url"]))

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                errors = []
                try:
                    context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                    context.on(
                        "page", lambda page: page.on("pageerror", lambda _: errors.append(1))
                    )
                    chat = await context.new_page()
                    STAGE = "administrator browser setup"
                    await sign_in(chat, credentials["admin_email"], credentials["admin_password"])
                    await expect(
                        chat.locator(".topbar").get_by_text("Admin access", exact=True)
                    ).to_be_visible()
                    await chat.get_by_label("Message the Harbor assistant").fill(
                        "Private admin draft"
                    )
                    team = await context.new_page()
                    await team.goto(BASE + "/#team")
                    await expect(
                        team.get_by_role("button", name="Invite teammate", exact=True)
                    ).to_be_visible()
                    unrelated = await browser.new_context()
                    unrelated_page = await unrelated.new_page()
                    await sign_in(
                        unrelated_page,
                        credentials["admin_email"],
                        credentials["admin_password"],
                    )

                    STAGE = "invitation acceptance with an administrator cookie"
                    invited = await context.new_page()
                    email, invitation_url = invitations[0]
                    accepted = await accept(invited, invitation_url, password)
                    assert accepted["user"]["email"] == email
                    await expect(invited.locator(".profile-email")).to_have_text(email)
                    identity = await context.request.get(BASE + "/api/auth/me")
                    assert (await identity.json())["user"]["id"] == accepted["user"]["id"]
                    record("Admin-cookie invitation enters the invited member session")
                    unrelated_identity = await unrelated.request.get(BASE + "/api/auth/me")
                    assert (await unrelated_identity.json())["user"]["email"] == credentials[
                        "admin_email"
                    ]
                    await expect(
                        unrelated_page.locator(".topbar").get_by_text("Admin access", exact=True)
                    ).to_be_visible()
                    record("An unrelated browser profile keeps its administrator session")
                    await unrelated.close()

                    STAGE = "cross-tab identity refresh"
                    for page in [chat, team]:
                        await expect(
                            page.locator(".topbar").get_by_text("Member access", exact=True)
                        ).to_be_visible(timeout=30000)
                        await expect(page.locator(".profile-email")).to_have_text(email)
                        await expect(
                            page.get_by_role("link", name="Team & keys", exact=True)
                        ).to_have_count(0)
                    await expect(chat.get_by_label("Message the Harbor assistant")).to_have_value(
                        ""
                    )
                    await expect(
                        team.get_by_role("heading", name="My API keys", exact=True)
                    ).to_be_visible()
                    await expect(
                        team.get_by_role("button", name="Invite teammate", exact=True)
                    ).to_have_count(0)
                    await expect(
                        team.get_by_role("heading", name="Workspace members", exact=False)
                    ).to_have_count(0)
                    record(
                        "Other tabs discard admin identity, controls, cached team data, and chat draft"
                    )

                    STAGE = "member CSRF and permissions"
                    for endpoint in ["/api/members", "/api/invites"]:
                        denied = await context.request.get(BASE + endpoint)
                        assert denied.status == 403
                    await invited.get_by_role("link", name="My API keys", exact=True).click()
                    await invited.get_by_role("button", name="Create key", exact=True).click()
                    dialog = invited.get_by_role("dialog")
                    await dialog.get_by_label("Key name", exact=True).fill(
                        "Invitation regression key"
                    )
                    async with invited.expect_response(
                        lambda r: r.url.endswith("/api/keys") and r.request.method == "POST"
                    ) as issued:
                        await dialog.get_by_role("button", name="Create key", exact=True).click()
                    result = await issued.value
                    assert result.ok
                    key = await result.json()
                    key_ids.append(key["record"]["id"])
                    assert key["record"]["user_id"] == accepted["user"]["id"]
                    await invited.get_by_role(
                        "button", name="I have saved my key", exact=True
                    ).click()
                    assert key["key"] not in await invited.locator("body").inner_text()
                    await admin.delete(f"/api/keys/{key_ids[-1]}")
                    record(
                        "Invited member has fresh CSRF, owns its new key, and cannot read admin endpoints"
                    )

                    STAGE = "reload and cross-tab logout"
                    await invited.reload()
                    await expect(
                        invited.locator(".topbar").get_by_text("Member access", exact=True)
                    ).to_be_visible(timeout=30000)
                    await invited.get_by_role("button", name="Sign out", exact=True).click()
                    for page in [invited, chat, team]:
                        await expect(
                            page.get_by_role("button", name="Sign in", exact=True)
                        ).to_be_visible(timeout=30000)
                    record("Member session survives reload and logout updates other tabs")

                    STAGE = "already-used invitation recovery"
                    await sign_in(
                        invited, credentials["admin_email"], credentials["admin_password"]
                    )
                    await invited.goto(invitation_url)
                    await invited.get_by_role("alert").wait_for(timeout=30000)
                    identity = await context.request.get(BASE + "/api/auth/me")
                    assert (await identity.json())["user"]["role"] == "admin"
                    await invited.get_by_role(
                        "link", name="Already joined? Sign in to your account"
                    ).click()
                    await expect(
                        invited.get_by_role("button", name="Sign in", exact=True)
                    ).to_be_visible()
                    await invited.get_by_label("Email address", exact=True).fill(email)
                    await invited.get_by_label("Password", exact=True).fill(password)
                    await invited.get_by_role("button", name="Sign in", exact=True).click()
                    await expect(
                        invited.locator(".topbar").get_by_text("Member access", exact=True)
                    ).to_be_visible(timeout=30000)
                    await expect(invited.locator(".profile-email")).to_have_text(email)
                    record(
                        "Used invitation preserves existing session and offers an explicit member sign-in path"
                    )
                    await context.close()

                    STAGE = "fresh-browser invitation acceptance"
                    anonymous = await browser.new_context()
                    page = await anonymous.new_page()
                    result = await accept(page, invitations[1][1], password)
                    assert result["user"]["email"] == invitations[1][0]
                    identity = await anonymous.request.get(BASE + "/api/auth/me")
                    assert (await identity.json())["user"]["id"] == result["user"]["id"]
                    record(
                        "Fresh-browser acceptance enters the invited account without a second sign-in"
                    )
                    await anonymous.close()
                    assert not errors
                    record("No dashboard JavaScript exceptions")
                finally:
                    await browser.close()
        finally:
            for key_id in key_ids:
                await admin.delete(f"/api/keys/{key_id}")
            members = await admin.get("/api/members")
            members.raise_for_status()
            for member in members.json():
                if member["email"] in emails and member["active"]:
                    response = await admin.patch(
                        f"/api/members/{member['id']}", json={"active": False}
                    )
                    response.raise_for_status()
            await admin.post("/api/auth/logout")
    output = output_path(ROOT, "browser-invitation")
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps({"complete": True, "base": BASE, "checks": checks}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"Invitation browser regression failed during {STAGE} ({type(error).__name__}).")
        print(
            "Credential values, response bodies, and page snapshots are intentionally not logged."
        )
        raise SystemExit(1) from None

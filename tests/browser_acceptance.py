"""Real browser journey: chat write, COD checkout, WP admin, then today's orders.

Creates a product and a test order. Credentials and recordings stay Git-excluded.
Run only against this project's local demonstration cluster.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from playwright.async_api import async_playwright, expect

from tests.acceptance import Acceptance, unpack

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://harbor.localhost:8080"
STORE = "http://store.localhost:8080"
OUT = ROOT / ".runtime/browser"


async def login(page, email, password):
    await page.goto(BASE)
    await page.get_by_label("Email address", exact=True).fill(email)
    await page.get_by_label("Password", exact=True).fill(password)
    await page.get_by_role("button", name="Sign in", exact=True).click()
    await expect(page.get_by_role("navigation", name="Main navigation")).to_be_visible(
        timeout=30000
    )


async def chat(page, prompt):
    await page.get_by_label("Message the Harbor assistant").fill(prompt)
    async with page.expect_response(
        lambda r: r.url.endswith("/api/chat") and r.request.method == "POST", timeout=150000
    ) as received:
        await page.get_by_role("button", name="Send message").click()
    response = await received.value
    data = await response.json()
    assert response.ok, data.get("detail", f"Chat HTTP {response.status}")
    return data


def structured(call):
    result = call["result"]
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    for content in result.get("content", []):
        if content.get("type") == "text":
            try:
                return json.loads(content["text"])
            except ValueError:
                pass
    raise AssertionError("Expected a structured tool result")


async def verify_order_tools(credentials, order_id):
    admin, _ = await Acceptance(BASE).login(
        credentials["admin_email"], credentials["admin_password"]
    )
    issued = await admin.post("/api/keys", json={"name": "Browser order verification"})
    issued.raise_for_status()
    key = issued.json()
    try:
        async with (
            httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + key["key"]}, trust_env=False
            ) as transport,
            Client(
                streamable_http_client(BASE + "/mcp", http_client=transport), cache=None
            ) as client,
        ):
            names = [t.name for t in (await client.list_tools()).tools]
            get_order = next(n for n in names if "__get_order_" in n)
            update = next(n for n in names if "__update_order_status_" in n)
            original = await client.call_tool(get_order, {"order_id": order_id})
            assert not original.is_error
            original_data = unpack(original)
            assert original_data["id"] == order_id
            assert "customer@example.test" not in json.dumps(original_data)
            changed = await client.call_tool(update, {"order_id": order_id, "status": "on-hold"})
            assert not changed.is_error and unpack(changed)["status"] == "on-hold"
            restored = await client.call_tool(
                update, {"order_id": order_id, "status": original_data["status"]}
            )
            assert not restored.is_error
            actual = await client.call_tool(get_order, {"order_id": order_id})
            assert unpack(actual)["status"] == original_data["status"]
    finally:
        await admin.delete(f"/api/keys/{key['record']['id']}")
        await admin.aclose()


async def main(audit_only=False):
    OUT.mkdir(parents=True, exist_ok=True)
    credentials = json.loads((ROOT / ".secrets/development.json").read_text("utf-8-sig"))
    # A narrower Activity check must never certify an interrupted full journey.
    report_path = OUT / ("audit-report.json" if audit_only else "report.json")
    report = {
        "timestamp": time.time(),
        "scope": "activity-only" if audit_only else "full-journey",
        "checks": [],
        "complete": False,
    }

    def record(name):
        report["checks"].append(name)
        print("PASS", name, flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 960},
            record_video_dir=str(OUT / "video"),
            record_video_size={"width": 1440, "height": 960},
        )
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await login(page, credentials["admin_email"], credentials["admin_password"])
            if audit_only:
                await page.get_by_role("link", name="Activity", exact=True).click()
                await expect(
                    page.get_by_role("table").get_by_text("create_product", exact=False).first
                ).to_be_visible()
                await page.screenshot(path=str(OUT / "audit.png"), full_page=True)
                record("Tool activity visible in browser")
                assert not errors, errors
                record("No dashboard JavaScript exceptions on audit verification")
                report["complete"] = True
                return
            record("Browser administrator login")
            await page.get_by_role("link", name="Connections", exact=False).first.click()
            await expect(page.get_by_text("Connected", exact=True).first).to_be_visible()
            await page.screenshot(path=str(OUT / "connections.png"), full_page=True)
            await page.get_by_role("link", name="Chat", exact=True).click()
            suffix = time.strftime("%m%d%H%M%S")
            name = "Field notebook"
            sku = "FIELD-" + suffix
            proposal = await chat(
                page,
                f"Create a product named '{name}' priced at INR 249.00 with SKU {sku}. "
                "Description: A simple A5 notebook for everyday ideas. Use the create product tool.",
            )
            assert proposal.get("confirmation"), "Write must wait for approval"
            await expect(page.get_by_role("heading", name="Review this action")).to_be_visible()
            await page.screenshot(path=str(OUT / "write-confirmation.png"), full_page=True)
            async with page.expect_response(
                lambda r: r.url.endswith("/api/chat/confirm"), timeout=60000
            ) as received:
                await page.get_by_role("button", name="Approve & run").click()
            response = await received.value
            completed = await response.json()
            assert response.ok and not completed["tool_calls"][0]["is_error"], completed
            product = structured(completed["tool_calls"][0])
            report["product_id"] = product["id"]
            assert product["name"] == name
            record("Live Gemini product creation after explicit browser confirmation")

            shop = await context.new_page()
            await shop.goto(product["permalink"])
            # Repeated test runs can show another Field notebook in related products.
            await expect(shop.locator(".product_title")).to_have_text(name)
            await shop.get_by_role("button", name="Add to cart", exact=True).click()
            await shop.goto(STORE + "/checkout/")
            await shop.locator("#billing_first_name").fill("Demo")
            await shop.locator("#billing_last_name").fill("Customer")
            await shop.locator("#billing_address_1").fill("12 Example Road")
            await shop.locator("#billing_city").fill("Bengaluru")
            await shop.locator("#billing_state").select_option("KA")
            await shop.locator("#billing_postcode").fill("560038")
            await shop.locator("#billing_phone").fill("9000000000")
            await shop.locator("#billing_email").fill("customer@example.test")
            ship_elsewhere = shop.locator("#ship-to-different-address-checkbox")
            if await ship_elsewhere.count() and await ship_elsewhere.is_checked():
                await ship_elsewhere.uncheck()
            if await shop.locator("#payment_method_cod").is_visible():
                await shop.locator("#payment_method_cod").check()
            # Woo recalculates shipping on address changes; wait for its overlay to clear.
            await expect(shop.locator(".blockUI.blockOverlay")).to_have_count(0, timeout=30000)
            await shop.screenshot(path=str(OUT / "checkout.png"), full_page=True)
            await shop.get_by_role("button", name="Place order", exact=True).click()
            await shop.wait_for_url("**/order-received/**", timeout=60000)
            await expect(
                shop.get_by_text("Thank you. Your order has been received.")
            ).to_be_visible()
            number = await shop.locator(".woocommerce-order-overview__order strong").inner_text()
            report["order_id"] = int(number.strip())
            await shop.screenshot(path=str(OUT / "order-received.png"), full_page=True)
            record("Storefront product and cash-on-delivery checkout")

            wp = await context.new_page()
            await wp.goto(STORE + "/wp-login.php")
            await wp.locator("#user_login").fill(credentials["wordpress_admin_user"])
            await wp.locator("#user_pass").fill(credentials["wordpress_admin_password"])
            await wp.locator("#wp-submit").click()
            await wp.wait_for_url("**/wp-admin/**", timeout=30000)
            await wp.goto(STORE + f"/wp-admin/admin.php?page=wc-orders&action=edit&id={number}")
            await expect(wp.locator("#order_data")).to_be_visible(timeout=30000)
            await expect(wp.locator("body")).to_contain_text(name)
            await wp.screenshot(path=str(OUT / "wordpress-order.png"), full_page=True)
            record("Order visible in authenticated WooCommerce admin")

            await page.bring_to_front()
            answer = await chat(page, "What orders came in today? Call the orders tool.")
            order_calls = [c for c in answer["tool_calls"] if "__list_orders_" in c["name"]]
            assert order_calls and not any(c["is_error"] for c in order_calls)
            assert any(
                order["id"] == int(number)
                for call in order_calls
                for order in structured(call)["orders"]
            ), "New checkout order is missing from today's MCP result"
            assert number in answer["message"], "Chat answer omitted the new order"
            await page.screenshot(path=str(OUT / "chat-orders.png"), full_page=True)
            record("Chat today filter returns the actual checkout order")
            await verify_order_tools(credentials, int(number))
            record("Get order and update status tools verified against the real order")
            await page.get_by_role("link", name="Activity", exact=True).click()
            await expect(
                page.get_by_role("table").get_by_text("create_product", exact=False).first
            ).to_be_visible()
            await page.screenshot(path=str(OUT / "audit.png"), full_page=True)
            record("Tool activity visible in browser")
            assert not errors, errors
            record("No dashboard JavaScript exceptions")
            report["complete"] = True
        except Exception:
            await page.screenshot(path=str(OUT / "failure.png"), full_page=True)
            raise
        finally:
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            await context.close()
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Verify Activity visibility separately from the full journey",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.audit_only))
    except Exception as error:
        # Playwright's full assertion snapshot can include password input values.
        # Keep terminal reports safe even when a sign-in assertion fails.
        print("FAIL browser acceptance:", type(error).__name__, str(error).splitlines()[0][:160])
        sys.exit(1)

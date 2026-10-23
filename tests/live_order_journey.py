"""Five real COD checkouts verified by both chat roles and MCP Inspector.

Run only on a demonstration store. Uses existing products, cancels its own
orders, and revokes its temporary keys. Never resubmits an uncertain checkout.
Evidence and Inspector credentials follow deployed_config's ignored paths.
Use --resume REPORT to continue existing recorded orders after an interruption.
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import time
import traceback
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from playwright.async_api import async_playwright, expect

from tests import external_agent
from tests.acceptance import unpack
from tests.browser_acceptance import structured
from tests.browser_store import StoreAudit, expect_amount
from tests.deployed_config import (
    console_url,
    credentials_path,
    output_path,
    private_path,
    store_url,
)

ROOT = Path(__file__).resolve().parents[1]
BASE, STORE = console_url(), store_url()
CASES = [
    (1440, "KA", [("HARBOR-NOTEBOOK-01", 1)]),
    (390, "KA", [("HARBOR-NOTEBOOK-01", 2)]),
    (768, "AS", [("HARBOR-NOTEBOOK-01", 1)]),
    (320, "KA", [("HARBOR-NOTEBOOK-01", 4)]),
    (1440, "KA", [("HARBOR-BOTTLE-01", 1), ("HARBOR-TOTE-01", 1)]),
]


def assert_order(data, cycle, status=None):
    assert data["id"] == cycle["order_id"], "Wrong order returned"
    assert data["currency"] == "INR"
    assert Decimal(data["total"]) == Decimal(cycle["total"]), "Order total differs"
    assert Decimal(data["shipping_total"]) == Decimal(cycle["shipping"]), "Shipping differs"
    assert data["delivery_region"] == {"country": "IN", "state": cycle["state"]}
    actual = sorted(
        (x["product_id"], x["quantity"], Decimal(x["total"])) for x in data["line_items"]
    )
    expected = sorted((x["id"], x["quantity"], Decimal(x["line_total"])) for x in cycle["products"])
    assert actual == expected, "Order line items differ from checkout"
    if status:
        assert data["status"] == status, "Unexpected order status"
    serialized = json.dumps(data).lower()
    assert all(
        secret not in serialized
        for secret in [
            "@example.test",
            "9000000000",
            "12 example road",
            "billing",
            "first_name",
            "last_name",
        ]
    ), "Customer details escaped the order summary"


def inspector_data(result):
    assert not result.get("isError"), "Inspector returned a tool error"
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content", []):
        if block.get("type") == "text":
            return json.loads(block["text"])
    raise AssertionError("Inspector returned no structured order")


class Journey:
    def __init__(self, resume=None):
        if resume:
            self.path = Path(resume).resolve()
            self.report = json.loads(self.path.read_text())
            assert self.report["base"] == BASE and self.report["store"] == STORE
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + secrets.token_hex(2)
            self.path = output_path(ROOT, "order-journey", stamp, "report.json")
            self.report = {
                "run": stamp,
                "base": BASE,
                "store": STORE,
                "complete": False,
                "cycles": [],
                "javascript_errors": [],
            }
            for number, (width, state, items) in enumerate(CASES, 1):
                self.report["cycles"].append(
                    {
                        "number": number,
                        "width": width,
                        "state": state,
                        "cart": items,
                        "checks": [],
                        "checkout_attempted": False,
                        "complete": False,
                    }
                )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.private = private_path(ROOT, "order-journey-" + self.report["run"])
        self.private.mkdir(parents=True, exist_ok=True)
        external_agent.WORK = self.private
        self.stage = "setup"
        self.identities, self.pages, self.contexts = {}, {}, {}
        self.keys = []
        if self.report.get("failure"):
            self.report.setdefault("previous_attempts", []).append(self.report.pop("failure"))

    def save(self):
        self.report["last_stage"] = self.stage
        self.path.write_text(json.dumps(self.report, indent=2), encoding="utf-8")

    def record(self, cycle, label):
        if label not in cycle["checks"]:
            cycle["checks"].append(label)
        self.save()
        print(f"PASS cycle {cycle['number']}: {label}", flush=True)

    async def api(self, role, path, method="GET", data=None):
        response = await self.contexts[role].request.fetch(
            BASE + "/api" + path,
            method=method,
            data=data,
            headers={"X-CSRF-Token": self.identities[role]["csrf_token"]},
        )
        return response

    async def sign_in(self, browser, role, email, password):
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        self.contexts[role] = context
        page = self.pages[role] = await context.new_page()
        page.on(
            "pageerror",
            lambda _: self.report["javascript_errors"].append({"role": role, "stage": self.stage}),
        )
        await page.goto(BASE + "/login")
        await page.get_by_label("Email address", exact=True).fill(email)
        await page.get_by_label("Password", exact=True).fill(password)
        async with page.expect_response(
            lambda r: urlsplit(r.url).path == "/api/auth/login"
        ) as received:
            await page.get_by_role("button", name="Sign in", exact=True).click()
        response = await received.value
        assert response.status == 200, f"{role} sign-in HTTP {response.status}"
        self.identities[role] = await response.json()
        assert self.identities[role]["user"]["role"] == role
        await expect(page.get_by_label("Message the Harbor assistant")).to_be_enabled()

    async def issue_key(self, role, cycle):
        self.stage = f"cycle {cycle['number']} {role} create key"
        page = self.pages[role]
        await page.goto(BASE + "/#team")
        title = "Team & keys" if role == "admin" else "My API keys"
        await expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
        if role == "member":
            await expect(
                page.get_by_role("button", name="Invite teammate", exact=True)
            ).to_have_count(0)
        await page.get_by_role("button", name="Create key", exact=True).click()
        name = f"Journey {self.report['run']} {cycle['number']} {role}"
        await page.get_by_label("Key name", exact=True).fill(name)
        async with page.expect_response(
            lambda r: urlsplit(r.url).path == "/api/keys" and r.request.method == "POST"
        ) as received:
            await (
                page.get_by_role("dialog")
                .get_by_role("button", name="Create key", exact=True)
                .click()
            )
        response = await received.value
        assert response.status == 201
        key = await response.json()
        self.keys.append((role, key))
        assert key["record"]["user_id"] == self.identities[role]["user"]["id"]
        await expect(page.locator(".secret-box code")).to_have_text(key["key"])
        await page.get_by_role("button", name="I have saved my key", exact=True).click()
        self.record(cycle, role + " creates a personal key through the console")
        return key

    async def checkout(self, browser, cycle):
        if cycle.get("order_id"):
            assert (
                "COD receipt, persisted quantities, shipping, total and empty cart match"
                in cycle["checks"]
            ), "Recorded order lacks completed receipt checks; reconcile before continuing"
            return
        assert not cycle["checkout_attempted"], (
            "Uncertain checkout requires reconciliation; do not resubmit"
        )
        self.stage = f"cycle {cycle['number']} checkout"
        context = await browser.new_context(
            viewport={"width": cycle["width"], "height": 1000}, reduced_motion="reduce"
        )
        page = await context.new_page()
        page.set_default_timeout(30000)

        def remember_receipt(frame):
            if frame != page.main_frame:
                return
            target = urlsplit(frame.url)
            match = re.search(r"/order-received/(\d+)", target.path)
            if target.netloc == urlsplit(STORE).netloc and match:
                cycle["order_id"] = int(match.group(1))
                self.save()

        page.on("framenavigated", remember_receipt)
        page.on(
            "pageerror",
            lambda _: self.report["javascript_errors"].append(
                {"role": "shopper", "stage": self.stage}
            ),
        )
        audit = StoreAudit(checkout=True)
        audit.out = self.path.parent / f"cycle-{cycle['number']}-store"
        audit.out.mkdir(exist_ok=True)
        try:
            products, count = [], 0
            for sku, quantity in cycle["cart"]:
                count += 1
                product = await audit.add_from_catalog(page, sku, count, cycle["width"])
                if quantity != 1:
                    row = await audit.cart_row(page, product)
                    await row.locator("input.qty").fill(str(quantity))
                    await row.locator("input.qty").press("Tab")
                    await page.locator('button[name="update_cart"]').click()
                    await expect_amount(
                        row.locator(".product-subtotal .amount"), product["price"] * quantity
                    )
                    await audit.cart_ready(page)
                    count += quantity - 1
                products.append(
                    {
                        "id": product["id"],
                        "sku": sku,
                        "quantity": quantity,
                        "line_total": str(product["price"] * quantity),
                    }
                )
            await audit.header_count(page, count)
            subtotal = sum(Decimal(p["line_total"]) for p in products)
            shipping = (
                Decimal(0)
                if subtotal >= 1500
                else Decimal(99 if cycle["state"] == "AS" else 59) + (count - 1) * 20
            )
            cycle.update(
                products=products,
                subtotal=str(subtotal),
                shipping=str(shipping),
                total=str(subtotal + shipping),
            )
            self.save()
            await page.reload(wait_until="domcontentloaded")
            await audit.header_count(page, count)
            await expect_amount(page.locator(".cart-subtotal .amount"), subtotal)
            await page.locator("a.checkout-button").click()
            await expect(page.locator("form.checkout")).to_be_visible()
            remote = cycle["state"] == "AS"
            for identifier, value in {
                "billing_first_name": "Harbor",
                "billing_last_name": f"Test {self.report['run']} {cycle['number']}",
                "billing_address_1": "12 Example Road",
                "billing_city": "Guwahati" if remote else "Bengaluru",
                "billing_postcode": "781001" if remote else "560038",
                "billing_phone": "9000000000",
                "billing_email": f"journey-{self.report['run']}-{cycle['number']}@example.test",
            }.items():
                await page.locator("#" + identifier).fill(value)
            elsewhere = page.locator("#ship-to-different-address-checkbox")
            if await elsewhere.count() and await elsewhere.is_checked():
                await elsewhere.uncheck()
            async with page.expect_response(
                lambda r: "wc-ajax=update_order_review" in r.url and r.request.method == "POST"
            ) as recalculation:
                await page.locator("#billing_state").select_option(cycle["state"])
                await page.locator("#billing_email").press("Tab")
            assert (await recalculation.value).ok
            await audit.cart_ready(page)
            await expect_amount(page.locator(".cart-subtotal .amount"), subtotal)
            await expect_amount(page.locator(".order-total .amount"), subtotal + shipping)
            if shipping:
                await expect_amount(page.locator(".woocommerce-shipping-totals .amount"), shipping)
            cod = page.locator("#payment_method_cod")
            if await cod.is_visible():
                await cod.check()
            assert await cod.is_checked()
            await audit.capture(page, "ready", cycle["width"])
            cycle["checkout_attempted"] = True
            self.save()
            async with page.expect_response(
                lambda r: "wc-ajax=checkout" in r.url and r.request.method == "POST", timeout=60000
            ) as received:
                await page.get_by_role("button", name="Place order", exact=True).click()
            response = await received.value
            assert response.ok, f"Checkout HTTP {response.status}"
            await page.wait_for_url("**/order-received/**", timeout=60000)
            remember_receipt(page.main_frame)
            assert cycle.get("order_id"), "Checkout did not reach its receipt"
            await expect(page.locator(".woocommerce-order-overview__order strong")).to_have_text(
                str(cycle["order_id"])
            )
            await expect_amount(
                page.locator(".woocommerce-order-overview__total .amount"), subtotal + shipping
            )
            await audit.header_count(page, 0)
            await audit.capture(page, "receipt", cycle["width"])
            self.record(
                cycle, "COD receipt, persisted quantities, shipping, total and empty cart match"
            )
        except Exception:
            remember_receipt(page.main_frame)
            await page.screenshot(path=str(audit.out / "failure.png"), full_page=True)
            raise
        finally:
            await context.close()

    async def inspector(self, role, key, method, name=None, arguments=None):
        config = self.private / f"{role}.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "harbor": {
                            "type": "http",
                            "url": BASE + "/mcp",
                            "protocolEra": "auto",
                            "headers": {"Authorization": "Bearer " + key["key"]},
                            "connectionTimeout": 15000,
                            "requestTimeout": 25000,
                        }
                    }
                }
            )
        )
        os.chmod(config, 0o600)
        command = external_agent.inspector_command() + [
            "--cli",
            "--config",
            str(config),
            "--server",
            "harbor",
            "--format",
            "json",
            "--quiet",
            "--stored-auth-only",
            "--method",
            method,
        ]
        if name:
            command += ["--tool-name", name, "--tool-args-json", json.dumps(arguments)]
        try:
            result = await asyncio.to_thread(
                external_agent.run_process, command, external_agent.child_environment(), 60
            )
            assert result["return_code"] == 0, "MCP Inspector process failed"
            value = json.loads(result["stdout"])
            return value.get("result", value)
        finally:
            config.unlink(missing_ok=True)

    async def chat(self, role, prompt, cycle, fresh=True):
        page = self.pages[role]
        if fresh:
            await page.goto(BASE + "/#chat")
            # Reload discards earlier in-memory conversations, not server sessions.
            await page.reload()
        composer = page.get_by_label("Message the Harbor assistant")
        await expect(composer).to_be_enabled(timeout=30000)
        await composer.fill(prompt)
        for attempt in range(3):
            started = time.monotonic()
            async with page.expect_response(
                lambda r: urlsplit(r.url).path == "/api/chat", timeout=180000
            ) as received:
                if attempt:
                    await page.get_by_role("button", name="Retry message", exact=True).click()
                else:
                    await page.get_by_role("button", name="Send message", exact=True).click()
            response = await received.value
            cycle.setdefault("chat_requests", []).append(
                {
                    "role": role,
                    "status": response.status,
                    "duration_seconds": round(time.monotonic() - started, 2),
                }
            )
            self.save()
            if response.status in {429, 503, 504}:
                delay = int(
                    response.headers.get("retry-after", "60" if response.status == 429 else "5")
                )
                cycle.setdefault("quota_waits", []).append({"role": role, "seconds": delay})
                self.save()
                assert delay <= 180 and attempt < 2, "Chat unavailable within bounded retry budget"
                print(
                    f"WAIT cycle {cycle['number']}: {role} chat HTTP {response.status}, retry after {delay}s",
                    flush=True,
                )
                await asyncio.sleep(delay + 1)
                continue
            assert response.status == 200, f"{role} chat HTTP {response.status}"
            result = await response.json()
            evidence = (
                self.path.parent
                / f"cycle-{cycle['number']}-{role}-chat-{len(cycle['chat_requests'])}.json"
            )
            evidence.write_text(json.dumps(result, indent=2), encoding="utf-8")
            await expect(page.locator(".message.assistant").last).to_be_visible()
            return result
        raise AssertionError("Chat did not complete")

    async def revoke(self, role, key, cycle):
        page = self.pages[role]
        await page.goto(BASE + "/#team")
        await page.get_by_role("button", name="Revoke " + key["record"]["name"], exact=True).click()
        async with page.expect_response(
            lambda r: (
                urlsplit(r.url).path == "/api/keys/" + key["record"]["id"]
                and r.request.method == "DELETE"
            )
        ) as received:
            await page.get_by_role("button", name="Revoke key", exact=True).click()
        assert (await received.value).status == 200
        async with httpx.AsyncClient(trust_env=False, timeout=30) as http:
            denied = await http.post(
                BASE + "/mcp",
                headers={
                    "Authorization": "Bearer " + key["key"],
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        assert denied.status_code == 401, "Revoked key still authorizes MCP"
        self.record(cycle, role + " revokes its key in the UI; MCP rejects it")

    async def cycle(self, browser, cycle):
        await self.checkout(browser, cycle)
        cycle["cleanup_status"] = "pending"
        self.save()
        for page in self.pages.values():
            await page.set_viewport_size({"width": cycle["width"], "height": 1000})
        keys = {role: await self.issue_key(role, cycle) for role in ["admin", "member"]}
        prior_audit = {}
        for role in keys:
            response = await self.api(role, "/audit?limit=100")
            assert response.ok
            prior_audit[role] = {row["id"] for row in (await response.json())["items"]}
        async with AsyncExitStack() as stack:
            clients, names = {}, {}
            for role in keys:
                transport = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        headers={"Authorization": "Bearer " + keys[role]["key"]}, trust_env=False
                    )
                )
                clients[role] = await stack.enter_async_context(
                    Client(streamable_http_client(BASE + "/mcp", http_client=transport), cache=None)
                )
                listing = await self.inspector(role, keys[role], "tools/list")
                names[role] = [tool["name"] for tool in listing["tools"]]
                assert all(
                    any(name.startswith(slug + "__") for name in names[role])
                    for slug in ("woo", "shipping", "policies")
                )
            read = next(n for n in names["admin"] if "__get_order_" in n)
            listing = next(n for n in names["member"] if "__list_orders_" in n)
            write = next(n for n in names["admin"] if "__update_order_status_" in n)
            assert all(
                "__create_product_" not in n and "__update_order_status_" not in n
                for n in names["member"]
            )
            order_id = cycle["order_id"]
            original = unpack(await clients["admin"].call_tool(read, {"order_id": order_id}))
            assert_order(original, cycle)
            try:
                self.stage = f"cycle {cycle['number']} admin chat"
                answer = await self.chat(
                    "admin",
                    f"Show order {order_id} using get_order. Include its status, total, shipping amount and product quantities.",
                    cycle,
                )
                calls = [c for c in answer["tool_calls"] if "__get_order_" in c["name"]]
                assert calls and all(not c["is_error"] for c in calls), (
                    "Admin chat did not retrieve the order"
                )
                assert_order(structured(calls[-1]), cycle, original["status"])
                change = await self.chat(
                    "admin", f"Change order {order_id} to on-hold.", cycle, fresh=False
                )
                proposal = change.get("confirmation")
                assert proposal and proposal["tool_name"] == write
                assert proposal["arguments"] == {"order_id": order_id, "status": "on-hold"}
                assert_order(
                    unpack(await clients["admin"].call_tool(read, {"order_id": order_id})),
                    cycle,
                    original["status"],
                )
                blocked = await self.api(
                    "member", "/chat/confirm", "POST", {"id": proposal["id"], "approve": True}
                )
                assert blocked.status == 404
                approve = cycle["number"] != 2
                page = self.pages["admin"]
                async with page.expect_response(
                    lambda r: urlsplit(r.url).path == "/api/chat/confirm", timeout=60000
                ) as received:
                    await page.get_by_role(
                        "button", name="Approve & run" if approve else "Cancel action", exact=True
                    ).click()
                result = await received.value
                assert result.status == 200
                confirmed = await result.json()
                if approve:
                    assert (
                        len(confirmed["tool_calls"]) == 1
                        and not confirmed["tool_calls"][0]["is_error"]
                    )
                else:
                    assert not confirmed["tool_calls"]
                expected_status = "on-hold" if approve else original["status"]
                assert_order(
                    unpack(await clients["admin"].call_tool(read, {"order_id": order_id})),
                    cycle,
                    expected_status,
                )
                replay = await self.api(
                    "admin", "/chat/confirm", "POST", {"id": proposal["id"], "approve": True}
                )
                assert replay.status == 404
                self.record(
                    cycle,
                    "Admin chat reads the receipt order; approval isolation, "
                    + ("execution" if approve else "cancellation")
                    + " and replay rejection pass",
                )

                self.stage = f"cycle {cycle['number']} member chat"
                member_answer = await self.chat(
                    "member",
                    f"Call get_order for order {order_id}. Report its ID, status, INR total, shipping amount and each product quantity. Do not change anything.",
                    cycle,
                )
                calls = [c for c in member_answer["tool_calls"] if "__get_order_" in c["name"]]
                assert calls and not member_answer.get("confirmation")
                assert all(not c["is_error"] for c in calls)
                assert_order(structured(calls[-1]), cycle, expected_status)
                assert str(order_id) in member_answer["message"]
                self.record(cycle, "Member chat reads the same order, quantities and amounts")

                self.stage = f"cycle {cycle['number']} external clients"
                for role in keys:
                    data = inspector_data(
                        await self.inspector(
                            role, keys[role], "tools/call", read, {"order_id": order_id}
                        )
                    )
                    assert_order(data, cycle, expected_status)
                    listed = await clients[role].call_tool(
                        listing, {"period": "today", "per_page": 50}
                    )
                    assert not listed.is_error
                    orders = unpack(listed)
                    assert any(o["id"] == order_id for o in orders["orders"]), (
                        "Today's orders omitted new checkout"
                    )
                    self.record(
                        cycle,
                        role + " MCP Inspector retrieves the exact order; today's list includes it",
                    )
                    quote_name = next(n for n in names[role] if "__quote_shipping_" in n)
                    quote = await clients[role].call_tool(
                        quote_name,
                        {
                            "subtotal": cycle["subtotal"],
                            "country": "IN",
                            "state": cycle["state"],
                            "weight_kg": str(
                                sum(p["quantity"] for p in cycle["products"]) * Decimal("0.5")
                            ),
                        },
                    )
                    assert not quote.is_error
                    assert Decimal(unpack(quote)["amount"]) == Decimal(cycle["shipping"])
                    policy_name = next(n for n in names[role] if "__get_policy_" in n)
                    policy = await clients[role].call_tool(policy_name, {"topic": "shipping"})
                    assert not policy.is_error
                    assert Decimal(unpack(policy)["free_shipping_threshold"]) == Decimal("1500")
                    self.record(
                        cycle, role + " shipping and policy MCP services agree with checkout"
                    )
                denied = await clients["member"].call_tool(
                    write, {"order_id": order_id, "status": expected_status}
                )
                assert denied.is_error
                assert_order(
                    unpack(await clients["admin"].call_tool(read, {"order_id": order_id})),
                    cycle,
                    expected_status,
                )
                self.record(cycle, "Member write tools are hidden and direct invocation is denied")
                changed = await clients["admin"].call_tool(
                    write, {"order_id": order_id, "status": "processing"}
                )
                assert not changed.is_error
                assert_order(unpack(changed), cycle, "processing")
                self.record(cycle, "Administrator external MCP status change succeeds")

                for role in keys:
                    response = await self.api(
                        role,
                        "/audit?limit=100&user_id=" + self.identities["admin"]["user"]["id"]
                        if role == "member"
                        else "/audit?limit=100",
                    )
                    assert response.ok
                    rows = [
                        row
                        for row in (await response.json())["items"]
                        if row["id"] not in prior_audit[role]
                    ]
                    user_id = self.identities[role]["user"]["id"]
                    assert any(
                        r["user_id"] == user_id
                        and r["tool_name"] == read
                        and r["outcome"] == "success"
                        for r in rows
                    )
                    if role == "member":
                        assert all(r["user_id"] == user_id for r in rows)
                        assert any(
                            r["tool_name"] == write and r["outcome"] == "denied" for r in rows
                        )
                    page = self.pages[role]
                    await page.goto(BASE + "/#audit")
                    await expect(
                        page.get_by_role("heading", name="Activity", exact=True)
                    ).to_be_visible()
                    await expect(page.locator(".audit-table")).to_contain_text("get_order")
                    await page.evaluate("document.fonts.ready")
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    await page.screenshot(
                        path=str(self.path.parent / f"cycle-{cycle['number']}-{role}-activity.png"),
                        full_page=True,
                    )
                self.record(
                    cycle,
                    "Both Activity views attribute calls correctly; member cannot read administrator activity",
                )
            finally:
                cancelled = await clients["admin"].call_tool(
                    write, {"order_id": order_id, "status": "cancelled"}
                )
                assert not cancelled.is_error
                assert_order(unpack(cancelled), cycle, "cancelled")
                cycle["cleanup_status"] = "cancelled"
                self.save()
        for role in keys:
            await self.revoke(role, keys[role], cycle)
        cycle["complete"] = True
        self.record(cycle, "Synthetic order cancelled; cycle complete")

    async def cleanup_orders(self):
        """Cover failures before a cycle's MCP clients or confirmation are ready."""
        pending = [
            c
            for c in self.report["cycles"]
            if c.get("order_id") and c.get("cleanup_status") != "cancelled"
        ]
        if not pending or "admin" not in self.identities:
            return
        issued = await self.api(
            "admin", "/keys", "POST", {"name": "Journey cleanup " + self.report["run"]}
        )
        assert issued.status == 201
        key = await issued.json()
        self.keys.append(("admin", key))
        async with (
            httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + key["key"]}, trust_env=False
            ) as transport,
            Client(
                streamable_http_client(BASE + "/mcp", http_client=transport), cache=None
            ) as client,
        ):
            names = [tool.name for tool in (await client.list_tools()).tools]
            write = next(name for name in names if "__update_order_status_" in name)
            for cycle in pending:
                result = await client.call_tool(
                    write, {"order_id": cycle["order_id"], "status": "cancelled"}
                )
                assert not result.is_error
                data = unpack(result)
                assert data["id"] == cycle["order_id"] and data["status"] == "cancelled"
                cycle["cleanup_status"] = "cancelled"
                self.save()

    async def run(self):
        credentials = json.loads(credentials_path(ROOT).read_text("utf-8-sig"))
        member = json.loads(private_path(ROOT, "demo-member.json").read_text("utf-8-sig"))
        self.save()
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                await self.sign_in(
                    browser, "admin", credentials["admin_email"], credentials["admin_password"]
                )
                await self.sign_in(browser, "member", member["email"], member["password"])
                for cycle in self.report["cycles"]:
                    if not cycle["complete"]:
                        await self.cycle(browser, cycle)
                assert not self.report["javascript_errors"]
                self.report["complete"] = all(c["complete"] for c in self.report["cycles"])
            except Exception as error:
                self.report["failure"] = {
                    "stage": self.stage,
                    "type": type(error).__name__,
                    "message": str(error)[:500]
                    if isinstance(error, AssertionError)
                    else "See private execution context",
                }
                self.report["failure"]["trace"] = traceback.format_tb(error.__traceback__)
                sensitive = [credentials.get("admin_password"), member.get("password")]
                sensitive.extend(key["key"] for _, key in self.keys)
                diagnostic = external_agent.redact(
                    "".join(traceback.format_exception(error)), sensitive
                )
                (self.private / "last-error.txt").write_text(diagnostic, encoding="utf-8")
                raise
            finally:
                try:
                    await self.cleanup_orders()
                except Exception:
                    self.report.setdefault("cleanup_errors", []).append(
                        "Order cancellation failed; reconcile recorded IDs"
                    )
                for role, key in self.keys:
                    try:
                        response = await self.api(role, "/keys/" + key["record"]["id"], "DELETE")
                        assert response.status in {200, 404}
                    except Exception:
                        self.report.setdefault("cleanup_errors", []).append(
                            "Key revocation: " + key["record"]["id"]
                        )
                for role, context in self.contexts.items():
                    try:
                        await self.api(role, "/auth/logout", "POST")
                    except Exception:
                        self.report.setdefault("cleanup_errors", []).append(
                            "Session logout: " + role
                        )
                    finally:
                        await context.close()
                for config in self.private.glob("*.json"):
                    config.unlink()
                await browser.close()
                if self.report.get("cleanup_errors"):
                    self.report["complete"] = False
                self.save()
        assert self.report["complete"], "Journey or cleanup remains incomplete"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume", type=Path, help="Continue this run without repeating recorded checkouts"
    )
    arguments = parser.parse_args()
    journey = Journey(arguments.resume)
    print("Evidence:", journey.path, flush=True)
    try:
        asyncio.run(journey.run())
    except Exception as error:
        print(
            f"Journey stopped at {journey.stage}: {type(error).__name__}. See private report.",
            flush=True,
        )
        raise SystemExit(1) from None

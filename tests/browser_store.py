"""Exercise the real storefront without an LLM or administrator credentials.

Default: use isolated carts at desktop and mobile widths, validate checkout, and
leave every cart empty. No products or orders are created. Requires the published
Harbor bottle and notebook starter products with their standard shipping weights.

Optional: ``uv run python -m tests.browser_store --checkout`` places exactly one
synthetic cash-on-delivery order after the cart checks. Use only the demonstration
deployment. A failed/uncertain checkout is never resubmitted automatically.

Targets and private evidence directories follow tests.deployed_config. Reports
retain the attempted checkout and any completed order ID even if a later check
fails. Each invocation has a separate evidence directory.
"""

import argparse
import asyncio
import json
import re
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from tests.deployed_config import output_path, store_url

ROOT = Path(__file__).resolve().parents[1]
STORE = store_url()
BOTTLE = "HARBOR-BOTTLE-01"
NOTEBOOK = "HARBOR-NOTEBOOK-01"
WIDTHS = (1440, 390, 320)


def amount(text):
    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", text)
    assert match, "Expected a visible currency amount"
    return Decimal(match.group().replace(",", ""))


async def expect_amount(locator, expected):
    formatted = re.escape(f"{expected:,.2f}").replace(",", ",?")
    await expect(locator).to_have_text(re.compile(rf"^\D*{formatted}\D*$"))


class StoreAudit:
    def __init__(self, checkout):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.out = output_path(ROOT, "store-browser", stamp)
        self.out.mkdir(parents=True, exist_ok=True)
        self.stage = "setup"
        self.report = {
            "timestamp": stamp,
            "store": STORE,
            "scope": "shopping-and-cod-checkout" if checkout else "shopping-without-order",
            "complete": False,
            "checkout_attempted": False,
            "checks": [],
            "captures": [],
            "javascript_errors": [],
        }

    def save(self):
        (self.out / "report.json").write_text(
            json.dumps(self.report, indent=2), encoding="utf-8"
        )

    def record(self, name, width):
        self.report["checks"].append({"name": name, "width": width})
        self.save()
        print(f"PASS {width}px: {name}", flush=True)

    async def capture(self, page, name, width):
        await page.evaluate("document.fonts.ready")
        geometry = await page.evaluate("""() => ({
            viewport: innerWidth,
            content: document.documentElement.scrollWidth
        })""")
        assert geometry["content"] <= geometry["viewport"], f"Overflow on {name}"
        await page.screenshot(path=str(self.out / f"{name}-{width}.png"), full_page=True)
        self.report["captures"].append({"name": name, **geometry})
        self.save()

    async def header_count(self, page, expected):
        link = page.locator(".harbor-cart-link")
        await expect(link).to_be_visible()
        await expect(link.locator(".harbor-cart-count")).to_have_text(str(expected))
        await expect(link).to_have_attribute("href", re.compile(r"/cart/?(?:\?.*)?$"))

    async def cart_ready(self, page):
        await expect(page.locator(".blockUI.blockOverlay")).to_have_count(0, timeout=30000)

    async def cart_row(self, page, product):
        return page.locator("tr.cart_item").filter(
            has=page.locator(f'a.remove[data-product_id="{product["id"]}"]')
        )

    async def cart_controls(self, page):
        for remove in await page.locator("tr.cart_item a.remove").all():
            box = await remove.bounding_box()
            assert box and box["width"] >= 44 and box["height"] >= 44, (
                "Cart remove action needs a 44px touch target"
            )
        checkout = page.locator("a.checkout-button")
        await expect(checkout).to_be_visible()
        layout = await checkout.evaluate("""button => {
            const style = getComputedStyle(button);
            const box = button.getBoundingClientRect();
            const verticalSpace = ['paddingTop', 'paddingBottom', 'borderTopWidth', 'borderBottomWidth']
                .reduce((total, property) => total + parseFloat(style[property]), 0);
            return {
                contentHeight: box.height - verticalSpace,
                lineHeight: parseFloat(style.lineHeight),
                contentWidth: button.clientWidth,
                scrollWidth: button.scrollWidth
            };
        }""")
        assert layout["contentHeight"] <= layout["lineHeight"] + 2, (
            "Checkout label and arrow should fit together on one line"
        )
        assert layout["scrollWidth"] <= layout["contentWidth"], (
            "Checkout action must not clip its label or arrow"
        )

    async def browse_catalog(self, page):
        width = 1440
        self.stage = "desktop product search"
        await page.goto(STORE + "/", wait_until="domcontentloaded")
        bottle = page.locator("li.product").filter(
            has=page.locator(f'a[data-product_sku="{BOTTLE}"]')
        )
        product_url = await bottle.locator(".woocommerce-LoopProduct-link").get_attribute("href")
        assert product_url, "Seed product has no detail link"
        term = "harbor-no-match-q7z"
        search = page.locator('.harbor-header-search input[type="search"]')
        await search.fill(term)
        await search.press("Enter")
        heading = page.locator("h1.woocommerce-products-header__title")
        await expect(heading).to_contain_text(term)
        await expect(heading).to_contain_text(re.compile("search results", re.IGNORECASE))
        await expect(page.locator(".harbor-eyebrow, .harbor-intro")).to_have_count(0)
        await expect(page.locator("ul.products li.product")).to_have_count(0)
        await expect(page.locator(".woocommerce-info")).to_contain_text(
            re.compile("no products", re.IGNORECASE)
        )
        await self.capture(page, "search-empty", width)
        self.record("Empty product search shows its query and a clear no-results state", width)

        self.stage = "desktop seed product gallery"
        await page.goto(product_url, wait_until="domcontentloaded")
        gallery = page.locator(".woocommerce-product-gallery")
        image = gallery.locator(".woocommerce-product-gallery__image img").first
        await expect(image).to_be_visible()
        await expect(image).to_have_js_property("complete", True)
        assert await image.evaluate("image => image.naturalWidth > 0"), "Broken product image"
        trigger = gallery.locator(".woocommerce-product-gallery__trigger")
        await expect(trigger).to_be_visible()
        await trigger.focus()
        await trigger.press("Enter")
        lightbox = page.locator(".pswp.pswp--open")
        await expect(lightbox).to_be_visible()
        enlarged = lightbox.locator(".pswp__img:not(.pswp__img--placeholder)").first
        await expect(enlarged).to_be_visible()
        await expect(enlarged).to_have_js_property("complete", True)
        assert await enlarged.evaluate("image => image.naturalWidth > 0"), "Broken enlarged image"
        await self.capture(page, "product-lightbox", width)
        await page.keyboard.press("Escape")
        await expect(lightbox).to_have_count(0)
        await expect(trigger).to_be_focused()
        self.record("Seed artwork opens in a working keyboard-accessible image viewer", width)

    async def add_from_catalog(self, page, sku, count, width, capture=True):
        self.stage = f"{width}px catalog add {sku}"
        await page.goto(STORE + "/", wait_until="domcontentloaded")
        navigation = page.locator(".harbor-shop-nav")
        for name in ("Shop", "My account"):
            link = navigation.get_by_role("link", name=name, exact=True)
            await expect(link).to_be_visible()
            box = await link.bounding_box()
            assert box and box["height"] >= 44, "Header navigation has a short touch target"
            await link.focus()
            await expect(link).to_be_focused()
        button = page.locator(f'a.add_to_cart_button[data-product_sku="{sku}"]')
        await expect(button).to_have_count(1)
        await expect(button).to_be_visible()
        card = page.locator("li.product").filter(has=button)
        product = {
            "sku": sku,
            "id": int(await button.get_attribute("data-product_id")),
            "price": amount(await card.locator(".price .amount").last.inner_text()),
        }
        await button.focus()
        await button.press("Enter")
        await self.header_count(page, count)
        view = card.locator(".harbor-product-actions a.added_to_cart")
        await expect(view).to_have_count(1)
        await expect(view).to_be_visible()
        await expect(view).to_have_attribute("href", re.compile(r"/cart/?(?:\?.*)?$"))
        controls = await card.evaluate("""card => {
            const inspect = selector => {
                const element = card.querySelector(selector);
                const box = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return {
                    x: box.x, y: box.y, right: box.right, bottom: box.bottom,
                    width: box.width, height: box.height,
                    treatment: [style.backgroundColor, style.color, style.borderColor].join('|')
                };
            };
            const box = card.getBoundingClientRect();
            return {
                card: {x: box.x, right: box.right, y: box.y, bottom: box.bottom},
                add: inspect('.harbor-product-actions .add_to_cart_button'),
                view: inspect('.harbor-product-actions .added_to_cart')
            };
        }""")
        add_box, view_box, card_box = controls["add"], controls["view"], controls["card"]
        for box in (add_box, view_box):
            assert box["height"] >= 44 and box["width"] >= 44, "Small shopping tap target"
            assert box["x"] >= card_box["x"] - 1 and box["right"] <= card_box["right"] + 1
            assert box["y"] >= card_box["y"] - 1 and box["bottom"] <= card_box["bottom"] + 1
        overlap_x = min(add_box["right"], view_box["right"]) - max(add_box["x"], view_box["x"])
        overlap_y = min(add_box["bottom"], view_box["bottom"]) - max(add_box["y"], view_box["y"])
        assert overlap_x <= 0 or overlap_y <= 0, "Add and View cart actions overlap"
        assert add_box["treatment"] != view_box["treatment"], "Shopping actions lack hierarchy"
        await button.focus()
        await button.press("Tab")
        await expect(view).to_be_focused()
        focus = await view.evaluate("""element => {
            const style = getComputedStyle(element);
            return {width: parseFloat(style.outlineWidth), style: style.outlineStyle};
        }""")
        assert focus["width"] >= 2 and focus["style"] != "none", "Missing keyboard focus ring"
        if capture:
            await self.capture(page, f"catalog-added-{sku.lower()}", width)
        await view.press("Enter")
        await page.wait_for_url(re.compile(r"/cart/?(?:\?.*)?$"))
        await self.cart_ready(page)
        await expect(await self.cart_row(page, product)).to_be_visible()
        await self.header_count(page, count)
        return product

    async def empty_cart(self, page, width):
        self.stage = f"{width}px empty cart"
        await page.goto(STORE + "/cart/", wait_until="domcontentloaded")
        rows = page.locator("tr.cart_item")
        remaining = await rows.count()
        while remaining:
            await rows.first.locator("a.remove").click()
            await expect(rows).to_have_count(remaining - 1)
            await self.cart_ready(page)
            remaining -= 1
        await expect(page.locator(".cart-empty")).to_be_visible()
        await self.header_count(page, 0)
        await expect(page.locator(".return-to-shop a.wc-backward")).to_be_visible()

    async def checkout_form(self, page, product, width, validate):
        self.stage = f"{width}px checkout"
        await page.locator("a.checkout-button").click()
        await page.wait_for_url(re.compile(r"/checkout/?(?:\?.*)?$"))
        await expect(page.locator("form.checkout")).to_be_visible()
        await self.cart_ready(page)
        if validate:
            await page.locator("#place_order").click()
            notice = page.locator(".woocommerce-NoticeGroup-checkout .woocommerce-error")
            await expect(notice).to_be_visible()
            notice_box = await notice.bounding_box()
            details_box = await page.locator("#customer_details").bounding_box()
            assert notice_box and details_box
            assert notice_box["y"] + notice_box["height"] <= details_box["y"] + 2, (
                "Checkout error summary must appear before the customer fields"
            )
            await expect(page.locator("#billing_first_name")).to_have_attribute(
                "aria-invalid", "true"
            )
            description = await page.locator("#billing_first_name").get_attribute(
                "aria-describedby"
            )
            assert description, "Required field has no associated error explanation"
            for identifier in description.split():
                await expect(page.locator(f'[id="{identifier}"]')).to_be_visible()
            assert "/order-received/" not in page.url, "Invalid checkout created an order"
            await self.capture(page, "checkout-validation", width)
            self.record("Invalid checkout exposes field errors without placing an order", width)

        for identifier, value in {
            "billing_first_name": "Harbor",
            "billing_last_name": "UI review",
            "billing_address_1": "12 Example Road",
            "billing_city": "Bengaluru",
            "billing_postcode": "560038",
            "billing_phone": "9000000000",
            "billing_email": "ui-review@example.test",
        }.items():
            await page.locator(f"#{identifier}").fill(value)
        elsewhere = page.locator("#ship-to-different-address-checkbox")
        if await elsewhere.count() and await elsewhere.is_checked():
            await elsewhere.uncheck()
        async with page.expect_response(
            lambda response: response.request.method == "POST"
            and "wc-ajax=update_order_review" in response.url,
            timeout=30000,
        ) as recalculation:
            await page.locator("#billing_state").select_option("KA")
            await page.locator("#billing_email").press("Tab")
        updated = await recalculation.value
        assert updated.ok, f"Checkout recalculation HTTP {updated.status}"
        await updated.body()
        await self.cart_ready(page)
        await expect_amount(page.locator(".cart-subtotal .amount"), product["price"])
        shipping = Decimal("0") if product["price"] >= 1500 else Decimal("59")
        total = product["price"] + shipping
        await expect_amount(page.locator(".order-total .amount"), total)
        if shipping:
            await expect_amount(page.locator(".woocommerce-shipping-totals .amount"), shipping)
        await expect(page.locator("#payment")).to_contain_text(
            re.compile(r"demonstration|demo", re.IGNORECASE)
        )
        cod = page.locator("#payment_method_cod")
        if await cod.is_visible():
            await cod.check()
        assert await cod.is_checked(), "Expected the demo cash-on-delivery method"
        await self.capture(page, "checkout-ready", width)
        self.record("Checkout recalculates the visible subtotal, shipping and total", width)
        return {"subtotal": str(product["price"]), "shipping": str(shipping), "total": str(total)}

    async def shopping_flow(self, page, width):
        first = await self.add_from_catalog(page, BOTTLE, 1, width)
        self.record("Keyboard catalog add updates cart and exposes a separate View cart link", width)
        second = await self.add_from_catalog(page, NOTEBOOK, 2, width)
        await expect(page.locator("tr.cart_item")).to_have_count(2)
        await expect_amount(
            page.locator(".cart-subtotal .amount"), first["price"] + second["price"]
        )
        self.record("Two different catalog products accumulate in one cart", width)

        self.stage = f"{width}px update quantity"
        first_row = await self.cart_row(page, first)
        await first_row.locator("input.qty").fill("2")
        await first_row.locator("input.qty").press("Tab")
        update = page.locator('button[name="update_cart"]')
        await expect(update).to_be_enabled()
        await update.click()
        await expect_amount(first_row.locator(".product-subtotal .amount"), 2 * first["price"])
        await expect_amount(
            page.locator(".cart-subtotal .amount"), 2 * first["price"] + second["price"]
        )
        await self.cart_ready(page)
        await self.header_count(page, 3)
        await self.cart_controls(page)
        self.record("Cart remove targets and the one-line checkout action remain usable", width)
        await self.capture(page, "cart-updated", width)
        await page.reload(wait_until="domcontentloaded")
        await expect((await self.cart_row(page, first)).locator("input.qty")).to_have_value("2")
        await self.header_count(page, 3)
        self.record("Quantity update changes amounts and header count and survives reload", width)

        self.stage = f"{width}px remove and undo"
        await (await self.cart_row(page, first)).locator("a.remove").click()
        await expect(await self.cart_row(page, first)).to_have_count(0)
        await self.header_count(page, 1)
        undo = page.locator("a.restore-item")
        await expect(undo).to_be_visible()
        await self.capture(page, "cart-removed", width)
        await undo.click()
        await expect((await self.cart_row(page, first)).locator("input.qty")).to_have_value("2")
        await self.header_count(page, 3)
        await expect_amount(
            page.locator(".cart-subtotal .amount"), 2 * first["price"] + second["price"]
        )
        self.record("Remove and Undo restore the same item and quantity", width)
        await self.empty_cart(page, width)
        await self.capture(page, "cart-empty", width)
        await page.locator(".return-to-shop a.wc-backward").click()
        await expect(page.locator("ul.products")).to_be_visible()
        self.record("Empty cart retains a usable return-to-shop action", width)

        product = await self.add_from_catalog(page, NOTEBOOK, 1, width, capture=False)
        await self.checkout_form(page, product, width, validate=True)
        await self.empty_cart(page, width)
        self.record("Cart-only checks leave their isolated cart empty", width)

    async def place_demo_order(self, page):
        width = 1440
        product = await self.add_from_catalog(page, NOTEBOOK, 1, width, capture=False)
        totals = await self.checkout_form(page, product, width, validate=False)
        self.stage = "one synthetic COD checkout"
        self.report["checkout_attempted"] = True
        self.report["checkout_expected"] = {"product_id": product["id"], "quantity": 1, **totals}
        self.save()
        # One submission only. Even a timeout after this point must not trigger a second order.
        await page.locator("#place_order").click()
        await page.wait_for_url("**/order-received/**", wait_until="domcontentloaded", timeout=60000)
        self.remember_receipt(page)
        await self.verify_receipt(page, totals)

    def remember_receipt(self, page):
        redirect = urlsplit(page.url)
        if redirect.netloc != urlsplit(STORE).netloc:
            return
        match = re.search(r"/order-received/(\d+)", redirect.path)
        if match:
            self.report["order_id"] = int(match.group(1))
            self.save()

    async def verify_receipt(self, page, totals):
        redirect = urlsplit(page.url)
        assert redirect.netloc == urlsplit(STORE).netloc and "/order-received/" in redirect.path
        self.remember_receipt(page)
        assert self.report.get("order_id"), "Receipt has no order ID"
        await expect(page.locator(".woocommerce-order-overview__order strong")).to_have_text(
            str(self.report["order_id"])
        )
        await expect_amount(
            page.locator(".woocommerce-order-overview__total .amount"), Decimal(totals["total"])
        )
        await expect(page.locator(".woocommerce-order-overview__payment-method strong")).to_contain_text(
            re.compile(r"cash on delivery", re.IGNORECASE)
        )
        await self.header_count(page, 0)
        await self.capture(page, "order-confirmation", 1440)
        self.record("One synthetic COD order confirms the expected amount and clears the cart", 1440)

    async def run(self, checkout):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                for width in WIDTHS:
                    context = await browser.new_context(
                        viewport={"width": width, "height": 1000 if width == 1440 else 844},
                        reduced_motion="reduce",
                    )
                    try:
                        page = await context.new_page()
                        page.set_default_timeout(30000)
                        page.on("pageerror", lambda _: self.report["javascript_errors"].append(self.stage))
                        if width == 1440:
                            await self.browse_catalog(page)
                        await self.shopping_flow(page, width)
                    except Exception:
                        (self.out / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
                        await page.screenshot(path=str(self.out / f"failure-{width}.png"), full_page=True)
                        raise
                    finally:
                        await context.close()
                if checkout:
                    context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                    try:
                        page = await context.new_page()
                        page.set_default_timeout(30000)
                        page.on("pageerror", lambda _: self.report["javascript_errors"].append(self.stage))
                        await self.place_demo_order(page)
                    except Exception:
                        self.remember_receipt(page)
                        (self.out / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
                        await page.screenshot(path=str(self.out / "checkout-failure.png"), full_page=True)
                        raise
                    finally:
                        await context.close()
                assert not self.report["javascript_errors"], "Storefront JavaScript exceptions were observed"
                self.report["complete"] = True
            finally:
                self.report["last_stage"] = self.stage
                self.save()
                await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout", action="store_true",
        help="Also place one synthetic cash-on-delivery order in this demonstration store",
    )
    args = parser.parse_args()
    audit = StoreAudit(args.checkout)
    print(f"Storefront evidence: {audit.out}", flush=True)
    try:
        asyncio.run(audit.run(args.checkout))
    except Exception as error:
        print(f"Storefront audit failed during {audit.stage} ({type(error).__name__}).")
        raise SystemExit(1) from None

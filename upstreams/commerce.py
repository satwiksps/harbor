"""WooCommerce REST adapter. Store state stays owned by WooCommerce."""

import asyncio
import json
import math
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from oauthlib.oauth1 import SIGNATURE_HMAC_SHA256, SIGNATURE_TYPE_AUTH_HEADER
from oauthlib.oauth1 import Client as OAuthClient


class StoreError(Exception):
    """A bounded, credential-free error safe to expose through a tool."""


MAX_RESPONSE_BYTES = 2_000_000
REQUEST_TIMEOUT_SECONDS = 10


def reject_json_constant(value: str):
    raise ValueError("Non-finite JSON number")


def finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def uncertain_error(message: str, method: str) -> StoreError:
    if method != "GET":
        message += ". Its outcome is unknown. Check the store before retrying"
    return StoreError(message)


def money(value: str, *, allow_zero: bool = True) -> str:
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Provide a decimal price, for example 499.00") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal(10000000):
        raise ValueError("Price must be between 0.00 and 10000000.00")
    if not allow_zero and amount == 0:
        raise ValueError("Price must be greater than zero")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("INR prices support at most two decimal places")
    return format(amount, ".2f")


def today_bounds(timezone: str, now: datetime | None = None) -> tuple[str, str]:
    """Exclusive WC after/before filters covering every second of the store's day."""
    zone = ZoneInfo(timezone)
    now = now.astimezone(zone) if now else datetime.now(zone)
    start = datetime.combine(now.date(), time.min, zone)
    # WC's REST date_query uses strict > and < comparisons, with second precision.
    after = start - timedelta(seconds=1)
    before = start + timedelta(days=1)
    return after.replace(tzinfo=None).isoformat(), before.replace(tzinfo=None).isoformat()


def require_record(item: Any) -> dict:
    if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0:
        raise StoreError("The store returned an invalid record")
    return item


def pagination(items: Any, headers: dict[str, str]) -> tuple[int, int]:
    if not isinstance(items, list):
        raise StoreError("The store returned an invalid collection")
    values = []
    for name, fallback in (("x-wp-total", len(items)), ("x-wp-totalpages", 1 if items else 0)):
        raw = headers.get(name, str(fallback))
        if not raw.isascii() or not raw.isdigit() or len(raw) > 10:
            raise StoreError("The store returned invalid pagination metadata")
        values.append(int(raw))
    return values[0], values[1]


def product_summary(item: dict[str, Any]) -> dict[str, Any]:
    item = require_record(item)
    fields = (
        "id",
        "name",
        "sku",
        "status",
        "type",
        "price",
        "regular_price",
        "currency",
        "permalink",
        "stock_status",
        "stock_quantity",
    )
    return {key: item[key] for key in fields if key in item}


def order_summary(item: dict[str, Any]) -> dict[str, Any]:
    item = require_record(item)
    lines = item.get("line_items", [])
    shipping = item.get("shipping", {})
    if not isinstance(lines, list) or any(not isinstance(line, dict) for line in lines):
        raise StoreError("The store returned invalid order line items")
    if not isinstance(shipping, dict):
        raise StoreError("The store returned an invalid delivery region")
    fields = (
        "id",
        "number",
        "status",
        "date_created",
        "date_created_gmt",
        "total",
        "currency",
        "payment_method_title",
        "shipping_total",
    )
    result = {key: item[key] for key in fields if key in item}
    result["line_items"] = [
        {key: line.get(key) for key in ("product_id", "name", "sku", "quantity", "total")}
        for line in lines
    ]
    # Delivery region is useful to quotes; names, emails and street addresses are not needed.
    result["delivery_region"] = {key: shipping.get(key, "") for key in ("country", "state")}
    return result


class WooCommerce:
    def __init__(
        self,
        url: str,
        key: str,
        secret: str,
        public_url: str = "",
        timezone: str = "Asia/Kolkata",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.url = url.rstrip("/")
        public = urlsplit(public_url or url)
        # WC signs against home_url, even when an internal service address transports the request.
        self.signing_url = urlunsplit(
            (urlsplit(url).scheme, public.netloc, public.path, "", "")
        ).rstrip("/")
        self.key, self.secret, self.timezone, self.transport = key, secret, timezone, transport

    async def request(
        self, method: str, path: str, params: dict | None = None, body: dict | None = None
    ) -> tuple[Any, dict[str, str]]:
        if not self.key or not self.secret:
            raise StoreError("Store credentials have not been configured")
        suffix = f"/wp-json/wc/v3/{path}"
        query = urlencode(params or {})
        suffix += "?" + query if query else ""
        signing_url = self.signing_url + suffix
        signer = OAuthClient(
            self.key,
            client_secret=self.secret,
            signature_method=SIGNATURE_HMAC_SHA256,
            signature_type=SIGNATURE_TYPE_AUTH_HEADER,
        )
        # JSON payloads are not form-urlencoded OAuth signature parameters.
        _, headers, _ = signer.sign(
            signing_url, http_method=method, headers={"Content-Type": "application/json"}
        )
        headers["Accept-Encoding"] = "identity"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS), httpx.AsyncClient(
                timeout=httpx.Timeout(8, connect=3),
                follow_redirects=False,
                transport=self.transport,
                trust_env=False,
            ) as client:
                async with client.stream(
                    method, self.url + suffix, headers=headers, json=body
                ) as response:
                    if response.is_redirect:
                        raise StoreError(
                            "The store redirected the API request. Check the configured store URLs"
                        )
                    encoding = response.headers.get("content-encoding", "identity")
                    if encoding.strip().lower() not in {"", "identity"}:
                        raise uncertain_error(
                            "Compressed store responses are disabled to enforce the size limit",
                            method,
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise uncertain_error(
                                "Store response is too large. Request fewer items", method
                            )
                        content.extend(chunk)
        except (TimeoutError, httpx.TimeoutException):
            raise uncertain_error("Store request timed out", method) from None
        except httpx.HTTPError:
            raise uncertain_error("The store connection failed", method) from None
        if response.status_code >= 400:
            # Do not relay HTML, arbitrary upstream messages, Authorization headers or request URLs.
            code = "store_error"
            try:
                candidate = json.loads(content).get("code", code)
                if isinstance(candidate, str) and candidate.replace("_", "").isalnum():
                    code = candidate[:80]
            except (ValueError, AttributeError, RecursionError):
                pass
            if response.status_code >= 500:
                raise uncertain_error(
                    f"WooCommerce failed to complete the request ({response.status_code}, {code})",
                    method,
                )
            raise StoreError(f"WooCommerce rejected the request ({response.status_code}, {code})")
        try:
            payload = json.loads(
                content, parse_constant=reject_json_constant, parse_float=finite_json_float
            )
            return payload, dict(response.headers)
        except (ValueError, RecursionError):
            raise uncertain_error("The store returned an invalid JSON response", method) from None

    async def list_products(self, search: str = "", page: int = 1, per_page: int = 20) -> dict:
        items, headers = await self.request(
            "GET",
            "products",
            {
                "search": search,
                "page": page,
                "per_page": per_page,
                "orderby": "id",
                "order": "desc",
            },
        )
        total, total_pages = pagination(items, headers)
        return {
            "products": [product_summary(x) for x in items],
            "page": page,
            "total": total,
            "total_pages": total_pages,
        }

    async def create_product(
        self, name: str, price: str, description: str = "", sku: str | None = None
    ) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("Product name must not be blank")
        body = {
            "name": name,
            "type": "simple",
            "status": "publish",
            "regular_price": money(price),
            "description": description,
            "virtual": False,
            "tax_status": "none",
        }
        if sku and sku.strip():
            # A unique SKU also allows the caller to reconcile an ambiguous create timeout.
            body["sku"] = sku.strip()
        item, _ = await self.request("POST", "products", body=body)
        try:
            return product_summary(item)
        except StoreError as error:
            raise uncertain_error(str(error), "POST") from None

    async def list_orders(
        self, period: str = "today", status: str = "any", page: int = 1, per_page: int = 20
    ) -> dict:
        params: dict[str, Any] = {
            "page": page,
            "per_page": per_page,
            "orderby": "date",
            "order": "desc",
        }
        if status != "any":
            params["status"] = status
        if period == "today":
            params["after"], params["before"] = today_bounds(self.timezone)
        items, headers = await self.request("GET", "orders", params)
        total, total_pages = pagination(items, headers)
        return {
            "orders": [order_summary(x) for x in items],
            "page": page,
            "total": total,
            "total_pages": total_pages,
            "store_timezone": self.timezone,
            "period": period,
            "after": params.get("after"),
            "before": params.get("before"),
        }

    async def get_order(self, order_id: int) -> dict:
        item, _ = await self.request("GET", f"orders/{order_id}")
        return order_summary(item)

    async def update_order_status(self, order_id: int, status: str) -> dict:
        item, _ = await self.request("PUT", f"orders/{order_id}", body={"status": status})
        try:
            return order_summary(item)
        except StoreError as error:
            raise uncertain_error(str(error), "PUT") from None

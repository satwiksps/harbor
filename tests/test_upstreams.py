import asyncio
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from upstreams.commerce import (
    MAX_RESPONSE_BYTES,
    StoreError,
    WooCommerce,
    money,
    order_summary,
    today_bounds,
)
from upstreams.rules import get_policy, quote_shipping
from upstreams.server import build_server, create_app


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1.001", "10000001", "oops"])
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValueError):
        money(value)


def test_decimal_money_is_exact():
    assert money("0.10") == "0.10"
    assert money("499") == "499.00"


def test_today_uses_store_day_at_utc_boundary():
    start, end = today_bounds("Asia/Kolkata", datetime(2026, 10, 14, 19, tzinfo=UTC))
    assert start == "2026-10-14T23:59:59"
    assert end == "2026-10-16T00:00:00"


async def test_today_orders_include_both_midnight_boundaries(monkeypatch):
    bounds = today_bounds("Asia/Kolkata", datetime(2026, 10, 15, 12, tzinfo=UTC))
    monkeypatch.setattr("upstreams.commerce.today_bounds", lambda _: bounds)
    dates = [
        "2026-10-14T23:59:59",
        "2026-10-15T00:00:00",
        "2026-10-15T23:59:59",
        "2026-10-16T00:00:00",
    ]

    def handler(request):
        query = dict(request.url.params)
        assert query["page"] == "2" and query["per_page"] == "10"
        assert query["status"] == "processing"
        assert query["orderby"] == "date" and query["order"] == "desc"
        # WooCommerce's date_query uses exclusive comparisons, as does WP_Date_Query.
        orders = [
            {"id": number, "date_created": date}
            for number, date in enumerate(dates, 1)
            if query["after"] < date < query["before"]
        ]
        return httpx.Response(200, json=orders)

    wc = WooCommerce("http://wordpress", "key", "secret", transport=httpx.MockTransport(handler))
    result = await wc.list_orders(status="processing", page=2, per_page=10)
    assert [order["id"] for order in result["orders"]] == [2, 3]


async def test_all_orders_omits_date_and_any_status_filters():
    def handler(request):
        assert not ({"after", "before", "status"} & set(request.url.params))
        return httpx.Response(200, json=[], headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"})

    wc = WooCommerce("http://wordpress", "key", "secret", transport=httpx.MockTransport(handler))
    result = await wc.list_orders(period="all")
    assert result["orders"] == [] and result["total_pages"] == 0


def test_order_projection_excludes_customer_pii():
    summary = order_summary(
        {
            "id": 1,
            "billing": {"email": "private@example.test"},
            "shipping": {"address_1": "Hidden", "country": "IN", "state": "KA"},
        }
    )
    assert "private" not in str(summary)
    assert "Hidden" not in str(summary)
    assert summary["delivery_region"] == {"country": "IN", "state": "KA"}


def test_shipping_boundary_and_half_kg_rounding():
    assert quote_shipping("1499.99")["amount"] == "59.00"
    assert quote_shipping("1500.00")["amount"] == "0.00"
    assert quote_shipping("100", weight_kg="0.51")["amount"] == "79.00"
    assert quote_shipping("100", state="AS")["amount"] == "99.00"
    assert quote_shipping("100", country="US")["available"] is False
    assert get_policy("shipping")["free_shipping_threshold"] == "1500.00"


async def test_wc_signed_request_pagination_and_error_redaction():
    calls = []

    def handler(request):
        calls.append(request)
        assert "consumer_secret" not in str(request.url)
        assert "test-secret" not in str(request.headers)
        assert request.headers["authorization"].startswith("OAuth ")
        return httpx.Response(
            200,
            json=[{"id": 1, "name": "Tea", "regular_price": "99.00"}],
            headers={"X-WP-Total": "25", "X-WP-TotalPages": "3"},
        )

    wc = WooCommerce(
        "http://wordpress",
        "ck_test",
        "test-secret",
        "http://store.harbor.test:8080",
        transport=httpx.MockTransport(handler),
    )
    result = await wc.list_products(page=2, per_page=10)
    assert result["total_pages"] == 3 and result["page"] == 2
    assert parse_qs(urlsplit(str(calls[0].url)).query)["page"] == ["2"]


async def test_write_timeout_reports_unknown_outcome_without_retry():
    count = 0

    def timeout(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("private details")

    wc = WooCommerce(
        "http://wordpress", "ck_test", "secret", transport=httpx.MockTransport(timeout)
    )
    with pytest.raises(StoreError, match="outcome is unknown"):
        await wc.create_product("Tea", "99.00")
    assert count == 1


@pytest.mark.parametrize("error", [httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError])
async def test_write_transport_failure_reports_unknown_outcome_without_retry(error):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise error("private upstream details")

    wc = WooCommerce("http://wordpress", "key", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(StoreError, match="outcome is unknown") as raised:
        await wc.update_order_status(1, "completed")
    assert calls == 1 and "private" not in str(raised.value)


@pytest.mark.parametrize(
    "status,body",
    [
        (500, '{"code":"internal_error"}'),
        (201, "invalid JSON"),
        (201, '{"missing":"id"}'),
    ],
)
async def test_unconfirmed_write_response_is_ambiguous(status, body):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, text=body)

    wc = WooCommerce("http://wordpress", "key", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(StoreError, match="outcome is unknown"):
        await wc.create_product("Tea", "99.00")
    assert calls == 1


async def test_product_write_normalizes_fields_and_rejects_blank_name():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 3, **calls[-1]})

    wc = WooCommerce("http://wordpress", "key", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="must not be blank"):
        await wc.create_product(" \n\t ", "99.00")
    assert not calls
    await wc.create_product("  Tea  ", "0.10", sku="  TEA-01 ")
    assert calls[0]["name"] == "Tea" and calls[0]["regular_price"] == "0.10"
    assert calls[0]["sku"] == "TEA-01"
    await wc.create_product("Coffee", "100", sku="  ")
    assert "sku" not in calls[1]


@pytest.mark.parametrize(
    ("payload", "headers"),
    [
        ({"private": "customer details"}, {}),
        ([None], {}),
        ([{"id": True}], {}),
        ([{"id": -1}], {}),
        ([{"id": 1}], {"X-WP-Total": "private details"}),
        ([{"id": 1}], {"X-WP-TotalPages": "-1"}),
    ],
)
async def test_malformed_collection_or_pagination_has_safe_error(payload, headers):
    wc = WooCommerce(
        "http://wordpress",
        "key",
        "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload, headers=headers)),
    )
    with pytest.raises(StoreError, match="invalid") as raised:
        await wc.list_products()
    assert "private" not in str(raised.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 1, "shipping": None},
        {"id": 1, "line_items": {}},
        {"id": 1, "line_items": [None]},
    ],
)
def test_malformed_order_has_safe_error(payload):
    with pytest.raises(StoreError, match="invalid"):
        order_summary(payload)


async def test_oversized_response_stops_reading_and_closes_stream():
    class OversizedStream(httpx.AsyncByteStream):
        reads = 0
        closed = False

        async def __aiter__(self):
            for chunk in (b"[", b" " * MAX_RESPONSE_BYTES, b"must not be consumed"):
                self.reads += 1
                yield chunk

        async def aclose(self):
            self.closed = True

    stream = OversizedStream()
    wc = WooCommerce(
        "http://wordpress",
        "key",
        "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
    )
    with pytest.raises(StoreError, match="too large"):
        await wc.list_products()
    assert stream.reads == 2 and stream.closed


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_slow_response_has_total_deadline_and_closes_stream(monkeypatch, method):
    class SlowStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr("upstreams.commerce.REQUEST_TIMEOUT_SECONDS", 0.04, raising=False)
    stream = SlowStream()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)

    wc = WooCommerce("http://wordpress", "key", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(StoreError, match="timed out") as raised:
        async with asyncio.timeout(0.5):
            await wc.request(method, "products")
    assert stream.closed and len(requests) == 1
    assert ("outcome is unknown" in str(raised.value)) == (method == "POST")


@pytest.mark.parametrize(
    "body",
    [b'[{"id":1,"price":NaN}]', b'[{"id":1,"price":1e999}]', b"[" * 10000 + b"]" * 10000],
    ids=["non-finite-number", "overflowing-number", "excessive-nesting"],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_unsafe_json_is_rejected_with_safe_write_outcome(body, method):
    wc = WooCommerce(
        "http://wordpress", "key", "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    )
    with pytest.raises(StoreError, match="invalid JSON") as raised:
        await wc.request(method, "products")
    assert ("outcome is unknown" in str(raised.value)) == (method == "POST")


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_compressed_store_response_is_rejected_before_decoding(method):
    class CompressedStream(httpx.AsyncByteStream):
        read = False
        closed = False

        async def __aiter__(self):
            self.read = True
            yield b"must not be decoded"

        async def aclose(self):
            self.closed = True

    stream = CompressedStream()

    def handler(request):
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream)

    wc = WooCommerce("http://wordpress", "key", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(StoreError, match="Compressed store responses") as raised:
        await wc.request(method, "products")
    assert not stream.read and stream.closed
    assert ("outcome is unknown" in str(raised.value)) == (method == "POST")


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (401, {"code": "woocommerce_rest_cannot_view", "message": "private details"}, "401"),
        (500, {"code": "private details", "message": "private details"}, "store_error"),
        (200, "private invalid JSON", "invalid JSON"),
        (302, "private redirect", "redirected"),
    ],
)
async def test_http_errors_do_not_expose_arbitrary_content(status, payload, expected):
    def handler(request):
        if isinstance(payload, dict):
            return httpx.Response(status, json=payload)
        return httpx.Response(status, text=payload, headers={"Location": "http://private.example"})

    wc = WooCommerce("http://wordpress", "key", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(StoreError, match=expected) as raised:
        await wc.list_products()
    assert "private" not in str(raised.value)


async def test_authentication_cannot_be_skipped_for_mcp():
    app = create_app("policies", "a" * 32)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.post("/mcp", json={})).status_code == 401
        assert (
            await client.post("/mcp", json={}, headers={b"Authorization": b"Bearer \xff"})
        ).status_code == 401


async def test_real_tool_schemas_and_name_collision():
    woo = await build_server("woo").list_tools()
    names = {tool.name for tool in woo}
    assert names == {
        "list_products",
        "create_product",
        "list_orders",
        "get_order",
        "update_order_status",
    }
    policies = await build_server("policies").list_tools()
    shipping = await build_server("shipping").list_tools()
    assert "search" in {t.name for t in policies} & {t.name for t in shipping}

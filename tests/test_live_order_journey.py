"""Offline safeguards for the live journey's evidence and synthetic cleanup."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests import live_order_journey as journey


def receipt():
    cycle = {
        "order_id": 73,
        "state": "KA",
        "total": "777.00",
        "shipping": "79.00",
        "products": [{"id": 15, "quantity": 2, "line_total": "698.00"}],
    }
    order = {
        "id": 73,
        "currency": "INR",
        "total": "777.00",
        "shipping_total": "79.00",
        "delivery_region": {"country": "IN", "state": "KA"},
        "line_items": [{"product_id": 15, "quantity": 2, "total": "698.00"}],
        "status": "processing",
    }
    return cycle, order


def test_receipt_comparison_accepts_equivalent_decimal_amounts():
    cycle, order = receipt()
    order.update(total="777", shipping_total="79.0")
    journey.assert_order(order, cycle, "processing")


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 74),
        ("currency", "USD"),
        ("total", "778.00"),
        ("shipping_total", "59.00"),
        ("delivery_region", {"country": "IN", "state": "AS"}),
        ("status", "cancelled"),
        ("line_items", [{"product_id": 15, "quantity": 1, "total": "698.00"}]),
        ("line_items", [{"product_id": 16, "quantity": 2, "total": "698.00"}]),
        ("line_items", [{"product_id": 15, "quantity": 2, "total": "349.00"}]),
    ],
)
def test_receipt_comparison_rejects_wrong_order_or_amounts(field, value):
    cycle, order = receipt()
    order[field] = value
    with pytest.raises(AssertionError):
        journey.assert_order(order, cycle, "processing")


@pytest.mark.parametrize("private_value", ["person@example.test", "12 Example Road", "9000000000"])
def test_receipt_comparison_rejects_customer_details(private_value):
    cycle, order = receipt()
    order["unexpected_customer_data"] = private_value
    with pytest.raises(AssertionError, match="Customer details"):
        journey.assert_order(order, cycle)


async def test_uncertain_checkout_never_opens_another_cart():
    runner = journey.Journey.__new__(journey.Journey)
    browser = SimpleNamespace(new_context=AsyncMock())
    with pytest.raises(AssertionError, match="Uncertain checkout"):
        await runner.checkout(browser, {"checkout_attempted": True})
    browser.new_context.assert_not_awaited()


async def test_verified_recorded_checkout_is_not_submitted_again():
    runner = journey.Journey.__new__(journey.Journey)
    browser = SimpleNamespace(new_context=AsyncMock())
    cycle = {
        "order_id": 73,
        "checkout_attempted": True,
        "receipt_verified": True,
        "complete": True,
        "checks": ["COD receipt, persisted quantities, shipping, total and empty cart match"],
    }
    await runner.checkout(browser, cycle)
    browser.new_context.assert_not_awaited()


async def test_recorded_order_without_receipt_evidence_cannot_certify_a_resumed_cycle():
    runner = journey.Journey.__new__(journey.Journey)
    browser = SimpleNamespace(new_context=AsyncMock())
    cycle = {"order_id": 73, "checkout_attempted": True, "checks": []}
    with pytest.raises(AssertionError, match="lacks completed receipt checks"):
        await runner.checkout(browser, cycle)
    browser.new_context.assert_not_awaited()


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_):
        return False


def cleanup_runner(cycles):
    runner = journey.Journey.__new__(journey.Journey)
    runner.report = {"run": "offline-test", "cycles": deepcopy(cycles)}
    runner.identities = {"admin": {}}
    runner.keys = []
    key = {"key": "fixture-token", "record": {"id": "fixture-key"}}
    runner.api = AsyncMock(return_value=SimpleNamespace(status=201, json=AsyncMock(return_value=key)))
    runner.save = Mock()
    return runner


async def test_cleanup_only_cancels_known_unfinished_synthetic_orders(monkeypatch):
    runner = cleanup_runner(
        [
            {"number": 1, "order_id": 73},
            {"number": 2, "order_id": 74, "cleanup_status": "cancelled"},
            {"number": 3, "checkout_attempted": True},
            {"number": 4, "order_id": 75},
        ]
    )

    async def cancel(name, arguments):
        return SimpleNamespace(
            is_error=False,
            structured_content={"id": arguments["order_id"], "status": "cancelled"},
        )

    client = SimpleNamespace(
        list_tools=AsyncMock(
            return_value=SimpleNamespace(
                tools=[SimpleNamespace(name="woo__update_order_status_hash")]
            )
        ),
        call_tool=AsyncMock(side_effect=cancel),
    )
    monkeypatch.setattr(journey.httpx2, "AsyncClient", lambda **_: AsyncContext(object()))
    monkeypatch.setattr(journey, "streamable_http_client", lambda *_, **__: object())
    monkeypatch.setattr(journey, "Client", lambda *_, **__: AsyncContext(client))
    await runner.cleanup_orders()

    assert [call.args[1] for call in client.call_tool.await_args_list] == [
        {"order_id": 73, "status": "cancelled"},
        {"order_id": 75, "status": "cancelled"},
    ]
    assert len(runner.keys) == 1, "Cleanup key must remain tracked for revocation"
    assert runner.report["cycles"][0]["cleanup_status"] == "cancelled"
    assert runner.report["cycles"][3]["cleanup_status"] == "cancelled"
    assert "cleanup_status" not in runner.report["cycles"][2]


async def test_cleanup_does_not_create_a_key_without_a_known_pending_order():
    runner = cleanup_runner(
        [{"checkout_attempted": True}, {"order_id": 73, "cleanup_status": "cancelled"}]
    )
    await runner.cleanup_orders()
    runner.api.assert_not_awaited()
    assert runner.keys == []

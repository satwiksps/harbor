"""Versioned example store policies and shipping rates. No live carrier claims."""

from decimal import ROUND_CEILING, Decimal, InvalidOperation

from upstreams.commerce import money

POLICY_VERSION = "2026-10-15"
POLICIES = {
    "shipping": {
        "title": "Delivery and free shipping",
        "currency": "INR",
        "free_shipping_threshold": "1500.00",
        "country": "IN",
        "dispatch_business_days": 1,
        "text": "Orders of INR 1500.00 or more ship free within India. Standard dispatch is within one business day. International delivery is not offered by this sample store.",
    },
    "returns": {
        "title": "Returns",
        "window_days": 14,
        "text": "Unused items in their original packaging may be returned within 14 days of delivery. Contact the store with the order number. Refunds are reviewed by a person.",
    },
    "fulfillment": {
        "title": "Order handling",
        "text": "Processing orders are waiting to be packed. Completed orders have been handed over for delivery. Cash on delivery orders are confirmed at checkout; payment is collected at delivery.",
    },
}


def get_policy(topic: str) -> dict:
    if topic not in POLICIES:
        raise ValueError("Choose shipping, returns, or fulfillment")
    return {
        "topic": topic,
        "version": POLICY_VERSION,
        "source": "Sample store policy",
        **POLICIES[topic],
    }


def search_policies(query: str) -> dict:
    words = query.lower().split()
    matches = [
        get_policy(k)
        for k, v in POLICIES.items()
        if any(w in (k + " " + v["text"]).lower() for w in words)
    ]
    return {"policies": matches, "version": POLICY_VERSION}


def quote_shipping(
    subtotal: str, country: str = "IN", state: str = "KA", weight_kg: str = "0.5"
) -> dict:
    subtotal_value = Decimal(money(subtotal))
    try:
        weight = Decimal(weight_kg)
    except (InvalidOperation, ValueError):
        raise ValueError("Weight must be a decimal in kilograms") from None
    if not weight.is_finite() or weight <= 0 or weight > 30:
        raise ValueError("Weight must be greater than zero and at most 30 kg")
    if country.upper() != "IN":
        return {
            "available": False,
            "reason": "This sample rate table covers India only",
            "rate_version": POLICY_VERSION,
        }
    remote_states = {"AN", "AR", "AS", "LD", "MN", "ML", "MZ", "NL", "SK", "TR"}
    remote = state.upper() in remote_states
    additional = max(Decimal(0), (weight - Decimal("0.5")) / Decimal("0.5"))
    base = Decimal("99.00") if remote else Decimal("59.00")
    amount = base + additional.to_integral_value(rounding=ROUND_CEILING) * Decimal("20.00")
    free = subtotal_value >= Decimal(POLICIES["shipping"]["free_shipping_threshold"])
    return {
        "available": True,
        "currency": "INR",
        "amount": "0.00" if free else money(str(amount)),
        "free_shipping": free,
        "delivery_business_days": {"min": 5 if remote else 2, "max": 8 if remote else 5},
        "country": country.upper(),
        "state": state.upper(),
        "rate_version": POLICY_VERSION,
        "source": "Sample rate table. This is an estimate, not a live carrier quote.",
    }

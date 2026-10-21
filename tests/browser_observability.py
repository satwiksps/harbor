"""Exercise Activity metrics with isolated browser fixtures and no live backend.

Run a dashboard dev server, then HARBOR_URL=http://127.0.0.1:5175
uv run python -m tests.browser_observability. All API calls are intercepted.
"""

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright, expect

from tests.browser_accessibility import text_contrast
from tests.deployed_config import console_url, output_path

ROOT = Path(__file__).resolve().parents[1]
BASE = console_url("http://127.0.0.1:5175")
OUT = output_path(ROOT, "observability")
OUTCOMES = [
    "success",
    "tool_error",
    "error",
    "timeout",
    "pending",
    "denied",
    "invalid_arguments",
    "cancelled",
    "other",
]


def metrics(**overrides):
    return {
        "calls": 0,
        "completed_calls": 0,
        "failed_calls": 0,
        "failure_rate": None,
        "latency_samples": 0,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "outcomes": dict.fromkeys(OUTCOMES, 0),
        **overrides,
    }


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"complete": False, "base": BASE, "scope": "fixture-observability", "checks": []}
    role = "admin"
    mode = "populated"
    summary_requests = []
    history_requests = []
    errors = []
    unexpected = []
    long_name = "Warehouse " + "connection" * 12
    completed = metrics(
        calls=14,
        completed_calls=10,
        failed_calls=3,
        failure_rate=0.3,
        latency_samples=10,
        p50_latency_ms=125.5,
        p95_latency_ms=982.2,
        outcomes={
            "success": 7,
            "tool_error": 1,
            "error": 1,
            "timeout": 1,
            "pending": 1,
            "denied": 1,
            "invalid_arguments": 1,
            "cancelled": 1,
            "other": 0,
        },
    )
    blocked = metrics(calls=2, outcomes={**dict.fromkeys(OUTCOMES, 0), "denied": 2})
    row = {
        "id": "fixture",
        "user_id": "reviewer",
        "user_name": "Reviewer",
        "user_email": "reviewer@example.test",
        "server_name": "Inventory",
        "tool_name": "lookup_inventory",
        "timestamp": "2026-10-20T08:00:00Z",
        "duration_ms": 126,
        "outcome": "success",
        "request_id": "fixture-request-id",
    }

    async def api_fixture(route):
        parsed = urlsplit(route.request.url)
        params = parse_qs(parsed.query)
        if parsed.path == "/api/auth/me":
            body = {
                "user": {
                    "id": "reviewer",
                    "name": "Reviewer",
                    "email": "reviewer@example.test",
                    "role": role,
                },
                "csrf_token": "fixture",
            }
        elif parsed.path == "/api/health":
            body = {
                "status": "ok",
                "chat_configured": False,
                "store_url": "http://store.localhost:8080",
                "gateway_url": "http://harbor.localhost:8080/mcp",
            }
        elif parsed.path == "/api/servers":
            body = [
                {
                    "id": "inventory",
                    "name": "Inventory",
                    "slug": "inventory",
                    "url": "https://example.test/mcp",
                    "enabled": True,
                    "status": "healthy",
                    "tools": [],
                },
                {
                    "id": "blocked",
                    "name": long_name,
                    "slug": "blocked",
                    "url": "https://example.test/mcp",
                    "enabled": True,
                    "status": "healthy",
                    "tools": [],
                },
            ]
        elif parsed.path == "/api/members":
            body = [
                {
                    "id": "reviewer",
                    "name": "Reviewer",
                    "email": "reviewer@example.test",
                    "role": "member",
                    "active": True,
                    "created_at": "2026-10-01T00:00:00Z",
                }
            ]
        elif parsed.path == "/api/audit/summary":
            summary_requests.append(params)
            if mode == "error":
                await route.fulfill(status=503, json={"detail": "Summary temporarily unavailable"})
                return
            empty = mode == "empty" or params.get("window") == ["1h"]
            totals = (
                metrics()
                if empty
                else {
                    **completed,
                    "calls": 16,
                    "outcomes": {**completed["outcomes"], "denied": 3},
                }
            )
            body = {
                "window": params.get("window", ["24h"])[0],
                "from": "2026-10-19T09:00:00Z",
                "to": "2026-10-20T09:00:00Z",
                "scope": "self" if role == "member" else "team",
                "user_id": "reviewer"
                if role == "member"
                else params.get("user_id", [None])[0],
                "server_id": params.get("server_id", [None])[0],
                "totals": totals,
                "servers": []
                if empty
                else [
                    {
                        "server_id": "inventory",
                        "server_name": "Inventory",
                        "server_deleted": False,
                        **completed,
                    },
                    {
                        "server_id": "blocked",
                        "server_name": long_name,
                        "server_deleted": True,
                        **blocked,
                    },
                ],
            }
        elif parsed.path == "/api/audit":
            history_requests.append(params)
            if params.get("outcome") == ["error"]:
                body = {"items": [], "total": 0}
            else:
                item = {**row, "outcome": "pending", "duration_ms": 0} if mode == "pending" else row
                body = {"items": [item], "total": 1}
        else:
            unexpected.append(parsed.path)
            await route.fulfill(status=404, json={"detail": "Unexpected fixture request"})
            return
        await route.fulfill(json=body)

    def check(label):
        report["checks"].append(label)
        print("PASS", label, flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1100}, reduced_motion="reduce"
        )
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.route("**/api/**", api_fixture)
        try:
            await page.goto(BASE + "/#audit")
            overview = page.get_by_role("region", name="Activity overview", exact=True)
            await expect(overview.locator(".activity-metrics")).to_be_visible()
            await expect(overview.locator(".activity-metric-value")).to_have_text(
                ["16", "30%", "125.5 ms", "982.2 ms"]
            )
            await expect(
                overview.get_by_text("3 of 10 completed calls", exact=True)
            ).to_be_visible()
            await expect(overview.get_by_text("10 measured calls", exact=True)).to_have_count(2)
            for label in [
                "Success",
                "Connection error",
                "Tool error",
                "Timed out",
                "In progress",
                "Denied",
                "Invalid arguments",
                "Cancelled",
            ]:
                await expect(
                    overview.locator(".activity-outcomes").get_by_text(label, exact=False)
                ).to_be_visible()
            blocked_row = overview.get_by_role("row").filter(has_text=long_name)
            await expect(blocked_row.get_by_text("No data", exact=True)).to_have_count(3)
            await expect(blocked_row.get_by_text("Removed connection", exact=True)).to_be_visible()
            check(
                "Summary distinguishes outcomes and renders eligible sample counts and null metrics"
            )
            for selector in [
                ".activity-overview-heading p",
                ".activity-shared-filters strong",
                ".activity-summary-meta > span:first-child",
                ".activity-metrics dt",
                ".activity-metric-note",
                ".activity-outcomes-label",
                ".activity-connections-heading > span",
                ".activity-method summary",
                ".activity-metric-value.has-failures",
            ]:
                measured = await text_contrast(overview.locator(selector).first)
                assert measured["ratio"] >= 4.5, (selector, measured)
            check("Overview labels, helper text and failure metrics meet 4.5:1 text contrast")

            await page.get_by_role("button", name="View details for lookup_inventory").click()
            await expect(page.get_by_role("dialog", name="Tool call details")).to_be_visible()
            await expect(page.get_by_text("fixture-request-id", exact=True)).to_be_visible()
            await page.get_by_role("button", name="Close dialog").click()
            check("Existing history details and request identifiers remain available")

            for width in [1440, 768, 390, 320]:
                await page.set_viewport_size({"width": width, "height": 1100})
                await page.evaluate("document.fonts.ready")
                await page.evaluate("window.scrollTo(0, 0)")
                geometry = await page.evaluate(
                    "() => ({viewport: innerWidth, content: document.documentElement.scrollWidth})"
                )
                assert geometry["content"] <= width, geometry
                await expect(
                    page.get_by_role("button", name="Last 24 hours", exact=True)
                ).to_be_visible()
                if width in [1440, 320]:
                    await page.screenshot(path=str(OUT / f"activity-{width}.png"), full_page=True)
            check("Overview fits 320, 390, 768 and 1440 pixel viewports with long connection names")

            await page.set_viewport_size({"width": 1440, "height": 1100})
            before_summary = len(summary_requests)
            await page.get_by_label("Filter by outcome").select_option("error")
            await expect(page.get_by_text("No matching activity.", exact=True)).to_be_visible()
            assert len(summary_requests) == before_summary
            await expect(overview.locator(".activity-metric-value").nth(1)).to_have_text("30%")
            await page.get_by_role("button", name="Clear outcome", exact=True).click()
            await expect(page.get_by_text("lookup_inventory", exact=True)).to_be_visible()
            check("History outcome filters preserve the overview failure-rate denominator")

            async with page.expect_response(lambda r: "/api/audit/summary?" in r.url):
                await page.get_by_label("Filter by connection").select_option("inventory")
            await expect(overview.locator(".activity-metrics")).to_be_visible()
            assert summary_requests[-1]["server_id"] == ["inventory"]
            assert history_requests[-1]["server_id"] == ["inventory"]
            async with page.expect_response(lambda r: "/api/audit/summary?" in r.url):
                await page.get_by_label("Filter by teammate").select_option("reviewer")
            await expect(overview.locator(".activity-metrics")).to_be_visible()
            assert summary_requests[-1]["user_id"] == ["reviewer"]
            assert history_requests[-1]["user_id"] == ["reviewer"]
            await expect(overview.locator(".activity-summary-meta")).to_contain_text(
                "Selected teammate"
            )
            await page.get_by_role("button", name="Last 7 days", exact=True).click()
            await expect(overview.locator(".activity-summary-meta")).to_contain_text("Last 7 days")
            assert summary_requests[-1] == {
                "window": ["7d"],
                "server_id": ["inventory"],
                "user_id": ["reviewer"],
            }
            await page.get_by_role("button", name="Clear filters", exact=True).click()
            await expect(overview.locator(".activity-metrics")).to_be_visible()
            check("Connection and teammate filters reach both endpoints and persist across windows")

            await page.get_by_role("button", name="Last hour", exact=True).click()
            await expect(
                overview.get_by_text("No calls in this window.", exact=True)
            ).to_be_visible()
            await expect(overview.locator(".activity-metric-value")).to_have_text(
                ["0", "No data", "No data", "No data"]
            )
            await expect(page.get_by_text("lookup_inventory", exact=True)).to_be_visible()
            check(
                "An empty time window has no invented rate or latency and leaves all-time history intact"
            )
            assert all("window" not in query for query in history_requests)
            assert all("outcome" not in query for query in summary_requests)

            await page.get_by_role("button", name="Last 24 hours", exact=True).click()
            await expect(overview.locator(".activity-metrics")).to_be_visible()
            mode = "error"
            await page.get_by_role("button", name="Refresh", exact=True).click()
            await expect(overview.get_by_role("alert")).to_contain_text(
                "Summary temporarily unavailable"
            )
            await expect(overview.locator(".activity-metrics")).to_have_count(0)
            await expect(page.get_by_text("lookup_inventory", exact=True)).to_be_visible()
            mode = "populated"
            await overview.get_by_role("button", name="Try again", exact=True).click()
            await expect(overview.locator(".activity-metrics")).to_be_visible()
            check("Failed refresh hides stale metrics, preserves history and recovers on retry")

            mode = "pending"
            await page.get_by_role("button", name="Refresh", exact=True).click()
            await expect(page.locator(".audit-table .duration-cell")).to_have_text("In progress")
            await page.get_by_role("button", name="View details for lookup_inventory").click()
            duration = page.locator(".detail-list > div").filter(
                has=page.get_by_text("Duration", exact=True)
            )
            await expect(duration.locator("dd")).to_have_text("In progress")
            await page.get_by_role("button", name="Close dialog").click()
            check("In-progress calls show no invented zero duration in history or details")

            mode = "populated"
            role = "member"
            await page.reload()
            await expect(overview.locator(".activity-summary-meta")).to_contain_text(
                "Your activity"
            )
            await expect(page.get_by_label("Filter by teammate")).to_have_count(0)
            await overview.get_by_text("How these metrics are measured", exact=True).click()
            await expect(
                overview.get_by_text("It is not the upstream server", exact=False)
            ).to_be_visible()
            check("Member view identifies personal scope and omits team filtering")
            assert not errors, errors
            assert not unexpected, unexpected
            report["complete"] = True
        finally:
            report["page_errors"] = errors
            report["unexpected_requests"] = unexpected
            (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

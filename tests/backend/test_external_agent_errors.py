import json
import sys

import httpx2
from google.genai.errors import APIError
from mcp.shared.exceptions import MCPError

from examples import ask_gateway


def test_nested_provider_failure_retains_status_without_response_details():
    secret = "synthetic-provider-key-never-print"
    provider_error = APIError(
        429,
        {"error": {"message": secret, "status": "RESOURCE_EXHAUSTED"}},
    )
    error = ExceptionGroup("transport wrapper", [ExceptionGroup("inner", [provider_error])])

    report = ask_gateway.failure_report(error)

    assert report["passed"] is False
    assert report["error"] == "APIError"
    assert report["provider_status"] == 429
    assert report["errors"][0]["provider_status"] == 429
    assert report["detail"]
    assert "gateway_status" not in report["errors"][0]
    assert secret not in json.dumps(report)


def test_nested_gateway_auth_failure_reports_http_status_without_request_details():
    secret = "synthetic-member-key-never-print"
    request = httpx2.Request(
        "POST",
        f"https://example.invalid/mcp?token={secret}",
        headers={"Authorization": f"Bearer {secret}"},
    )
    response = httpx2.Response(401, request=request, text=secret)
    gateway_error = httpx2.HTTPStatusError(secret, request=request, response=response)
    error = ExceptionGroup("transport wrapper", [ExceptionGroup("inner", [gateway_error])])

    report = ask_gateway.failure_report(error)

    assert report["error"] == "HTTPStatusError"
    assert report["provider_status"] is None
    assert report["errors"][0]["gateway_status"] == 401
    assert "provider_status" not in report["errors"][0]
    assert report["detail"]
    assert secret not in json.dumps(report)
    assert "example.invalid" not in json.dumps(report)


def test_nested_catalog_error_preserves_safe_local_guidance():
    detail = "Gateway repeated a catalog cursor. Refresh the catalog."
    error = ExceptionGroup("outer", [ExceptionGroup("inner", [ask_gateway.CatalogError(detail)])])

    report = ask_gateway.failure_report(error)

    assert report["error"] == "CatalogError"
    assert report["provider_status"] is None
    assert report["detail"] == detail
    assert report["errors"] == [{"error": "CatalogError", "detail": detail}]


def test_mcp_error_code_is_not_misreported_as_a_provider_status():
    secret = "untrusted-mcp-error-detail-never-print"
    error = MCPError(429, secret, data={"authorization": secret})

    report = ask_gateway.failure_report(error)

    assert report["error"] == "MCPError"
    assert report["provider_status"] is None
    assert "provider_status" not in report["errors"][0]
    assert secret not in json.dumps(report)


def test_sibling_errors_are_preserved_in_order_without_raw_messages():
    secret = "synthetic-secret-in-wrapper-and-error"
    error = ExceptionGroup(
        secret,
        [
            RuntimeError(secret),
            ExceptionGroup("nested", [APIError(503, {"error": {"message": secret}})]),
            TimeoutError(secret),
        ],
    )

    report = ask_gateway.failure_report(error)

    assert report["error"] == "RuntimeError"
    assert [item["error"] for item in report["errors"]] == [
        "RuntimeError", "APIError", "TimeoutError"
    ]
    assert report["errors"][1]["provider_status"] == 503
    assert all(item["detail"] for item in report["errors"])
    assert secret not in json.dumps(report)


def test_main_emits_safe_json_and_failure_exit_for_wrapped_provider_error(monkeypatch, capsys):
    secret = "synthetic-provider-error-message-never-print"

    async def fail(*args, **kwargs):
        raise ExceptionGroup("transport wrapper", [APIError(429, {"message": secret})])

    monkeypatch.setattr(ask_gateway, "ask", fail)
    monkeypatch.setattr(sys, "argv", ["ask_gateway.py", "--prompt", "Read the shipping policy"])

    assert ask_gateway.main() == 1

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["passed"] is False
    assert report["error"] == "APIError"
    assert report["provider_status"] == 429
    assert report["errors"][0]["provider_status"] == 429
    assert secret not in captured.out + captured.err

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from backend import chat
from backend.gateway import tool_alias
from tests.backend.test_backend import environment as environment
from tests.backend.test_backend import seed_upstream


def answer(parts):
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def provider_error(status):
    return httpx.Response(status, json={"error": {
        "code": status,
        "message": "private-provider-key and confidential tool data",
        "status": "UNAVAILABLE" if status == 503 else "PROVIDER_FAILURE",
    }})


@pytest.fixture
def provider_wire(environment, monkeypatch):
    """Run the pinned SDK against an in-memory HTTP transport, without network calls."""
    app, _ = environment
    app.state.settings.gemini_api_key = "private-provider-key"
    original_client = chat.genai.Client

    def install(responses):
        requests = []

        async def handle(request):
            requests.append(json.loads(request.content))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response if isinstance(response, httpx.Response) else httpx.Response(
                200, json=response
            )

        def client(**kwargs):
            kwargs["http_options"].httpx_async_client = httpx.AsyncClient(
                transport=httpx.MockTransport(handle), trust_env=False
            )
            return original_client(**kwargs)

        monkeypatch.setattr(chat.genai, "Client", client)
        return requests

    return install


async def ask(http):
    return await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Read today's orders"}]}
    )


async def test_provider_503_retries_once_then_returns_answer(environment, provider_wire):
    _, http = environment
    requests = provider_wire([
        provider_error(503), answer([{"text": "Please specify an order number."}])
    ])
    response = await ask(http)
    assert response.status_code == 200
    assert response.json()["message"] == "Please specify an order number."
    assert len(requests) == 2
    assert requests[0] == requests[1]


async def test_persistent_503_stops_after_two_attempts_and_is_sanitized(
    environment, provider_wire, caplog
):
    _, http = environment
    requests = provider_wire([provider_error(503), provider_error(503)])
    response = await ask(http)
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The chat provider is temporarily unavailable. Please try again shortly."
    )
    assert len(requests) == 2
    assert "ServerError status=503" in caplog.text
    assert "private-provider-key" not in response.text + caplog.text
    assert "confidential" not in response.text + caplog.text


@pytest.mark.parametrize("status, expected", [(429, 429), (500, 502), (504, 504)])
async def test_other_provider_errors_are_not_retried(
    environment, provider_wire, status, expected
):
    _, http = environment
    requests = provider_wire([provider_error(status)])
    response = await ask(http)
    assert response.status_code == expected
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_errors_are_not_retried(environment, provider_wire, failure):
    _, http = environment
    requests = provider_wire([failure("Synthetic transport failure")])
    response = await ask(http)
    assert response.status_code == 502
    assert len(requests) == 1


async def test_provider_retry_does_not_replay_a_completed_tool_read(
    environment, provider_wire, monkeypatch
):
    app, http = environment
    seed_upstream(app)
    name = tool_alias("woo", "list_orders")
    requests = provider_wire([
        answer([{"functionCall": {"name": name, "args": {}}}]),
        provider_error(503),
        answer([{"text": "There are no orders."}]),
    ])
    read = AsyncMock(return_value={
        "name": name, "arguments": {}, "is_error": False,
        "result": {"structuredContent": {"orders": []}}, "duration_ms": 1,
    })
    monkeypatch.setattr(app.state.agent, "call_gateway", read)
    response = await ask(http)
    assert response.status_code == 200
    assert response.json()["message"] == "There are no orders."
    assert len(response.json()["tool_calls"]) == 1
    read.assert_awaited_once()
    assert len(requests) == 3
    assert requests[1] == requests[2]
    assert requests[1]["contents"][-1]["parts"][0]["functionResponse"]["name"] == name


async def test_gateway_failure_is_not_retried(environment, provider_wire, monkeypatch):
    app, http = environment
    seed_upstream(app)
    name = tool_alias("woo", "list_orders")
    requests = provider_wire([answer([{"functionCall": {"name": name, "args": {}}}])])
    read = AsyncMock(side_effect=HTTPException(503, "Synthetic gateway unavailable"))
    monkeypatch.setattr(app.state.agent, "call_gateway", read)
    response = await ask(http)
    assert response.status_code == 503
    assert response.json()["detail"] == "Synthetic gateway unavailable"
    read.assert_awaited_once()
    assert len(requests) == 1


async def test_overall_chat_deadline_cancels_retry_delay(environment, provider_wire):
    app, http = environment
    app.state.settings.chat_timeout_seconds = 0.1
    requests = provider_wire([provider_error(503)])
    response = await ask(http)
    assert response.status_code == 504
    assert response.json()["detail"].startswith("Chat timed out.")
    assert len(requests) == 1

import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend import chat
from backend.gateway import tool_alias
from backend.models import Confirmation
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


@pytest.mark.parametrize("failure, status", [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)])
async def test_transport_errors_are_not_retried(environment, provider_wire, failure, status):
    _, http = environment
    requests = provider_wire([failure("Synthetic transport failure")])
    response = await ask(http)
    assert response.status_code == status
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


async def test_chat_mcp_transport_does_not_run_sdk_schema_validation(
    environment, provider_wire, monkeypatch
):
    from mcp.client.session import ClientSession

    from tests.backend.test_catalog import connect_fake_upstream

    app, http = environment
    seed_upstream(app)
    name = tool_alias("woo", "list_orders")
    dispatched = connect_fake_upstream(app, monkeypatch)
    validate = AsyncMock(side_effect=AssertionError("Untrusted schema on API event loop"))
    monkeypatch.setattr(ClientSession, "validate_tool_result", validate)
    requests = provider_wire([
        answer([{"functionCall": {"name": name, "args": {}}}]),
        answer([{"text": "The read completed."}]),
    ])
    response = await ask(http)
    assert response.status_code == 200, response.text
    assert dispatched == [("list_orders", {})]
    assert len(response.json()["tool_calls"]) == 1
    assert len(requests) == 2
    validate.assert_not_awaited()


async def test_overall_chat_deadline_cancels_retry_delay(environment, provider_wire):
    app, http = environment
    app.state.settings.chat_timeout_seconds = 0.1
    requests = provider_wire([provider_error(503)])
    response = await ask(http)
    assert response.status_code == 504
    assert response.json()["detail"].startswith("Chat timed out.")
    assert len(requests) == 1


def quota_error(*, retry_delay=None, retry_after=None):
    return httpx.Response(
        429,
        headers={"Retry-After": retry_after} if retry_after is not None else {},
        json={"error": {
            "code": 429,
            "message": "private-provider-key and confidential quota data",
            "status": "RESOURCE_EXHAUSTED",
            "details": [{
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": retry_delay,
            }] if retry_delay is not None else [],
        }},
    )


@pytest.mark.parametrize("retry_delay, retry_after, expected", [
    ("3.2s", None, 4),
    (None, "7", 7),
    ("3s", "8", 8),
    (None, None, 60),
    ("invalid", "invalid", 60),
    ("NaNs", "inf", 60),
    ("0s", None, 1),
])
async def test_quota_cooldown_blocks_followups_then_recovers(
    environment, provider_wire, monkeypatch, caplog, retry_delay, retry_after, expected
):
    app, http = environment
    clock = [1000.0]
    # Replace the module binding instead of patching asyncio's process-wide clock.
    monkeypatch.setattr(chat, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    requests = provider_wire([
        quota_error(retry_delay=retry_delay, retry_after=retry_after),
        answer([{"text": "Ready again."}]),
    ])
    first = await ask(http)
    second = await ask(http)
    assert first.status_code == second.status_code == 429
    assert first.headers["Retry-After"] == second.headers["Retry-After"] == str(expected)
    assert len(requests) == 1
    assert "private-provider-key" not in first.text + second.text + caplog.text
    assert "confidential" not in first.text + second.text + caplog.text
    clock[0] += expected
    recovered = await ask(http)
    assert recovered.status_code == 200
    assert recovered.json()["message"] == "Ready again."
    assert len(requests) == 2
    assert not app.state.agent.slots.locked()


def test_retry_after_http_date_and_bounded_delay():
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=90))
    exc = SimpleNamespace(response=SimpleNamespace(headers={"Retry-After": future}))
    assert 89 <= chat.provider_retry_delay(exc) <= 90
    exc.response.headers["Retry-After"] = "999999999"
    assert chat.provider_retry_delay(exc) == 86400


async def test_cooldown_is_shared_across_members(environment, provider_wire):
    from tests.backend.test_backend import member_client, use_client

    app, admin = environment
    member, _ = await member_client(app, admin)
    requests = provider_wire([quota_error(retry_delay="12s")])
    assert (await ask(admin)).status_code == 429
    async with use_client(member):
        response = await ask(member)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "12"
    assert len(requests) == 1


@pytest.mark.parametrize("parts", [[], [{"text": "  \n  "}], [{"text": "Private thought", "thought": True}]])
async def test_empty_or_thought_only_answers_are_rejected(environment, provider_wire, parts):
    _, http = environment
    provider_wire([answer(parts)])
    response = await ask(http)
    assert response.status_code == 502
    assert "Private thought" not in response.text


async def test_thought_text_is_not_returned_as_the_answer(environment, provider_wire):
    _, http = environment
    provider_wire([answer([
        {"text": "Private thought", "thought": True},
        {"text": "The visible answer."},
    ])])
    response = await ask(http)
    assert response.status_code == 200
    assert response.json()["message"] == "The visible answer."


@pytest.mark.parametrize("finish_reason", ["MAX_TOKENS", "SAFETY", "MALFORMED_FUNCTION_CALL"])
async def test_incomplete_model_response_never_prepares_or_runs_tools(
    environment, provider_wire, monkeypatch, finish_reason
):
    app, http = environment
    seed_upstream(app)
    name = tool_alias("woo", "create_product")
    response_body = answer([{"functionCall": {"name": name, "args": {"name": "Partial"}}}])
    response_body["candidates"][0]["finishReason"] = finish_reason
    provider_wire([response_body])
    dispatch = AsyncMock()
    monkeypatch.setattr(app.state.agent, "call_gateway", dispatch)
    response = await ask(http)
    assert response.status_code == 502
    assert "confirmation" not in response.json()
    dispatch.assert_not_awaited()


async def test_invalid_write_arguments_are_corrected_before_confirmation(
    environment, provider_wire, monkeypatch
):
    app, http = environment
    seed_upstream(app)
    name = tool_alias("woo", "create_product")
    requests = provider_wire([
        answer([{"functionCall": {"name": name, "args": {"name": 42}}}]),
        answer([{"functionCall": {"name": name, "args": {"name": "Valid product"}}}]),
    ])
    dispatch = AsyncMock()
    monkeypatch.setattr(app.state.agent, "call_gateway", dispatch)
    response = await ask(http)
    assert response.status_code == 200
    assert response.json()["confirmation"]["arguments"] == {"name": "Valid product"}
    dispatch.assert_not_awaited()
    assert len(requests) == 2
    feedback = requests[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert feedback["error"].startswith("Tool arguments did not match its schema.")
    with app.state.db.session() as session:
        pending = list(session.scalars(select(Confirmation)))
        assert len(pending) == 1
        assert pending[0].arguments == {"name": "Valid product"}


async def test_model_thought_function_is_never_executed(
    environment, provider_wire, monkeypatch
):
    app, http = environment
    seed_upstream(app)
    provider_wire([answer([{
        "functionCall": {"name": tool_alias("woo", "list_orders"), "args": {}},
        "thought": True,
    }])])
    dispatch = AsyncMock()
    monkeypatch.setattr(app.state.agent, "call_gateway", dispatch)
    response = await ask(http)
    assert response.status_code == 502
    dispatch.assert_not_awaited()


async def test_chat_overload_fails_fast_and_cancel_releases_slots(environment, monkeypatch):
    from contextlib import asynccontextmanager

    from backend.schemas import ChatMessage
    from backend.security import authenticate

    app, http = environment
    app.state.settings.gemini_api_key = "synthetic-provider-key"
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    messages = [ChatMessage(role="user", content="Read today's orders")]
    started = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def generate(**kwargs):
        nonlocal count
        count += 1
        if count == 3:
            started.set()
        await release.wait()
        return chat.genai_types.GenerateContentResponse.model_validate(answer([{"text": "Done."}]))

    @asynccontextmanager
    async def provider():
        yield SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    monkeypatch.setattr(chat.genai, "Client", lambda **kwargs: SimpleNamespace(aio=provider()))
    tasks = [asyncio.create_task(app.state.agent.run(principal, messages)) for _ in range(3)]
    try:
        async with asyncio.timeout(2):
            await started.wait()
            with pytest.raises(HTTPException) as overloaded:
                await app.state.agent.run(principal, messages)
        assert overloaded.value.status_code == 503
        assert overloaded.value.headers["Retry-After"] == "1"
        assert count == 3
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    release.set()
    recovered = await app.state.agent.run(principal, messages)
    assert recovered["message"] == "Done."
    assert count == 4

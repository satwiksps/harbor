import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
from fastapi import HTTPException
from google.genai import types as genai_types
from mcp import types
from sqlalchemy import select

from backend import security
from backend.gateway import tool_alias
from backend.models import Audit, Upstream
from backend.security import authenticate
from examples import ask_gateway
from tests.backend.test_backend import environment as environment
from tests.backend.test_backend import seed_upstream


@pytest.mark.parametrize("environment", ["file"], indirect=True)
async def test_parallel_login_rejects_overload_and_keeps_health_responsive(
    environment, monkeypatch
):
    app, _ = environment
    release, occupied = threading.Event(), threading.Event()
    lock = threading.Lock()
    entered = 0

    def verify(encoded, password):
        nonlocal entered
        with lock:
            entered += 1
            if entered == 2:
                occupied.set()
        assert release.wait(5), "Test did not release password workers"
        return False

    monkeypatch.setattr(security, "password_hasher", SimpleNamespace(verify=verify))

    async def attempt(index):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, client=(f"198.51.100.{index}", 1234)),
            base_url="http://testserver",
        ) as http:
            return await http.post(
                "/api/auth/login",
                json={"email": "admin@example.test", "password": "wrong-password"},
            )

    tasks = [asyncio.create_task(attempt(index)) for index in range(1, 11)]
    try:
        assert await asyncio.to_thread(occupied.wait, 3)
        async with asyncio.timeout(3):
            while sum(task.done() for task in tasks) < 8:
                await asyncio.sleep(0.01)
        assert entered == 2
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            async with asyncio.timeout(1):
                assert (await http.get("/health/live")).status_code == 200
    finally:
        release.set()
        responses = await asyncio.gather(*tasks)
    assert sorted(response.status_code for response in responses) == [401] * 2 + [503] * 8
    assert all(
        response.headers["Retry-After"] == "1"
        for response in responses
        if response.status_code == 503
    )


def test_hash_and_verification_share_slots_and_release_on_failure(monkeypatch):
    def broken(*args):
        raise RuntimeError("hash operation failed")

    monkeypatch.setattr(security, "password_hasher", SimpleNamespace(hash=broken, verify=broken))
    with security.password_slot(), security.password_slot():
        for operation in (
            lambda: security.hash_password("password"),
            lambda: security.check_password("hash", "password"),
        ):
            with pytest.raises(HTTPException) as error:
                operation()
            assert error.value.status_code == 503
    for _ in range(3):
        with pytest.raises(RuntimeError):
            security.hash_password("password")
        with pytest.raises(RuntimeError):
            security.check_password("hash", "password")


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize(
    "failure", ["oversized", "protocol", "serialization", "tool_error", "timeout", "cancelled"]
)
async def test_post_dispatch_failures_preserve_write_uncertainty(
    environment, monkeypatch, write, failure
):
    app, http = environment
    seed_upstream(app)
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("commerce_session")
    )
    name = tool_alias("woo", "create_product" if write else "list_orders")
    dispatched = []

    class UpstreamClient:
        async def call_tool(self, name, arguments):
            dispatched.append(name)
            if failure == "protocol":
                raise ConnectionError("private credential must not be exposed")
            if failure == "timeout":
                raise TimeoutError()
            if failure == "cancelled":
                raise asyncio.CancelledError()
            if failure == "serialization":
                return SimpleNamespace(model_dump_json=lambda **kwargs: "not JSON")
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text="x" * (256 * 1024) if failure == "oversized" else "failed"
                    )
                ],
                is_error=failure == "tool_error",
            )

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await app.state.gateway.execute(principal, name, {"name": "Test"} if write else {})
    else:
        result = await app.state.gateway.execute(principal, name, {"name": "Test"} if write else {})
        assert result.is_error
        message = " ".join(item.text for item in result.content)
        assert ("A write may have completed" in message) is write
        assert "private credential" not in message
    assert len(dispatched) == 1
    with app.state.db.session() as session:
        audit = session.scalar(select(Audit))
        assert audit.outcome != "success"
        assert ("A write may have completed" in audit.error) is write
        assert "private credential" not in audit.error


async def test_failed_handshake_does_not_claim_write_was_dispatched(environment, monkeypatch):
    app, http = environment
    seed_upstream(app)
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("commerce_session")
    )

    @asynccontextmanager
    async def connect(upstream):
        raise ConnectionError("handshake failed")
        yield  # pragma: no cover

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    result = await app.state.gateway.execute(
        principal, tool_alias("woo", "create_product"), {"name": "Test"}
    )
    assert result.is_error
    assert "may have completed" not in result.content[0].text


async def test_external_agent_refuses_tool_reclassified_while_model_thinks(
    environment, monkeypatch
):
    app, http = environment
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        session.get(Upstream, upstream_id).permissions = {"create_product": "read"}
    name = tool_alias("woo", "create_product")
    key = (await http.post("/api/keys", json={"name": "read-only agent test"})).json()["key"]
    dispatched = []

    class UpstreamClient:
        async def call_tool(self, name, arguments):
            dispatched.append(name)
            return types.CallToolResult(content=[])

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    original_client = httpx2.AsyncClient

    def gateway_http(**kwargs):
        return original_client(**kwargs, transport=httpx2.ASGITransport(app=app))

    monkeypatch.setattr(ask_gateway.httpx2, "AsyncClient", gateway_http)

    class Provider:
        def __init__(self):
            self.models = self
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def generate_content(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                with app.state.db.session() as session:
                    session.get(Upstream, upstream_id).permissions = {"create_product": "write"}
                parts = [
                    genai_types.Part.from_function_call(name=name, args={"name": "Unexpected"})
                ]
            else:
                parts = [genai_types.Part(text="The gateway rejected the changed tool.")]
            return genai_types.GenerateContentResponse(
                candidates=[
                    genai_types.Candidate(content=genai_types.Content(role="model", parts=parts))
                ]
            )

    monkeypatch.setattr(
        ask_gateway.genai, "Client", lambda **kwargs: SimpleNamespace(aio=Provider())
    )
    result = await ask_gateway.ask(
        "Read only", "http://testserver/mcp", key, "synthetic-provider-key"
    )
    assert result["passed"]
    assert result["tool_calls"][0]["is_error"]
    assert dispatched == []
    with app.state.db.session() as session:
        assert session.scalar(select(Audit)).outcome == "denied"

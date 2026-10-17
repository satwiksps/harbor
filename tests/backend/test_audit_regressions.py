import asyncio
import socket
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpcore2
import httpx2
import pytest
from fastapi import HTTPException
from google.genai import types as genai_types
from mcp import types
from sqlalchemy import select

from backend import security
from backend.gateway import tool_alias
from backend.models import Audit, Invite, Upstream
from backend.network import SafeNetworkBackend
from backend.security import authenticate
from examples import ask_gateway
from tests.backend.test_backend import environment as environment
from tests.backend.test_backend import seed_upstream


async def test_dns_falls_back_to_another_validated_address(monkeypatch):
    network = SafeNetworkBackend(set())
    records = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
    ]
    monkeypatch.setattr(
        asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=records)
    )
    stream = object()
    connect = AsyncMock(side_effect=[httpcore2.ConnectError("IPv6 has no route"), stream])
    monkeypatch.setattr(network.backend, "connect_tcp", connect)
    assert await network.connect_tcp("example.test", 443, timeout=1) is stream
    assert [call.args[0] for call in connect.await_args_list] == [
        "2606:4700:4700::1111", "1.1.1.1"
    ]

    # Fallback must never partially accept a DNS response containing a private target.
    records.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)))
    connect.reset_mock()
    with pytest.raises(httpcore2.ConnectError, match="forbidden"):
        await network.connect_tcp("example.test", 443, timeout=1)
    connect.assert_not_awaited()


async def test_dns_fallback_keeps_one_connection_deadline(monkeypatch):
    network = SafeNetworkBackend(set())
    records = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.0.0.1", 443)),
    ]
    monkeypatch.setattr(
        asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=records)
    )
    attempts = []

    async def connect(host, *args):
        attempts.append(host)
        if len(attempts) == 1:
            raise httpcore2.ConnectError("No route")
        await asyncio.Event().wait()

    monkeypatch.setattr(network.backend, "connect_tcp", connect)
    with pytest.raises(httpcore2.ConnectTimeout, match="deadline"):
        await network.connect_tcp("example.test", 443, timeout=0.05)
    assert attempts == ["1.1.1.1", "1.0.0.1"]


async def test_invalid_email_cannot_create_unusable_invitation(environment):
    app, http = environment
    invalid = (
        "@example.test",
        "person @example.test",
        "person@example..test",
        "person@.test",
        "person@example.test\nother",
        "person@example.test@other.test",
        "person@-example.test",
        "person@example-.test",
        "person@exam_ple.test",
        ".person@example.test",
        "person..name@example.test",
        "person.@example.test",
        "p" * 65 + "@example.test",
    )
    for email in invalid:
        invitation = await http.post("/api/invites", json={"email": email})
        assert invitation.status_code == 422, email
        assert invitation.json()["detail"] == "email: Value error, Enter a valid email address"
        login = await http.post(
            "/api/auth/login", json={"email": email, "password": "not-a-password"}
        )
        assert login.status_code == 422, email
    with app.state.db.session() as session:
        assert list(session.scalars(select(Invite))) == []

    valid = await http.post(
        "/api/invites", json={"email": "  First.Last+Tag@Sub.Example.Test  "}
    )
    assert valid.status_code == 201
    assert valid.json()["email"] == "first.last+tag@sub.example.test"
    token = valid.json()["invite_url"].rsplit("/", 1)[1]
    assert (await http.get(f"/api/invites/{token}")).json()["email"] == (
        "first.last+tag@sub.example.test"
    )


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
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
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
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
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

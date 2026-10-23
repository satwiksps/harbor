"""Gateway boundaries that must remain effective around the MCP SDK."""

import asyncio
import socket
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpcore2
import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.server import Server
from sqlalchemy import select

from backend.gateway import UpstreamClient, tool_alias
from backend.models import Audit, Upstream
from backend.network import SafeNetworkBackend
from backend.security import authenticate
from tests.backend.test_backend import environment as environment
from tests.backend.test_backend import seed_upstream


async def test_dns_and_socket_connection_share_one_deadline(monkeypatch):
    network = SafeNetworkBackend(set())
    connected = []

    async def resolve(*args, **kwargs):
        await asyncio.sleep(0.12)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]

    async def connect(*args):
        connected.append(args[0])
        await asyncio.sleep(0.12)
        return object()

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(network.backend, "connect_tcp", connect)
    with pytest.raises(httpcore2.ConnectTimeout, match="deadline"):
        await network.connect_tcp("example.test", 443, timeout=0.2)
    assert connected == ["1.1.1.1"]


async def test_dns_failure_is_safe_and_never_attempts_a_socket(monkeypatch):
    network = SafeNetworkBackend(set())
    monkeypatch.setattr(
        asyncio.get_running_loop(), "getaddrinfo",
        AsyncMock(side_effect=socket.gaierror("sensitive lookup detail")),
    )
    connect = AsyncMock()
    monkeypatch.setattr(network.backend, "connect_tcp", connect)
    with pytest.raises(httpcore2.ConnectError, match="^MCP hostname did not resolve$"):
        await network.connect_tcp("example.test", 443)
    connect.assert_not_awaited()


async def test_upstream_dispatch_does_not_run_sdk_output_validation_or_fetch_new_catalog(monkeypatch):
    calls = []

    async def listing(ctx, params):
        calls.append("list")
        return types.ListToolsResult(tools=[])

    async def execute(ctx, params):
        calls.append(params.name)
        return types.CallToolResult(content=[], structured_content={"value": "result"})

    async def unsafe_validation(*args):
        pytest.fail("Untrusted schema validation reached the API event loop")

    monkeypatch.setattr(ClientSession, "validate_tool_result", unsafe_validation)
    server = Server("fixture", on_list_tools=listing, on_call_tool=execute)
    async with UpstreamClient(server, cache=None) as client:
        result = await client.call_tool("operation", {})
    assert result.structured_content == {"value": "result"}
    assert calls == ["operation"]


async def test_upstream_continuation_cannot_be_reported_as_completed_or_retried():
    calls = []

    async def execute(ctx, params):
        calls.append(params.name)
        return types.CallToolResult(content=[], result_type="input_required")

    async with UpstreamClient(Server("fixture", on_call_tool=execute), cache=None) as client:
        with pytest.raises(ValueError, match="unsupported tool continuation"):
            await client.call_tool("operation", {})
    assert calls == ["operation"]


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("case", ["valid", "invalid", "missing", "tool_error"])
async def test_gateway_validates_pinned_output_schema(environment, monkeypatch, write, case):
    app, http = environment
    upstream_id = seed_upstream(app)
    name = "create_product" if write else "list_orders"
    with app.state.db.session() as session:
        upstream = session.get(Upstream, upstream_id)
        upstream.tools = [{
            "name": name,
            "inputSchema": {"type": "object"},
            "outputSchema": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
        }]
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    calls = []

    class Client:
        async def call_tool(self, tool, arguments):
            calls.append(tool)
            data = {"id": 1 if case == "valid" else "incorrect"}
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="fixture")],
                is_error=case == "tool_error",
                **({} if case == "missing" else {"structured_content": data}),
            )

    @asynccontextmanager
    async def connect(upstream):
        yield Client(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    result = await app.state.gateway.execute(principal, tool_alias("woo", name), {})
    assert calls == [name]
    assert result.is_error == (case != "valid")
    if case in {"invalid", "missing"}:
        assert "declared schema" in result.content[0].text
        assert ("verify the store state" in result.content[0].text) == write
        assert "incorrect" not in result.model_dump_json()
    with app.state.db.session() as session:
        audit = session.scalar(select(Audit))
        assert audit.outcome == (
            "success" if case == "valid" else "tool_error" if case == "tool_error" else "error"
        )


async def test_hostile_output_schema_is_bounded_and_does_not_block_api(environment, monkeypatch):
    app, http = environment
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        upstream = session.get(Upstream, upstream_id)
        upstream.tools = [{
            "name": "list_orders",
            "inputSchema": {"type": "object"},
            "outputSchema": {
                "type": "object",
                "properties": {"value": {"type": "string", "pattern": "^(a+)+$"}},
            },
        }]
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )

    class Client:
        async def call_tool(self, name, arguments):
            return types.CallToolResult(content=[], structured_content={"value": "a" * 64 + "!"})

    @asynccontextmanager
    async def connect(upstream):
        yield Client(), None

    gateway = app.state.gateway
    monkeypatch.setattr(gateway, "connect", connect)
    await gateway.schema_checks.check(True)
    gateway.schema_checks.timeout_seconds = 0.3
    worker = gateway.schema_checks._process
    execution = asyncio.create_task(
        gateway.execute(principal, tool_alias("woo", "list_orders"), {})
    )
    try:
        await asyncio.sleep(0.05)
        async with asyncio.timeout(0.5):
            assert (await http.get("/health/live")).status_code == 200
            assert (await http.get("/api/auth/me")).status_code == 200
        async with asyncio.timeout(2):
            result = await execution
    finally:
        if not execution.done():
            execution.cancel()
            with pytest.raises(asyncio.CancelledError):
                await execution
    assert result.is_error
    assert "deadline" in result.content[0].text
    assert worker.returncode is not None
    await gateway.schema_checks.validate({"type": "integer"}, 1)
    with app.state.db.session() as session:
        assert session.scalar(select(Audit)).outcome == "timeout"

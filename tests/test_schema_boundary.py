"""Exercise the real process boundary with adversarial JSON schemas."""

import asyncio
import sys
import time
from contextlib import asynccontextmanager

import pytest
from mcp import types
from sqlalchemy import select

from backend.gateway import tool_alias
from backend.models import Audit, Upstream
from backend.security import authenticate
from backend.tool_safety import SchemaBoundary
from tests.backend import test_backend as backend_tests

environment = backend_tests.environment


@pytest.fixture
async def boundary():
    checker = SchemaBoundary(timeout_seconds=10)
    try:
        yield checker
    finally:
        await checker.close()


async def test_worker_preserves_validation_and_is_reused(boundary):
    schema = {
        "type": "object",
        "$defs": {"count": {"type": "integer", "minimum": 1}},
        "properties": {
            "count": {"$ref": "#/$defs/count"},
            "$ref": {"type": "string"},
        },
        "required": ["count"],
        "additionalProperties": False,
        "default": {"$ref": "https://literal.example/data"},
    }
    await boundary.check(schema)
    worker = boundary._process
    await boundary.validate(schema, {"count": 2, "$ref": "https://literal.example/data"})
    for invalid in ({"count": 0}, {"count": "2"}, {}, {"count": 1, "extra": 3}):
        with pytest.raises(ValueError, match="invalid"):
            await boundary.validate(schema, invalid)
    assert boundary._process is worker
    assert worker.returncode is None


@pytest.mark.parametrize("reference", ["https://example.test/schema", "file:///etc/passwd"])
async def test_worker_rejects_external_references(boundary, reference):
    with pytest.raises(ValueError, match="invalid"):
        await boundary.check({"properties": {"value": {"$ref": reference}}})


def exponential_schema():
    definitions = {"level0": True}
    for depth in range(1, 40):
        previous = {"$ref": f"#/$defs/level{depth - 1}"}
        definitions[f"level{depth}"] = {"allOf": [previous, previous]}
    return {"$defs": definitions, "$ref": "#/$defs/level39"}


@pytest.mark.parametrize(
    ("schema", "arguments"),
    [
        ({"type": "string", "pattern": "^(a+)+$"}, "a" * 64 + "!"),
        ({"patternProperties": {"^(a+)+$": True}}, {"a" * 64 + "!": "value"}),
        (exponential_schema(), {}),
    ],
    ids=["pattern-backtracking", "property-name-backtracking", "reference-expansion"],
)
async def test_hostile_schema_is_killed_without_blocking_loop(boundary, schema, arguments):
    # Warm the reusable worker so the deadline tests hostile computation, not startup.
    await boundary.check({"type": "object"})
    worker = boundary._process
    boundary.timeout_seconds = 0.25
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    monitor = asyncio.create_task(heartbeat())
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            await boundary.validate(schema, arguments)
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor
    assert time.monotonic() - started < 2
    assert ticks >= 3
    assert worker.returncode is not None
    assert boundary._process is None
    # Killing a hostile job must not break later ordinary calls.
    boundary.timeout_seconds = 10
    await boundary.validate({"type": "integer"}, 2)
    assert boundary._process.pid != worker.pid


async def test_cancellation_reaps_worker_and_does_not_leak_results(boundary):
    await boundary.check(True)
    worker = boundary._process
    task = asyncio.create_task(
        boundary.validate({"type": "string", "pattern": "^(a+)+$"}, "a" * 64 + "!")
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.returncode is not None
    assert boundary._process is None
    with pytest.raises(ValueError, match="invalid"):
        await boundary.validate({"type": "integer"}, "not an integer")


@pytest.mark.skipif(sys.platform != "linux", reason="Production worker memory limit is Linux-only")
async def test_regex_memory_exhaustion_is_rejected_without_killing_worker(boundary):
    await boundary.check(True)
    worker = boundary._process
    with pytest.raises(ValueError, match="invalid"):
        await boundary.validate({"type": "string", "pattern": "(?:a?){100000000}"}, "")
    assert boundary._process is worker
    assert worker.returncode is None
    await boundary.validate({"type": "integer"}, 2)


async def test_validation_payload_is_bounded_before_starting_worker(boundary):
    with pytest.raises(ValueError, match="limit"):
        await boundary.check({"description": "x" * (512 * 1024)})
    assert boundary._process is None


async def test_validation_structure_is_bounded_before_serialization(boundary):
    nested = {}
    for _ in range(70):
        nested = {"value": nested}
    for value in (nested, [0] * 20_001):
        with pytest.raises(ValueError, match="structural limit"):
            await boundary.validate(True, value)
    assert boundary._process is None


async def test_close_reaps_worker_and_prevents_restart(boundary):
    await boundary.check(True)
    worker = boundary._process
    await boundary.close()
    assert worker.returncode is not None
    with pytest.raises(ValueError, match="closed"):
        await boundary.check(True)
    assert boundary._process is None


async def test_gateway_stays_responsive_and_isolates_hostile_schema(environment, monkeypatch):
    app, http = environment
    hostile_id = backend_tests.seed_upstream(app, "woo")
    backend_tests.seed_upstream(app, "policies")
    with app.state.db.session() as session:
        session.get(Upstream, hostile_id).tools = [
            {
                "name": "list_orders",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "pattern": "^(a+)+$"}},
                },
            }
        ]
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    dispatched = []

    class UpstreamClient:
        def __init__(self, slug):
            self.slug = slug

        async def call_tool(self, name, arguments):
            dispatched.append(self.slug)
            return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(upstream.slug), None

    gateway = app.state.gateway
    monkeypatch.setattr(gateway, "connect", connect)
    await gateway.schema_checks.check(True)
    gateway.schema_checks.timeout_seconds = 1
    hostile = asyncio.create_task(
        gateway.execute(principal, tool_alias("woo", "list_orders"), {"query": "a" * 64 + "!"})
    )
    try:
        await asyncio.sleep(0.05)
        async with asyncio.timeout(0.5):
            assert (await http.get("/health/live")).status_code == 200
            assert (await http.get("/api/auth/me")).status_code == 200
        assert not hostile.done()
        # This call waits for the worker budget, then succeeds using a replacement.
        healthy = await gateway.execute(principal, tool_alias("policies", "list_orders"), {})
        failure = await hostile
    finally:
        if not hostile.done():
            hostile.cancel()
            with pytest.raises(asyncio.CancelledError):
                await hostile
    assert failure.is_error
    assert not healthy.is_error
    assert dispatched == ["policies"]
    with app.state.db.session() as session:
        outcomes = {row.server_name: row.outcome for row in session.scalars(select(Audit))}
        assert outcomes == {"Woo": "timeout", "Policies": "success"}

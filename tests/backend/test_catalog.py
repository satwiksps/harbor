import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
from google.genai import types as genai_types
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from backend import chat
from backend.catalog import (
    MAX_DECLARATION_BYTES,
    SEARCH_DECLARATION,
    SEARCH_NAME,
    CatalogEntry,
    ChatCatalog,
    declaration,
    declaration_bytes,
)
from backend.gateway import tool_alias
from backend.models import Audit, Confirmation, LoginSession, Upstream, User
from backend.security import authenticate
from examples import ask_gateway
from tests.backend.test_backend import environment as environment
from tests.backend.test_backend import member_client, use_client


def entry(name="lookup", description="Find orders", schema=None):
    return CatalogEntry(
        types.Tool(
            name=name,
            description=description,
            input_schema=schema or {"type": "object", "additionalProperties": False},
            annotations=types.ToolAnnotations(read_only_hint=True),
        ),
        "binding-" + name,
    )


def test_direct_catalog_threshold_counts_and_exact_serialized_utf8_budget():
    assert not ChatCatalog([entry(str(index)) for index in range(40)]).progressive
    assert ChatCatalog([entry(str(index)) for index in range(41)]).progressive
    baseline = entry(schema={"type": "object", "description": ""})
    remaining = MAX_DECLARATION_BYTES - declaration_bytes([declaration(baseline)])
    exact = entry(schema={"type": "object", "description": "x" * remaining})
    assert declaration_bytes([declaration(exact)]) == MAX_DECLARATION_BYTES
    assert not ChatCatalog([exact]).progressive
    oversized = entry(schema={"type": "object", "description": "x" * (remaining + 1)})
    assert ChatCatalog([oversized]).progressive
    unicode_over = entry(schema={"type": "object", "description": "x" * (remaining - 1) + "₹"})
    assert ChatCatalog([unicode_over]).progressive


def test_search_budget_preserves_complete_schemas_and_reports_oversized_tools():
    padding = entry("large", schema={"type": "object", "description": ""})
    remaining = MAX_DECLARATION_BYTES - declaration_bytes(
        [SEARCH_DECLARATION, declaration(padding)]
    )
    exact = entry("large", schema={"type": "object", "description": "x" * remaining})
    too_large = entry("giant", schema={"type": "object", "description": "x" * (remaining + 1)})
    entries = [exact, too_large, entry("small")] + [entry(f"other_{i}") for i in range(40)]
    catalog = ChatCatalog(entries)
    found = catalog.search(entries, {"query": "large", "limit": 1})
    assert found["results"][0]["enabled"]
    assert declaration_bytes(catalog.declarations()) == MAX_DECLARATION_BYTES
    assert catalog.declarations()[1].parameters_json_schema == exact.tool.input_schema
    giant = catalog.search(entries, {"query": "giant"})
    assert giant["results"][0]["enabled"] is False
    assert "external MCP client" in giant["results"][0]["reason"]
    assert list(catalog.active) == []
    catalog.search(entries, {"query": "small"})
    assert list(catalog.active) == ["small"]
    page = catalog.search(entries, {"query": ""})
    assert declaration_bytes(catalog.declarations()) == MAX_DECLARATION_BYTES
    assert any(
        item.get("reason") == "Search for this exact tool name to load its schema."
        for item in page["results"]
    )


def test_search_pages_ranking_empty_results_and_replaces_selection():
    entries = [entry(f"operation_{i:03}", f"[Store {i % 10}] Read inventory") for i in range(500)]
    entries[-1] = entry("store9__list_orders", "[Store 9] Northstar customer orders")
    catalog = ChatCatalog(entries)
    found = catalog.search(entries, {"query": "Northstar", "limit": 5})
    assert found["matches"] == 1
    assert found["results"][0]["name"] == "store9__list_orders"
    first = catalog.search(entries, {"query": "", "limit": 5})
    second = catalog.search(entries, {"query": "", "limit": 5, "offset": first["next_offset"]})
    assert first["matches"] == 500
    assert not {item["name"] for item in first["results"]} & {
        item["name"] for item in second["results"]
    }
    assert len(catalog.active) == 5
    empty = catalog.search(entries, {"query": "xyznonexistent"})
    assert empty == {"matches": 0, "results": [], "next_offset": None}
    assert catalog.active == {}
    numeric = catalog.search(entries, {"query": "", "limit": 5.0, "offset": 5.0})
    assert numeric["results"] == second["results"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": "x" * 201},
        {"query": 5},
        {"query": "", "offset": -1},
        {"query": "", "limit": 6},
        {"query": "", "limit": True},
        {"query": "", "extra": "untrusted"},
    ],
)
def test_search_validates_model_arguments(arguments):
    catalog = ChatCatalog([entry(str(i)) for i in range(41)])
    assert "error" in catalog.search([], arguments)


def seed_many(app, write=False):
    ids = []
    with app.state.db.session() as session:
        for server in range(10):
            tools, permissions = [], {}
            for number in range(50):
                target = server == 9 and number == 49
                name = (
                    ("create_product" if write else "list_orders")
                    if target
                    else f"task_{number:02}"
                )
                tools.append(
                    {
                        "name": name,
                        "description": "Northstar commerce operation"
                        if target
                        else f"Read store {server} data",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}} if target and write else {},
                            "required": ["name"] if target and write else [],
                            "additionalProperties": False,
                        },
                    }
                )
                permissions[name] = "write" if target and write else "read"
            upstream = Upstream(
                name=f"Store {server}",
                slug=f"store{server:02}",
                url="http://woo:8001/mcp",
                status="Connected",
                tools=tools,
                permissions=permissions,
            )
            session.add(upstream)
            session.flush()
            ids.append(upstream.id)
    return ids, tool_alias("store09", "create_product" if write else "list_orders")


def fake_provider(app, monkeypatch, step):
    app.state.settings.gemini_api_key = "synthetic-provider-key"
    observations = []

    async def generate(**kwargs):
        declarations = (
            kwargs["config"].tools[0].function_declarations if kwargs["config"].tools else []
        )
        observations.append(
            {
                "names": [item.name for item in declarations],
                "bytes": declaration_bytes(declarations),
                "contents": [item.model_dump(by_alias=True) for item in kwargs["contents"]],
            }
        )
        parts = step(len(observations), kwargs)
        return genai_types.GenerateContentResponse(
            candidates=[
                genai_types.Candidate(content=genai_types.Content(role="model", parts=parts))
            ]
        )

    @asynccontextmanager
    async def provider():
        yield SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    monkeypatch.setattr(chat.genai, "Client", lambda **kwargs: SimpleNamespace(aio=provider()))
    return observations


def connect_fake_upstream(app, monkeypatch):
    dispatched = []

    class UpstreamClient:
        async def call_tool(self, name, arguments):
            dispatched.append((name, arguments))
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Fixture result")]
            )

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    original_client = httpx2.AsyncClient

    def local_http(**kwargs):
        return original_client(**kwargs, transport=httpx2.ASGITransport(app=app))

    monkeypatch.setattr(chat.httpx2, "AsyncClient", local_http)
    return dispatched


async def test_large_catalog_discovers_and_executes_read_through_mcp(environment, monkeypatch):
    app, http = environment
    _, name = seed_many(app)
    dispatched = connect_fake_upstream(app, monkeypatch)

    def step(number, kwargs):
        if number == 1:
            return [
                genai_types.Part.from_function_call(name=SEARCH_NAME, args={"query": "Northstar"})
            ]
        if number == 2:
            return [genai_types.Part.from_function_call(name=name, args={})]
        return [genai_types.Part(text="Read the Northstar result.")]

    observations = fake_provider(app, monkeypatch, step)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Read Northstar"}]}
    )
    assert response.status_code == 200, response.text
    assert observations[0]["names"] == [SEARCH_NAME]
    assert observations[1]["names"] == [SEARCH_NAME, name]
    assert all(item["bytes"] <= MAX_DECLARATION_BYTES for item in observations)
    assert dispatched == [("list_orders", {})]
    assert len(response.json()["tool_calls"]) == 1
    with app.state.db.session() as session:
        audit = session.scalar(select(Audit))
        assert audit.tool_name == name
        assert audit.outcome == "success"


async def test_large_catalog_write_still_requires_single_use_confirmation(environment, monkeypatch):
    app, http = environment
    _, name = seed_many(app, write=True)
    dispatched = connect_fake_upstream(app, monkeypatch)

    def step(number, kwargs):
        return [
            genai_types.Part.from_function_call(
                name=SEARCH_NAME if number == 1 else name,
                args={"query": "Northstar"} if number == 1 else {"name": "Reviewed"},
            )
        ]

    fake_provider(app, monkeypatch, step)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Create Northstar product"}]}
    )
    assert response.status_code == 200, response.text
    confirmation_id = response.json()["confirmation"]["id"]
    assert dispatched == []
    with app.state.db.session() as session:
        assert list(session.scalars(select(Audit))) == []
        pending = session.get(Confirmation, confirmation_id)
        assert pending.target_hash
    confirmed = await http.post("/api/chat/confirm", json={"id": confirmation_id, "approve": True})
    assert confirmed.status_code == 200, confirmed.text
    assert dispatched == [("create_product", {"name": "Reviewed"})]
    assert (
        await http.post("/api/chat/confirm", json={"id": confirmation_id, "approve": True})
    ).status_code == 404


@pytest.mark.parametrize("change", ["schema", "endpoint", "permission", "disable"])
async def test_discovered_target_change_before_model_call_never_dispatches(
    environment, monkeypatch, change
):
    app, http = environment
    ids, name = seed_many(app)
    dispatched = connect_fake_upstream(app, monkeypatch)

    def step(number, kwargs):
        if number == 1:
            return [
                genai_types.Part.from_function_call(name=SEARCH_NAME, args={"query": "Northstar"})
            ]
        if number == 2:
            with app.state.db.session() as session:
                upstream = session.get(Upstream, ids[-1])
                if change == "schema":
                    upstream.tools = [
                        {**tool, "inputSchema": {"type": "object", "required": ["new"]}}
                        if tool["name"] == "list_orders"
                        else tool
                        for tool in upstream.tools
                    ]
                elif change == "endpoint":
                    upstream.url = "http://policies:8001/mcp"
                elif change == "permission":
                    upstream.permissions = {**upstream.permissions, "list_orders": "write"}
                else:
                    upstream.enabled = False
            return [genai_types.Part.from_function_call(name=name, args={})]
        assert "Tool was not selected, changed" in json.dumps(kwargs["contents"][-1].model_dump())
        return [genai_types.Part(text="The selected tool changed.")]

    fake_provider(app, monkeypatch, step)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Read Northstar"}]}
    )
    assert response.status_code == 200, response.text
    assert dispatched == []
    assert "confirmation" not in response.json()


async def test_member_discovery_hides_write_and_rejects_guessed_tool(environment, monkeypatch):
    app, http = environment
    _, name = seed_many(app, write=True)
    member, _ = await member_client(app, http)
    dispatched = connect_fake_upstream(app, monkeypatch)

    def step(number, kwargs):
        if number == 1:
            return [
                genai_types.Part.from_function_call(name=SEARCH_NAME, args={"query": "Northstar"})
            ]
        if number == 2:
            assert kwargs["contents"][-1].parts[0].function_response.response["matches"] == 0
            return [genai_types.Part.from_function_call(name=name, args={"name": "Unauthorized"})]
        return [genai_types.Part(text="The tool is unavailable.")]

    observations = fake_provider(app, monkeypatch, step)
    async with use_client(member):
        response = await member.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "Create Northstar product"}]},
        )
    assert response.status_code == 200
    assert all(item["names"] == [SEARCH_NAME] for item in observations)
    assert dispatched == []
    assert "confirmation" not in response.json()


@pytest.mark.parametrize("change", ["session", "inactive"])
async def test_discovery_rechecks_identity_after_model_wait(environment, monkeypatch, change):
    app, http = environment
    seed_many(app)
    identity = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )

    def step(number, kwargs):
        with app.state.db.session() as session:
            if change == "session":
                session.delete(session.get(LoginSession, identity.session_id))
            else:
                session.get(User, identity.id).active = False
        return [genai_types.Part.from_function_call(name=SEARCH_NAME, args={"query": ""})]

    fake_provider(app, monkeypatch, step)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "List tools"}]}
    )
    assert response.status_code == 401


async def test_discovery_calls_share_function_budget(environment, monkeypatch):
    app, http = environment
    seed_many(app)

    def step(number, kwargs):
        return [
            genai_types.Part.from_function_call(name=SEARCH_NAME, args={"query": ""})
            for _ in range(13)
        ]

    observations = fake_provider(app, monkeypatch, step)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "List tools"}]}
    )
    assert response.status_code == 422
    assert "Tool call limit reached" in response.json()["detail"]
    assert len(observations) == 1


async def test_chat_never_dispatches_oversized_unselected_schema(environment, monkeypatch):
    app, http = environment
    with app.state.db.session() as session:
        session.add(
            Upstream(
                name="Oversized",
                slug="oversized",
                url="http://woo:8001/mcp",
                tools=[
                    {
                        "name": "giant",
                        "description": "Oversized operation",
                        "inputSchema": {"type": "object", "description": "x" * 50000},
                    }
                ],
                permissions={"giant": "read"},
            )
        )
    name = tool_alias("oversized", "giant")
    dispatched = connect_fake_upstream(app, monkeypatch)

    def step(number, kwargs):
        if number == 1:
            return [genai_types.Part.from_function_call(name=SEARCH_NAME, args={"query": "giant"})]
        if number == 2:
            result = kwargs["contents"][-1].parts[0].function_response.response["results"][0]
            assert not result["enabled"]
            assert "external MCP client" in result["reason"]
            return [genai_types.Part.from_function_call(name=name, args={})]
        return [genai_types.Part(text="This tool's schema is too large for chat.")]

    observations = fake_provider(app, monkeypatch, step)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Call the giant operation"}]}
    )
    assert response.status_code == 200, response.text
    assert all(item["names"] == [SEARCH_NAME] for item in observations)
    assert all(item["bytes"] <= MAX_DECLARATION_BYTES for item in observations)
    assert dispatched == []


async def test_standard_mcp_keeps_paginated_500_tool_catalog(environment):
    app, http = environment
    _, name = seed_many(app)
    key = (await http.post("/api/keys", json={"name": "catalog fixture"})).json()["key"]
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {key}"},
    ) as transport:
        async with Client(
            streamable_http_client("http://testserver/mcp", http_client=transport), cache=None
        ) as client:
            names, cursor = [], None
            for page_index in range(5):
                page = await client.list_tools(cursor=cursor)
                assert len(page.tools) == 100
                names.extend(tool.name for tool in page.tools)
                cursor = page.next_cursor
                assert bool(cursor) is (page_index < 4)
    assert len(set(names)) == 500
    assert name in names[400:]
    assert SEARCH_NAME not in names


async def test_external_example_reads_beyond_first_page_and_reports_model_budget():
    readonly = entry("last_page_read").tool
    writes = [
        types.Tool(
            name=f"write_{i}",
            input_schema={"type": "object"},
            annotations=types.ToolAnnotations(read_only_hint=False),
        )
        for i in range(100)
    ]
    requested = []

    async def listing(cursor=None):
        requested.append(cursor)
        return types.ListToolsResult(
            tools=writes if cursor is None else [readonly],
            next_cursor="100" if cursor is None else None,
        )

    catalog = await ask_gateway.read_only_catalog(SimpleNamespace(list_tools=listing))
    assert requested == [None, "100"]
    assert list(catalog) == ["last_page_read"]
    assert len(ask_gateway.model_declarations(catalog)) == 1
    with pytest.raises(ask_gateway.CatalogError, match="Harbor chat"):
        ask_gateway.model_declarations({str(i): entry(str(i)).tool for i in range(41)})
    with pytest.raises(ask_gateway.CatalogError, match="48 KiB"):
        ask_gateway.model_declarations(
            {"giant": entry(schema={"type": "object", "description": "x" * 50000}).tool}
        )


async def test_external_example_rejects_repeated_catalog_cursor():
    async def listing(cursor=None):
        return types.ListToolsResult(tools=[], next_cursor="same")

    with pytest.raises(ask_gateway.CatalogError, match="repeated a catalog cursor"):
        await ask_gateway.read_only_catalog(SimpleNamespace(list_tools=listing))

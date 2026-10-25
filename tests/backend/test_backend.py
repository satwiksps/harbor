import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx2
import pytest
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from backend.app import create_app
from backend.config import Settings
from backend.gateway import tool_alias
from backend.models import (
    ApiKey,
    Audit,
    BootstrapState,
    Confirmation,
    Invite,
    LoginSession,
    Upstream,
    User,
    now,
)
from backend.network import SafeNetworkBackend, validate_header, validate_url
from backend.security import authenticate, delegate, digest
from backend.tool_safety import schema_validator, target_hash


@pytest.fixture
async def environment(tmp_path, request):
    password = secrets.token_urlsafe(24)
    settings = Settings(
        database_url=(
            f"sqlite:///{tmp_path / 'concurrent.db'}"
            if getattr(request, "param", None) == "file"
            else "sqlite:///:memory:"
        ),
        testing=True,
        session_secret=secrets.token_urlsafe(48),
        secret_backend="file",
        secret_file=str(tmp_path / "credentials.json"),
        bootstrap_admin_email="admin@example.test",
        bootstrap_admin_password=password,
        gateway_url="http://testserver/mcp",
        allowed_origins="http://testserver",
        internal_gateway_url="http://testserver/mcp",
        allowed_internal_hosts="woo,policies,shipping",
    )
    app = create_app(settings)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def lifespan_task():
        async with app.router.lifespan_context(app):
            started.set()
            await stopped.wait()

    task = asyncio.create_task(lifespan_task())
    await started.wait()
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            response = await http.post(
                "/api/auth/login", json={"email": "admin@example.test", "password": password}
            )
            assert response.status_code == 200, response.text
            http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            yield app, http
    finally:
        stopped.set()
        await task


@asynccontextmanager
async def use_client(client):
    try:
        yield client
    finally:
        await client.aclose()


def seed_upstream(app, slug="woo"):
    with app.state.db.session() as session:
        row = Upstream(
            name=slug.title(),
            slug=slug,
            url=f"http://{slug}:8001/mcp",
            status="Connected",
            permissions={"list_orders": "read"},
            tools=[
                {
                    "name": "list_orders",
                    "description": "List orders",
                    "inputSchema": {"type": "object", "additionalProperties": False},
                },
                {
                    "name": "create_product",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
            ],
        )
        session.add(row)
        session.flush()
        return row.id


async def member_client(app, admin_http):
    response = await admin_http.post(
        "/api/invites", json={"email": "member@example.test", "role": "member"}
    )
    assert response.status_code == 201
    token = response.json()["invite_url"].rsplit("/", 1)[1]
    password = secrets.token_urlsafe(24)
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver")
    response = await http.post(
        "/api/auth/accept-invite", json={"token": token, "name": "Member", "password": password}
    )
    assert response.status_code == 200
    response = await http.post(
        "/api/auth/login", json={"email": "member@example.test", "password": password}
    )
    assert response.status_code == 200
    http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return http, response.json()["user"]


async def test_csrf_invites_and_team_isolation(environment):
    app, admin_http = environment
    response = await admin_http.post(
        "/api/keys", json={"name": "test"}, headers={"X-CSRF-Token": "wrong"}
    )
    assert response.status_code == 403
    member, user = await member_client(app, admin_http)
    async with use_client(member):
        assert (await member.get("/api/members")).status_code == 403
        assert (
            await member.post(
                "/api/servers", json={"name": "x", "slug": "x", "url": "https://example.test/mcp"}
            )
        ).status_code == 403
        admin_id = (await admin_http.get("/api/auth/me")).json()["user"]["id"]
        assert (
            await member.post("/api/keys", json={"name": "impersonation", "user_id": admin_id})
        ).status_code == 403
        own = await member.post("/api/keys", json={"name": "own"})
        assert own.status_code == 201
        assert "key" not in (await member.get("/api/keys")).json()[0]
        assert (
            await admin_http.patch(f"/api/members/{admin_id}", json={"active": False})
        ).status_code == 409
        assert (
            await admin_http.patch(f"/api/members/{user['id']}", json={"active": False})
        ).status_code == 200
        assert (await member.get("/api/auth/me")).status_code == 401


async def test_accept_invite_replaces_only_presented_admin_session(environment):
    app, http = environment
    old_cookie = http.cookies.get("harbor_session")
    old_csrf = http.headers["X-CSRF-Token"]
    old_principal = authenticate(app.state.db, app.state.settings, cookie=old_cookie)
    old_delegation = delegate(app.state.settings.session_secret, old_principal.session_id)
    invitation = await http.post(
        "/api/invites", json={"email": "invited@example.test", "role": "member"}
    )
    token = invitation.json()["invite_url"].rsplit("/", 1)[1]
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as other_browser:
        other_login = await other_browser.post(
            "/api/auth/login",
            json={
                "email": app.state.settings.bootstrap_admin_email,
                "password": app.state.settings.bootstrap_admin_password,
            },
        )
        assert other_login.status_code == 200
        accepted = await http.post(
            "/api/auth/accept-invite",
            json={"token": token, "name": "Invited member", "password": secrets.token_urlsafe(24)},
        )
        assert accepted.status_code == 200
        auth = accepted.json()
        assert auth["user"]["email"] == "invited@example.test"
        assert auth["user"]["role"] == "member"
        assert auth["user"]["id"] != old_principal.id
        assert http.cookies.get("harbor_session") != old_cookie
        assert auth["csrf_token"] != old_csrf
        assert "HttpOnly" in accepted.headers["set-cookie"]
        assert "SameSite=lax" in accepted.headers["set-cookie"]
        assert "Path=/" in accepted.headers["set-cookie"]
        assert (await http.get("/api/auth/me")).json() == auth

        # The old browser cookie and its delegated chat credential lose authority together.
        assert (
            await http.get("/api/auth/me", headers={"Cookie": f"harbor_session={old_cookie}"})
        ).status_code == 401
        assert (
            await http.post(
                "/mcp",
                headers={"Authorization": f"Bearer {old_delegation}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        ).status_code == 401
        with app.state.db.session() as session:
            assert session.get(LoginSession, old_principal.session_id).revoked_at is not None
        assert (await other_browser.get("/api/auth/me")).json()["user"]["role"] == "admin"
        assert (await other_browser.get("/api/members")).status_code == 200

        assert (await http.post("/api/keys", json={"name": "old csrf"})).status_code == 403
        http.headers["X-CSRF-Token"] = auth["csrf_token"]
        assert (await http.get("/api/members")).status_code == 403
        assert (await http.get("/api/invites")).status_code == 403
        assert (
            await http.post("/api/invites", json={"email": "next@example.test", "role": "admin"})
        ).status_code == 403
        assert (
            await http.post(
                "/api/servers", json={"name": "x", "slug": "x", "url": "https://example.test/mcp"}
            )
        ).status_code == 403
        assert (
            await http.patch(f"/api/members/{auth['user']['id']}", json={"role": "admin"})
        ).status_code == 403
        key = await http.post("/api/keys", json={"name": "member key"})
        assert key.status_code == 201
        assert key.json()["record"]["user_id"] == auth["user"]["id"]


async def test_accept_invite_without_session_signs_in_invited_user(environment):
    app, admin_http = environment
    invitation = await admin_http.post(
        "/api/invites", json={"email": "new@example.test", "role": "member"}
    )
    token = invitation.json()["invite_url"].rsplit("/", 1)[1]
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as anonymous:
        accepted = await anonymous.post(
            "/api/auth/accept-invite",
            json={"token": token, "name": "New member", "password": secrets.token_urlsafe(24)},
        )
        assert accepted.status_code == 200
        assert (await anonymous.get("/api/auth/me")).json() == accepted.json()
        assert accepted.json()["user"]["role"] == "member"
        assert "csrf_token" in accepted.json()
        assert (await admin_http.get("/api/members")).status_code == 200


@pytest.mark.parametrize("kind", ["invalid", "expired", "replayed"])
async def test_rejected_invite_preserves_current_session(environment, kind):
    app, http = environment
    original_auth = (await http.get("/api/auth/me")).json()
    original_cookie = http.cookies.get("harbor_session")
    token = "not-an-invitation"
    if kind != "invalid":
        invitation = await http.post(
            "/api/invites", json={"email": "rejected@example.test", "role": "member"}
        )
        token = invitation.json()["invite_url"].rsplit("/", 1)[1]
        if kind == "expired":
            with app.state.db.session() as session:
                row = session.scalar(select(Invite).where(Invite.token_hash == digest(token)))
                row.expires_at = now() - timedelta(seconds=1)
        else:
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
            ) as other_browser:
                accepted = await other_browser.post(
                    "/api/auth/accept-invite",
                    json={"token": token, "name": "Member", "password": secrets.token_urlsafe(24)},
                )
                assert accepted.status_code == 200
    rejected = await http.post(
        "/api/auth/accept-invite",
        json={"token": token, "name": "Rejected", "password": secrets.token_urlsafe(24)},
    )
    assert rejected.status_code == 404
    assert "set-cookie" not in rejected.headers
    assert http.cookies.get("harbor_session") == original_cookie
    assert (await http.get("/api/auth/me")).json() == original_auth
    assert (await http.get("/api/members")).status_code == 200


async def test_successful_login_rotates_presented_session(environment):
    app, http = environment
    old_cookie = http.cookies.get("harbor_session")
    old_csrf = http.headers["X-CSRF-Token"]
    login = await http.post(
        "/api/auth/login",
        json={
            "email": app.state.settings.bootstrap_admin_email,
            "password": app.state.settings.bootstrap_admin_password,
        },
    )
    assert login.status_code == 200
    assert http.cookies.get("harbor_session") != old_cookie
    assert login.json()["csrf_token"] != old_csrf
    assert (
        await http.get("/api/auth/me", headers={"Cookie": f"harbor_session={old_cookie}"})
    ).status_code == 401
    assert (await http.get("/api/auth/me")).json() == login.json()


async def test_conflicting_invite_acceptance_rolls_back_without_session_change(environment):
    app, http = environment
    original_auth = (await http.get("/api/auth/me")).json()
    original_cookie = http.cookies.get("harbor_session")
    invitation = await http.post(
        "/api/invites", json={"email": "conflict@example.test", "role": "member"}
    )
    token = invitation.json()["invite_url"].rsplit("/", 1)[1]
    with app.state.db.session() as session:
        admin = session.get(User, original_auth["user"]["id"])
        session.add(
            User(
                email="conflict@example.test",
                name="Existing member",
                role="member",
                password_hash=admin.password_hash,
            )
        )
    rejected = await http.post(
        "/api/auth/accept-invite",
        json={"token": token, "name": "Conflict", "password": secrets.token_urlsafe(24)},
    )
    assert rejected.status_code == 409
    assert "set-cookie" not in rejected.headers
    assert http.cookies.get("harbor_session") == original_cookie
    assert (await http.get("/api/auth/me")).json() == original_auth
    with app.state.db.session() as session:
        row = session.scalar(select(Invite).where(Invite.token_hash == digest(token)))
        assert row.accepted_at is None


@pytest.mark.parametrize("legacy_key", [False, True])
async def test_real_mcp_sdk_permissions_revocation_and_audit(environment, monkeypatch, legacy_key):
    app, admin_http = environment
    upstream_id = seed_upstream(app)
    member, user = await member_client(app, admin_http)
    called = []

    class UpstreamClient:
        async def call_tool(self, name, arguments):
            called.append(name)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text='{"orders":[]}')]
            )

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    async with use_client(member):
        key_response = (await member.post("/api/keys", json={"name": "external"})).json()
        token = key_response["key"]
        assert token.startswith("harbor_")
        with app.state.db.session() as session:
            key = session.get(ApiKey, key_response["record"]["id"])
            assert key.token_hash == digest(token)
            assert token not in str(key.__dict__)
            if legacy_key:
                # Previously issued keys retain their stored hash through the rename.
                token = "cc_" + token.removeprefix("harbor_")
                key.token_hash = digest(token)
                key.prefix = token[:12]
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers={"Authorization": f"Bearer {token}"}
        ) as mcp_http:
            async with Client(
                streamable_http_client("http://testserver/mcp", http_client=mcp_http), cache=None
            ) as client:
                tools = await client.list_tools()
                assert [t.name for t in tools.tools] == [tool_alias("woo", "list_orders")]
                result = await client.call_tool(tool_alias("woo", "list_orders"), {})
                assert not result.is_error
                denied = await client.call_tool(tool_alias("woo", "create_product"), {"name": "No"})
                assert denied.is_error
                assert called == ["list_orders"]
                assert (
                    await admin_http.patch(f"/api/servers/{upstream_id}", json={"enabled": False})
                ).status_code == 200
                assert not (await client.list_tools()).tools
                disabled = await client.call_tool(tool_alias("woo", "list_orders"), {})
                assert disabled.is_error
                await member.delete(f"/api/keys/{key_response['record']['id']}")
                with pytest.raises(Exception):
                    await client.call_tool(tool_alias("woo", "list_orders"), {})
        audit = (await member.get("/api/audit")).json()
        assert audit["total"] == 4
        assert all(row["user_id"] == user["id"] for row in audit["items"])
        assert sum(row["outcome"] == "denied" for row in audit["items"]) == 3


async def test_gateway_schema_timeout_and_failure_isolation(environment, monkeypatch):
    app, admin_http = environment
    dead_id = seed_upstream(app, "woo")
    healthy_id = seed_upstream(app, "policies")
    principal = authenticate(
        app.state.db, app.state.settings, cookie=admin_http.cookies.get("harbor_session")
    )
    # Include local IPC scheduling while remaining below the simulated slow call.
    app.state.settings.tool_timeout_seconds = 0.25

    class UpstreamClient:
        def __init__(self, slug):
            self.slug = slug

        async def call_tool(self, name, arguments):
            if self.slug == "woo":
                await asyncio.sleep(1)
            return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

        async def list_tools(self, cursor=None):
            if self.slug == "woo":
                raise ConnectionError("sensitive credential must never be persisted")
            return types.ListToolsResult(
                tools=[types.Tool(name="list_orders", input_schema={"type": "object"})]
            )

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(upstream.slug), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    await asyncio.gather(app.state.gateway.refresh(dead_id), app.state.gateway.refresh(healthy_id))
    with app.state.db.session() as session:
        assert session.get(Upstream, dead_id).status == "Unreachable"
        assert "sensitive" not in session.get(Upstream, dead_id).last_error
        assert session.get(Upstream, healthy_id).status == "Connected"
    invalid = await app.state.gateway.execute(principal, tool_alias("woo", "create_product"), {})
    assert invalid.is_error
    timed_out, healthy = await asyncio.gather(
        app.state.gateway.execute(principal, tool_alias("woo", "list_orders"), {}),
        app.state.gateway.execute(principal, tool_alias("policies", "list_orders"), {}),
    )
    assert timed_out.is_error and not healthy.is_error
    with app.state.db.session() as session:
        assert {row.outcome for row in session.scalars(select(Audit))} == {
            "invalid_arguments",
            "timeout",
            "success",
        }


async def test_delegation_dies_with_login_session(environment):
    app, http = environment
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    token = delegate(app.state.settings.session_secret, principal.session_id)
    assert authenticate(app.state.db, app.state.settings, bearer=token).id == principal.id
    await http.post("/api/auth/logout")
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        authenticate(app.state.db, app.state.settings, bearer=token)


async def test_confirmation_is_session_bound_and_single_use(environment, monkeypatch):
    app, http = environment
    seed_upstream(app)
    tool_name = tool_alias("woo", "create_product")
    upstream, tool = app.state.gateway.lookup(tool_name)
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    with app.state.db.session() as session:
        confirmation = Confirmation(
            user_id=principal.id,
            session_id=principal.session_id,
            tool_name=tool_name,
            target_hash=target_hash(upstream, tool),
            arguments={"name": "approved"},
            expires_at=now() + timedelta(minutes=5),
        )
        session.add(confirmation)
        session.flush()
        confirmation_id = confirmation.id
    member, _ = await member_client(app, http)
    async with use_client(member):
        assert (
            await member.post("/api/chat/confirm", json={"id": confirmation_id, "approve": True})
        ).status_code == 404
    called = []

    async def gateway_call(p, name, arguments, expected_target=None, require_read=True):
        assert expected_target == target_hash(upstream, tool)
        assert require_read is False
        called.append((p.id, name, arguments))
        return {
            "name": name,
            "arguments": arguments,
            "is_error": False,
            "duration_ms": 1,
            "result": {"content": [{"type": "text", "text": "created"}]},
        }

    monkeypatch.setattr(app.state.agent, "call_gateway", gateway_call)
    assert (
        await http.post("/api/chat/confirm", json={"id": confirmation_id, "approve": True})
    ).status_code == 200
    assert (
        await http.post("/api/chat/confirm", json={"id": confirmation_id, "approve": True})
    ).status_code == 404
    assert len(called) == 1 and called[0][0] == principal.id


async def test_schema_references_never_fetch_network_or_files(environment, monkeypatch):
    import urllib.request

    app, http = environment
    upstream_id = seed_upstream(app)
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    retrievals, called = [], []

    def fetch(request, *args, **kwargs):
        retrievals.append(request)
        raise AssertionError("Schema retrieval must never run")

    monkeypatch.setattr(urllib.request, "urlopen", fetch)

    class UpstreamClient:
        async def call_tool(self, name, arguments):
            called.append(name)
            return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    for reference in ("http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"):
        with app.state.db.session() as session:
            row = session.get(Upstream, upstream_id)
            row.tools = [{"name": "list_orders", "inputSchema": {"$ref": reference}}]
        result = await app.state.gateway.execute(principal, tool_alias("woo", "list_orders"), {})
        assert result.is_error
    assert retrievals == [] and called == []
    with app.state.db.session() as session:
        row = session.get(Upstream, upstream_id)
        row.tools = [
            {
                "name": "list_orders",
                "inputSchema": {
                    "type": "object",
                    "$defs": {"count": {"type": "integer", "minimum": 1}},
                    "properties": {"count": {"$ref": "#/$defs/count"}},
                },
            }
        ]
    valid = await app.state.gateway.execute(
        principal, tool_alias("woo", "list_orders"), {"count": 2}
    )
    invalid = await app.state.gateway.execute(
        principal, tool_alias("woo", "list_orders"), {"count": 0}
    )
    assert not valid.is_error and invalid.is_error
    assert called == ["list_orders"] and retrievals == []


def test_schema_reference_keys_inside_instance_data_are_not_schema_references():
    schema = {
        "type": "object",
        "properties": {"$ref": {"type": "string"}},
        "default": {"$ref": "https://literal.example/data"},
        "examples": [{"$dynamicRef": "file:///literal-data"}],
        "const": {"$ref": "https://literal.example/data"},
    }
    schema_validator(schema).validate({"$ref": "https://literal.example/data"})
    for keyword, nested in (
        ("properties", {"value": {"$ref": "https://blocked.example/schema"}}),
        ("allOf", [{"$ref": "https://blocked.example/schema"}]),
        ("items", {"$ref": "file:///blocked"}),
    ):
        with pytest.raises(ValueError, match="own document"):
            schema_validator({keyword: nested})


@pytest.mark.parametrize("kind", ["input", "output", "alias_collision"])
async def test_unsafe_tool_catalog_is_rejected(environment, monkeypatch, kind):
    app, _ = environment
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        session.get(Upstream, upstream_id).tools = []
    tools = [types.Tool(name="list_orders", input_schema={"type": "object"})]
    if kind == "input":
        tools[0].input_schema = {"$ref": "http://169.254.169.254/schema"}
    elif kind == "output":
        tools[0].output_schema = {"$ref": "file:///etc/passwd"}
    else:
        # A real truncated SHA-256 collision for names with the same readable prefix.
        names = ["shared_tool_name_prefix__40618", "shared_tool_name_prefix__43287"]
        assert tool_alias("woo", names[0]) == tool_alias("woo", names[1])
        tools = [types.Tool(name=name, input_schema={"type": "object"}) for name in names]

    class UpstreamClient:
        async def list_tools(self, cursor=None):
            return types.ListToolsResult(tools=tools)

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    await app.state.gateway.refresh(upstream_id)
    with app.state.db.session() as session:
        upstream = session.get(Upstream, upstream_id)
        assert upstream.status == "Unreachable"
        assert upstream.tools == []


@pytest.mark.parametrize(
    "credential", ['synthetic"credential\\with-escape', "synthetic-trailing\\"]
)
async def test_credentials_redacted_from_catalog_and_escaped_results(
    environment, monkeypatch, credential
):
    app, http = environment
    upstream_id = seed_upstream(app)

    class UpstreamClient:
        async def list_tools(self, cursor=None):
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="list_orders",
                        description=f"An upstream accidentally echoed {credential}",
                        input_schema={"type": "object", "description": credential},
                    )
                ]
            )

        async def call_tool(self, name, arguments):
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps({"token": credential}))],
                structured_content={"token": credential, credential: "value"},
            )

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), credential

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    await app.state.gateway.refresh(upstream_id)
    server = (await http.get("/api/servers")).json()[0]
    assert server["tools"][0]["description"].endswith("[REDACTED]")
    assert server["tools"][0]["inputSchema"]["description"] == "[REDACTED]"
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    result = await app.state.gateway.execute(principal, tool_alias("woo", "list_orders"), {})
    assert not result.is_error
    assert result.structured_content == {"token": "[REDACTED]", "[REDACTED]": "value"}
    assert json.loads(result.content[0].text)["token"] == "[REDACTED]"


async def test_chat_prepares_reviewed_write_without_executing_it(environment, monkeypatch):
    from backend import chat

    app, http = environment
    seed_upstream(app)
    app.state.settings.gemini_api_key = "synthetic-provider-key"
    name = tool_alias("woo", "create_product")

    async def generate(**kwargs):
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=chat.genai_types.Content(
                        role="model",
                        parts=[
                            chat.genai_types.Part.from_function_call(
                                name=name, args={"name": "Reviewed product"}
                            )
                        ],
                    )
                )
            ]
        )

    @asynccontextmanager
    async def provider():
        yield SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    monkeypatch.setattr(chat.genai, "Client", lambda **kwargs: SimpleNamespace(aio=provider()))

    async def gateway_call(*args, **kwargs):
        raise AssertionError("Preparing a write must not execute it")

    monkeypatch.setattr(app.state.agent, "call_gateway", gateway_call)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Create a product"}]}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["tool_calls"] == []
    upstream, tool = app.state.gateway.lookup(name)
    with app.state.db.session() as session:
        pending = session.get(Confirmation, result["confirmation"]["id"])
        assert pending.target_hash == target_hash(upstream, tool)
        assert pending.arguments == {"name": "Reviewed product"}
        assert pending.used_at is None
        assert not list(session.scalars(select(Audit)))


@pytest.mark.parametrize("change", ["write", "endpoint"])
async def test_automatic_chat_read_cannot_change_authority_in_transit(
    environment, monkeypatch, change
):
    from backend import chat

    app, http = environment
    upstream_id = seed_upstream(app)
    app.state.settings.gemini_api_key = "synthetic-provider-key"
    name = tool_alias("woo", "list_orders")
    generations, called = [], []

    async def generate(**kwargs):
        generations.append(True)
        part = (
            chat.genai_types.Part.from_function_call(name=name, args={})
            if len(generations) == 1
            else chat.genai_types.Part(text="The connection changed before the read could run.")
        )
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(content=chat.genai_types.Content(role="model", parts=[part]))
            ]
        )

    @asynccontextmanager
    async def provider():
        yield SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    monkeypatch.setattr(chat.genai, "Client", lambda **kwargs: SimpleNamespace(aio=provider()))

    class UpstreamClient:
        async def call_tool(self, original, arguments):
            called.append(original)
            return types.CallToolResult(content=[types.TextContent(type="text", text="ran")])

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)

    async def in_transit(principal, tool, arguments, expected_target=None, require_read=False):
        with app.state.db.session() as session:
            server = session.get(Upstream, upstream_id)
            if change == "write":
                server.permissions = {"list_orders": "write"}
            else:
                server.url = "http://policies:8001/mcp"
        result = await app.state.gateway.execute(
            principal, tool, arguments, expected_target=expected_target, require_read=require_read
        )
        return {
            "name": tool,
            "arguments": arguments,
            "is_error": result.is_error,
            "result": result.model_dump(by_alias=True),
            "duration_ms": 1,
        }

    monkeypatch.setattr(app.state.agent, "call_gateway", in_transit)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Read orders"}]}
    )
    assert response.status_code == 200
    assert response.json()["tool_calls"][0]["is_error"]
    assert called == []
    with app.state.db.session() as session:
        assert session.scalar(select(Audit)).outcome == "denied"


@pytest.mark.parametrize("change", ["disable", "revoke", "role", "target"])
async def test_queued_call_rechecks_access_and_approved_target(environment, monkeypatch, change):
    app, http = environment
    upstream_id = seed_upstream(app)
    key = (await http.post("/api/keys", json={"name": "queued call"})).json()
    principal = authenticate(app.state.db, app.state.settings, bearer=key["key"])
    name = tool_alias("woo", "create_product")
    upstream, tool = app.state.gateway.lookup(name)
    expected_target = target_hash(upstream, tool) if change == "target" else None
    slot = asyncio.Semaphore(0)
    app.state.gateway.call_slots[upstream_id] = slot
    calls = []

    @asynccontextmanager
    async def connect(upstream):
        calls.append(upstream.id)
        raise AssertionError("A queued denied call must not connect")
        yield

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    task = asyncio.create_task(
        app.state.gateway.execute(
            principal, name, {"name": "queued"}, expected_target=expected_target
        )
    )
    await asyncio.sleep(0)
    try:
        with app.state.db.session() as session:
            assert session.scalar(select(Audit)).outcome == "pending"
            if change == "disable":
                session.get(Upstream, upstream_id).enabled = False
            elif change == "revoke":
                session.get(ApiKey, key["record"]["id"]).revoked_at = now()
            elif change == "role":
                session.get(User, principal.id).role = "member"
            else:
                session.get(Upstream, upstream_id).url = "http://policies:8001/mcp"
        slot.release()
        result = await task
        assert result.is_error and calls == []
        with app.state.db.session() as session:
            assert session.scalar(select(Audit)).outcome == "denied"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("change", ["endpoint", "schema", "legacy"])
async def test_changed_confirmation_target_cannot_execute(environment, monkeypatch, change):
    app, http = environment
    upstream_id = seed_upstream(app)
    name = tool_alias("woo", "create_product")
    upstream, tool = app.state.gateway.lookup(name)
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    with app.state.db.session() as session:
        pending = Confirmation(
            user_id=principal.id,
            session_id=principal.session_id,
            tool_name=name,
            target_hash=None if change == "legacy" else target_hash(upstream, tool),
            arguments={"name": "Reviewed"},
            expires_at=now() + timedelta(minutes=5),
        )
        session.add(pending)
        session.flush()
        confirmation_id = pending.id
        server = session.get(Upstream, upstream_id)
        if change == "endpoint":
            server.url = "http://policies:8001/mcp"
        elif change == "schema":
            updated = json.loads(json.dumps(server.tools))
            updated[1]["inputSchema"]["properties"]["unreviewed"] = {"type": "string"}
            server.tools = updated

    async def call(*args, **kwargs):
        raise AssertionError("A changed target must require a new review")

    monkeypatch.setattr(app.state.agent, "call_gateway", call)
    response = await http.post("/api/chat/confirm", json={"id": confirmation_id, "approve": True})
    assert response.status_code == 409
    assert "prepare the change again" in response.json()["detail"]


async def test_gateway_audits_oversized_unknown_tool_identifiers(environment):
    app, http = environment
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    result = await app.state.gateway.execute(principal, "unknown" * 100, {}, "request" * 100)
    assert result.is_error
    with app.state.db.session() as session:
        row = session.scalar(select(Audit))
        assert row.outcome == "denied"
        assert len(row.tool_name) == 150 and len(row.request_id) == 100


@pytest.mark.parametrize("header_prefix", ["X-Harbor", "X-Commerce"])
async def test_mcp_enforces_approval_target_and_gateway_read_hints(
    environment, monkeypatch, header_prefix
):
    app, http = environment
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        row = session.get(Upstream, upstream_id)
        tools = json.loads(json.dumps(row.tools))
        tools[1]["annotations"] = {"readOnlyHint": True}
        row.tools = tools
    name = tool_alias("woo", "create_product")
    upstream, tool = app.state.gateway.lookup(name)
    reviewed_target = target_hash(upstream, tool)
    key = (await http.post("/api/keys", json={"name": "approval protocol"})).json()["key"]
    called = []

    class UpstreamClient:
        async def call_tool(self, original, arguments):
            called.append(original)
            return types.CallToolResult(content=[types.TextContent(type="text", text="created")])

    @asynccontextmanager
    async def connect(upstream):
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    other_prefix = "X-Commerce" if header_prefix == "X-Harbor" else "X-Harbor"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        headers={"Authorization": f"Bearer {key}", f"{header_prefix}-Target": "outdated"},
    ) as mcp_http:
        async with Client(
            streamable_http_client("http://testserver/mcp", http_client=mcp_http), cache=None
        ) as client:
            listing = (await client.list_tools()).tools
            hints = {tool.name: tool.annotations.read_only_hint for tool in listing}
            assert hints[tool_alias("woo", "list_orders")] is True
            assert hints[name] is False
            assert (await client.call_tool(name, {"name": "Reviewed"})).is_error
            assert called == []
            mcp_http.headers[f"{header_prefix}-Target"] = reviewed_target
            mcp_http.headers[f"{header_prefix}-Read-Only"] = "true"
            mcp_http.headers[f"{other_prefix}-Read-Only"] = "false"
            assert (await client.call_tool(name, {"name": "Reviewed"})).is_error
            assert called == []
            del mcp_http.headers[f"{header_prefix}-Read-Only"]
            del mcp_http.headers[f"{other_prefix}-Read-Only"]
            mcp_http.headers[f"{other_prefix}-Target"] = "conflicting"
            conflict = await client.call_tool(name, {"name": "Reviewed"})
            assert conflict.is_error
            assert "Conflicting approval target headers" in conflict.content[0].text
            assert called == []
            mcp_http.headers[f"{other_prefix}-Target"] = reviewed_target
            assert not (await client.call_tool(name, {"name": "Reviewed"})).is_error
    assert called == ["create_product"]
    outcomes = (await http.get("/api/audit")).json()["items"]
    assert {row["outcome"] for row in outcomes} == {"denied", "success"}
    assert any(row["error"] == "Conflicting approval target headers" for row in outcomes)


@pytest.mark.parametrize("change", ["revoke", "endpoint"])
async def test_tool_does_not_dispatch_after_access_changes_during_handshake(
    environment, monkeypatch, change
):
    app, http = environment
    upstream_id = seed_upstream(app)
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )

    class UpstreamClient:
        async def call_tool(self, original, arguments):
            raise AssertionError("Authority changed during the handshake")

    @asynccontextmanager
    async def connect(upstream):
        with app.state.db.session() as session:
            if change == "revoke":
                session.get(LoginSession, principal.session_id).revoked_at = now()
            else:
                session.get(Upstream, upstream_id).url = "http://policies:8001/mcp"
        yield UpstreamClient(), None

    monkeypatch.setattr(app.state.gateway, "connect", connect)
    result = await app.state.gateway.execute(principal, tool_alias("woo", "list_orders"), {})
    assert result.is_error
    with app.state.db.session() as session:
        assert session.scalar(select(Audit)).outcome == "denied"


@pytest.mark.parametrize("error, status", [(TimeoutError, 504), (ConnectionError, 502)])
async def test_chat_gateway_errors_are_safe_for_confirmation_paths(
    environment, monkeypatch, error, status
):
    from fastapi import HTTPException

    from backend import chat

    app, http = environment
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )

    def transport(*args, **kwargs):
        raise error("private upstream credential")

    monkeypatch.setattr(chat, "streamable_http_client", transport)
    with pytest.raises(HTTPException) as caught:
        await app.state.agent.call_gateway(principal, "write", {})
    assert caught.value.status_code == status
    assert "private" not in caught.value.detail
    assert "audit log" in caught.value.detail


@pytest.mark.parametrize("url", ["http://woo:8001/mcp", None, "http://policies:8001/mcp"])
async def test_edit_server_only_resets_permissions_when_endpoint_changes(
    environment, monkeypatch, url
):
    app, http = environment
    server_id = seed_upstream(app)
    member, _ = await member_client(app, http)
    async with use_client(member):
        principal = authenticate(
            app.state.db, app.state.settings, cookie=member.cookies.get("harbor_session")
        )
        with app.state.db.session() as session:
            original_tools = session.get(Upstream, server_id).tools

        refreshed = []

        async def refresh(changed_id):
            refreshed.append(changed_id)

        monkeypatch.setattr(app.state.gateway, "refresh", refresh)
        response = await http.patch(
            f"/api/servers/{server_id}", json={"name": "Renamed Harbor", "url": url}
        )
        assert response.status_code == 200
        assert refreshed == [server_id]
        endpoint_changed = url == "http://policies:8001/mcp"
        with app.state.db.session() as session:
            server = session.get(Upstream, server_id)
            assert server.name == "Renamed Harbor"
            assert server.url == (url or "http://woo:8001/mcp")
            assert server.tools == ([] if endpoint_changed else original_tools)
            assert server.permissions == ({} if endpoint_changed else {"list_orders": "read"})
        visible = [tool.name for tool in app.state.gateway.list_for(principal)]
        assert visible == ([] if endpoint_changed else [tool_alias("woo", "list_orders")])


async def test_unconfigured_chat_is_honest(environment):
    app, http = environment
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "orders today"}]}
    )
    assert response.status_code == 503


@pytest.mark.parametrize("provider_code, expected", [(429, 429), (504, 504), (500, 502)])
async def test_chat_provider_errors_are_bounded_and_redacted(
    environment, monkeypatch, caplog, provider_code, expected
):
    from backend import chat

    app, http = environment
    app.state.settings.gemini_api_key = "private-provider-key"
    captured = {}

    class ProviderError(Exception):
        code = provider_code

    async def generate(**kwargs):
        captured["config"] = kwargs["config"]
        raise ProviderError("private-provider-key and confidential tool data")

    @asynccontextmanager
    async def context():
        yield SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    def client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(aio=context())

    monkeypatch.setattr(chat.genai, "Client", client)
    response = await http.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "orders today"}]}
    )
    assert response.status_code == expected
    assert captured["http_options"].timeout == 45000
    assert captured["http_options"].retry_options.attempts == 1
    assert captured["config"].temperature == 1.0
    assert captured["config"].thinking_config.thinking_level.value == "LOW"
    assert "ProviderError" in caplog.text
    assert "private-provider-key" not in caplog.text + response.text
    assert "confidential" not in caplog.text + response.text


async def test_chunked_body_limit_applies_before_json_parsing(environment):
    _, http = environment

    async def oversized():
        for _ in range(5):
            yield b"x" * 65536

    response = await http.post("/api/auth/login", content=oversized())
    assert response.status_code == 413
    assert response.json()["detail"] == "Request is too large"
    small = await http.post("/api/auth/login", json={"email": "bad"})
    assert small.status_code == 422 and isinstance(small.json()["detail"], str)


async def test_compressed_upstream_is_rejected_before_decompression():
    from backend.network import SafeTransport

    transport = SafeTransport(set())
    await transport.pool.aclose()
    closed = []

    async def close():
        closed.append(True)

    async def request(request):
        assert (b"Accept-Encoding", b"identity") in request.headers
        return SimpleNamespace(status=200, headers=[(b"Content-Encoding", b"gzip")], aclose=close)

    transport.pool = SimpleNamespace(handle_async_request=request)
    with pytest.raises(ValueError, match="Compressed upstream"):
        await transport.handle_async_request(httpx2.Request("GET", "https://public.example/mcp"))
    assert closed == [True]


def test_write_confirmation_summarizes_actual_structured_result():
    from backend.chat import confirmed_message

    result = {
        "is_error": False,
        "result": {
            "structuredContent": {
                "id": 42,
                "name": "Demo mug",
                "price": "12.00",
                "permalink": "http://store.localhost:8080/product/demo",
            }
        },
    }
    message = confirmed_message(tool_alias("woo", "create_product"), result)
    assert "Demo mug" in message and "12.00" in message and "View product:" in message
    assert "structuredContent" not in message
    result["is_error"] = True
    assert "could not be completed" in confirmed_message("write", result)


@pytest.mark.parametrize("remove_all", [False, True])
async def test_deleted_bootstrap_servers_stay_deleted_after_fresh_start(tmp_path, remove_all):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'persistent.db'}",
        testing=True,
        session_secret=secrets.token_urlsafe(48),
        secret_backend="file",
        secret_file=str(tmp_path / "credentials.json"),
        bootstrap_admin_email="admin@example.test",
        bootstrap_admin_password=secrets.token_urlsafe(24),
        allowed_internal_hosts="woo,policies,shipping",
        bootstrap_servers_json=json.dumps(
            [
                {"name": slug, "slug": slug, "url": f"http://{slug}:8001/mcp"}
                for slug in ("woo", "policies", "shipping")
            ]
        ),
    )
    first = create_app(settings)
    async with first.router.lifespan_context(first):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=first), base_url="http://testserver"
        ) as http:
            login = await http.post(
                "/api/auth/login",
                json={
                    "email": settings.bootstrap_admin_email,
                    "password": settings.bootstrap_admin_password,
                },
            )
            assert login.status_code == 200
            http.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            servers = (await http.get("/api/servers")).json()
            assert len(servers) == 3
            for server in servers if remove_all else servers[:1]:
                assert (await http.delete(f"/api/servers/{server['id']}")).status_code == 200
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        with restarted.state.db.session() as session:
            assert len(list(session.scalars(select(Upstream)))) == (0 if remove_all else 2)
            assert session.get(BootstrapState, "defaults") is not None
            assert len(list(session.scalars(select(User)))) == 1


async def test_upgrade_marks_existing_installation_initialized_without_reseeding(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from backend.db import Database

    url = f"sqlite:///{tmp_path / 'existing.db'}"
    old = Database(url)
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "backend" / "migrations")
    )
    with old.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
    with old.session() as session:
        session.add(
            User(
                email="existing@example.test",
                name="Existing administrator",
                password_hash="unused-existing-hash",
                role="admin",
            )
        )
    old.engine.dispose()
    app = create_app(
        Settings(
            database_url=url,
            session_secret=secrets.token_urlsafe(48),
            secret_backend="file",
            secret_file=str(tmp_path / "credentials.json"),
            allowed_internal_hosts="woo",
            bootstrap_servers_json='[{"name":"Woo","slug":"woo","url":"http://woo:8001/mcp"}]',
        )
    )
    async with app.router.lifespan_context(app):
        with app.state.db.session() as session:
            assert session.get(BootstrapState, "defaults") is not None
            assert not list(session.scalars(select(Upstream)))
            assert session.scalar(select(User)).email == "existing@example.test"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "https://127.0.0.1/mcp",
        "https://[::1]/mcp",
        "https://169.254.169.254/",
        "https://u:p@example.com/mcp",
        "https://example.com/mcp?token=bad",
        "https://localhost/mcp",
    ],
)
def test_ssrf_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url, {"woo"})


def test_header_and_internal_allowlist():
    assert validate_url("http://woo:8001/mcp", {"woo"}).hostname == "woo"
    validate_header("X-API-Key")
    with pytest.raises(ValueError):
        validate_header("Host")
    with pytest.raises(ValueError):
        validate_header("Authorization")


async def test_dns_rebinding_is_blocked_at_connect(monkeypatch):
    backend = SafeNetworkBackend(set())
    addresses = ["8.8.8.8", "127.0.0.1"]
    connections = []

    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", (addresses.pop(0), 443))]

    async def connect(host, *args, **kwargs):
        connections.append(host)
        return SimpleNamespace()

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(backend.backend, "connect_tcp", connect)
    await backend.connect_tcp("attacker.example", 443)
    import httpcore2

    with pytest.raises(httpcore2.ConnectError):
        await backend.connect_tcp("attacker.example", 443)
    assert connections == ["8.8.8.8"]


async def test_named_secret_storage_only(environment):
    app, _ = environment
    token = secrets.token_urlsafe(32)
    await app.state.credentials.put("woo", token)
    assert await app.state.credentials.get("woo") == token
    await app.state.credentials.put("woo", None)
    assert await app.state.credentials.get("woo") is None


async def test_concurrent_delete_and_edit_do_not_leave_orphaned_credential(
    environment, monkeypatch
):
    app, http = environment
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        row = session.get(Upstream, upstream_id)
        row.auth_type, row.credential_key = "bearer", "upstream-secret"
    secret_data = {"upstream-secret": "old synthetic credential"}
    secret_lock = asyncio.Lock()
    deleting, release = asyncio.Event(), asyncio.Event()

    async def put(key, value):
        async with secret_lock:
            if value is None:
                deleting.set()
                await release.wait()
                secret_data.pop(key, None)
            else:
                secret_data[key] = value

    async def refresh(server_id):
        pass

    monkeypatch.setattr(app.state.credentials, "put", put)
    monkeypatch.setattr(app.state.gateway, "refresh", refresh)
    deletion = asyncio.create_task(http.delete(f"/api/servers/{upstream_id}"))
    await asyncio.wait_for(deleting.wait(), 2)
    editing = asyncio.create_task(
        http.patch(f"/api/servers/{upstream_id}", json={"credential": "new synthetic credential"})
    )
    await asyncio.sleep(0)
    release.set()
    removed, edited = await asyncio.gather(deletion, editing)
    assert removed.status_code == 200
    assert edited.status_code == 404
    assert secret_data == {}
    with app.state.db.session() as session:
        assert session.get(Upstream, upstream_id) is None


async def test_credential_snapshot_waits_for_secret_write_and_registry_revision(
    environment, monkeypatch
):
    app, http = environment
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        row = session.get(Upstream, upstream_id)
        row.auth_type, row.credential_key = "bearer", "tenant"
    upstream, _ = app.state.gateway.lookup(tool_alias("woo", "list_orders"))
    applied, release = asyncio.Event(), asyncio.Event()
    reads = []

    async def put(key, value):
        applied.set()  # The Kubernetes write applied, but its response has not returned.
        await release.wait()

    async def get(key):
        reads.append(key)
        raise AssertionError("A stale registry snapshot must never read the new credential")

    async def refresh(server_id):
        pass

    monkeypatch.setattr(app.state.credentials, "put", put)
    monkeypatch.setattr(app.state.credentials, "get", get)
    monkeypatch.setattr(app.state.gateway, "refresh", refresh)
    editing = asyncio.create_task(
        http.patch(f"/api/servers/{upstream_id}", json={"credential": "synthetic-new-tenant"})
    )
    await asyncio.wait_for(applied.wait(), 2)

    async def connect():
        async with app.state.gateway.connect(upstream):
            raise AssertionError("The old target must not connect after credential rotation")

    connecting = asyncio.create_task(connect())
    await asyncio.sleep(0)
    assert not connecting.done() and reads == []
    release.set()
    changed, denied = await asyncio.gather(editing, connecting, return_exceptions=True)
    assert changed.status_code == 200
    assert isinstance(denied, ValueError) and reads == []


@pytest.mark.parametrize("outcome", ["lost_response", "cancelled"])
async def test_uncertain_credential_update_stays_disabled(environment, monkeypatch, outcome):
    app, http = environment
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        row = session.get(Upstream, upstream_id)
        row.auth_type, row.credential_key = "bearer", "tenant"
    upstream, tool = app.state.gateway.lookup(tool_alias("woo", "list_orders"))
    original_target = target_hash(upstream, tool)
    applied = asyncio.Event()

    async def put(key, value):
        applied.set()
        if outcome == "lost_response":
            raise TimeoutError("The remote write may already have applied")
        await asyncio.Event().wait()

    monkeypatch.setattr(app.state.credentials, "put", put)
    editing = asyncio.create_task(
        http.patch(f"/api/servers/{upstream_id}", json={"credential": "synthetic-new-tenant"})
    )
    await asyncio.wait_for(applied.wait(), 2)
    if outcome == "cancelled":
        editing.cancel()
        await asyncio.gather(editing, return_exceptions=True)
    else:
        assert (await editing).status_code == 503
    upstream, tool = app.state.gateway.lookup(tool_alias("woo", "list_orders"))
    assert not upstream.enabled and upstream.status == "Disabled"
    assert target_hash(upstream, tool) != original_target
    assert "incomplete" in upstream.last_error
    assert not app.state.gateway.registry_lock.locked()


@pytest.mark.parametrize("worker_outcome", ["conflict_retry", "failure"])
async def test_cancelled_kubernetes_worker_finishes_before_a_later_credential_edit(
    environment, monkeypatch, worker_outcome
):
    import base64
    import threading

    from kubernetes.client.exceptions import ApiException

    app, http = environment
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        row = session.get(Upstream, upstream_id)
        row.auth_type, row.credential_key = "bearer", "tenant"
    app.state.settings.secret_backend = "kubernetes"
    loop = asyncio.get_running_loop()
    started, release = asyncio.Event(), threading.Event()
    writes, data = [], {}

    class SecretAPI:
        def read_namespaced_secret(self, *args, **kwargs):
            assert kwargs["_request_timeout"] == 5
            return SimpleNamespace(data=data.copy(), metadata=SimpleNamespace(resource_version="1"))

        def patch_namespaced_secret(self, name, namespace, body, **kwargs):
            assert kwargs["_request_timeout"] == 5
            value = base64.b64decode(body["data"]["tenant"]).decode()
            writes.append(value)
            if len(writes) == 1:
                loop.call_soon_threadsafe(started.set)
                if not release.wait(timeout=5):
                    raise TimeoutError("Test worker was not released")
                raise ApiException(status=409 if worker_outcome == "conflict_retry" else 500)
            data.update(body["data"])

    app.state.credentials.api = SecretAPI()

    async def refresh(server_id):
        pass

    monkeypatch.setattr(app.state.gateway, "refresh", refresh)
    first = asyncio.create_task(
        http.patch(f"/api/servers/{upstream_id}", json={"credential": "synthetic-first"})
    )
    second = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()  # Repeated cancellation must not release the worker's locks.
        await asyncio.sleep(0)
        assert not first.done()
        assert app.state.credentials.lock.locked()
        assert app.state.gateway.registry_lock.locked()
        second = asyncio.create_task(
            http.patch(
                f"/api/servers/{upstream_id}",
                json={"credential": "synthetic-latest", "enabled": True},
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), 0.05)
        assert writes == ["synthetic-first"]
        with app.state.db.session() as session:
            assert not session.get(Upstream, upstream_id).enabled
    finally:
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first, *([second] if second else []), return_exceptions=True), 3
        )
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1].status_code == 200
    expected = ["synthetic-first"] * (2 if worker_outcome == "conflict_retry" else 1)
    assert writes == [*expected, "synthetic-latest"]
    assert await app.state.credentials.get("tenant") == "synthetic-latest"
    assert not app.state.credentials.lock.locked()
    assert not app.state.gateway.registry_lock.locked()
    with app.state.db.session() as session:
        assert session.get(Upstream, upstream_id).enabled


async def test_blank_names_are_rejected_before_mutations(environment):
    app, http = environment
    assert (await http.post("/api/keys", json={"name": "   "})).status_code == 422
    assert (
        await http.post(
            "/api/servers", json={"name": "  ", "slug": "blank", "url": "http://woo:8001/mcp"}
        )
    ).status_code == 422
    upstream_id = seed_upstream(app)
    assert (
        await http.patch(f"/api/servers/{upstream_id}", json={"name": "\t "})
    ).status_code == 422
    invitation = await http.post(
        "/api/invites", json={"email": "blank@example.test", "role": "member"}
    )
    token = invitation.json()["invite_url"].rsplit("/", 1)[1]
    assert (
        await http.post(
            "/api/auth/accept-invite",
            json={"token": token, "name": "  ", "password": secrets.token_urlsafe(24)},
        )
    ).status_code == 422
    assert (await http.get(f"/api/invites/{token}")).status_code == 200
    assert (await http.get("/api/auth/me")).json()["user"]["role"] == "admin"


def test_long_names_and_collisions():
    assert tool_alias("woo", "search") != tool_alias("policies", "search")
    assert tool_alias("woo", "a-b") != tool_alias("woo", "a_b")
    assert len(tool_alias("a" * 24, "b" * 128)) <= 64


async def test_origin_rejected(environment):
    _, http = environment
    assert (
        await http.get("/api/auth/me", headers={"Origin": "https://attacker.example"})
    ).status_code == 403


async def test_real_network_transport_and_upstream_sdk(environment):
    import socket

    import uvicorn

    from upstreams.server import create_app as create_upstream

    app, http = environment
    credential = secrets.token_urlsafe(32)
    app.state.settings.internal_hosts.add("127.0.0.1")
    await app.state.credentials.put("policies", credential)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_upstream("policies", credential), log_level="critical", access_log=False
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        with app.state.db.session() as session:
            upstream = Upstream(
                name="Policies",
                slug="policies",
                url=f"http://127.0.0.1:{port}/mcp",
                auth_type="bearer",
                credential_key="policies",
                permissions={"get_policy": "read", "search": "read"},
            )
            session.add(upstream)
            session.flush()
            upstream_id = upstream.id
        await app.state.gateway.refresh(upstream_id)
        with app.state.db.session() as session:
            upstream = session.get(Upstream, upstream_id)
            assert upstream.status == "Connected", upstream.last_error
            assert {tool["name"] for tool in upstream.tools} == {"get_policy", "search"}
        principal = authenticate(
            app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
        )
        result = await app.state.gateway.execute(
            principal, tool_alias("policies", "get_policy"), {"topic": "shipping"}
        )
        assert not result.is_error, result
        assert "shipping" in result.model_dump_json()
    finally:
        server.should_exit = True
        await task

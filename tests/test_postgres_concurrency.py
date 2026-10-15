"""Opt-in row-lock regressions against an explicitly supplied disposable PostgreSQL.

Set TEST_POSTGRES_URL to a PostgreSQL SQLAlchemy URL. Each case owns a random
schema and removes only that schema. No deployment credentials are discovered.
The two HTTP requests run on independent event loops so synchronous database
queries genuinely contend, as they can across production workers.
"""

import asyncio
import os
import secrets
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier, Lock
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.app import create_app
from backend.config import Settings
from backend.gateway import tool_alias
from backend.models import Confirmation, Invite, LoginSession, Upstream, User, now
from backend.security import csrf, digest
from backend.tool_safety import target_hash


@pytest.fixture
async def postgres_app(tmp_path):
    configured = os.getenv("TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("Set TEST_POSTGRES_URL to run isolated PostgreSQL concurrency checks")
    url = make_url(configured)
    if url.get_backend_name() != "postgresql":
        pytest.fail("TEST_POSTGRES_URL must use PostgreSQL")
    schema = "audit_concurrency_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped = url.update_query_dict(
        {"options": f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=15000"}
    )
    settings = Settings(
        database_url=scoped.render_as_string(hide_password=False),
        testing=True,
        session_secret=secrets.token_urlsafe(48),
        secret_backend="file",
        secret_file=str(tmp_path / "credentials.json"),
        bootstrap_admin_email="admin@example.test",
        bootstrap_admin_password=secrets.token_urlsafe(24),
        allowed_origins="http://testserver",
        allowed_internal_hosts="fixture-upstream",
        bootstrap_servers_json="[]",
    )
    app = create_app(settings)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def lifespan():
        async with app.router.lifespan_context(app):
            started.set()
            await stopped.wait()

    task = asyncio.create_task(lifespan())
    try:
        ready = asyncio.create_task(started.wait())
        done, _ = await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            ready.cancel()
            await task
            pytest.fail("Application lifespan ended before startup")
        yield app
    finally:
        stopped.set()
        try:
            await task
        finally:
            app.state.db.engine.dispose()
            with control.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            control.dispose()


@contextmanager
def simultaneous_row_locks(app, table):
    """Release both requests immediately before their real locking SELECTs."""
    barrier = Barrier(2, timeout=10)
    arrivals = []
    mutex = Lock()

    def before_query(connection, cursor, statement, parameters, context, executemany):
        if f"FROM {table}" in statement and "FOR UPDATE" in statement:
            with mutex:
                arrivals.append(connection)
            barrier.wait()

    event.listen(app.state.db.engine, "before_cursor_execute", before_query)
    try:
        yield arrivals
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", before_query)


async def competing_posts(app, path, body, cookie=None, headers=None):
    def request():
        async def perform():
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
                cookies={"harbor_session": cookie} if cookie else None,
            ) as client:
                result = await client.post(path, json=body)
                identity = None
                if result.status_code == 200:
                    identity = (await client.get("/api/auth/me")).json()
                return result, identity

        return asyncio.run(perform())

    return await asyncio.wait_for(
        asyncio.gather(asyncio.to_thread(request), asyncio.to_thread(request)), timeout=25
    )


async def test_concurrent_invitation_acceptance_creates_one_user_and_session(postgres_app):
    app = postgres_app
    token = secrets.token_urlsafe(32)
    with app.state.db.session() as session:
        invitation = Invite(
            email="invited@example.test",
            role="member",
            token_hash=digest(token),
            expires_at=now() + timedelta(hours=1),
        )
        session.add(invitation)
        session.flush()
        invitation_id = invitation.id

    with simultaneous_row_locks(app, "invites") as arrivals:
        results = await competing_posts(
            app,
            "/api/auth/accept-invite",
            {"token": token, "name": "Invited Member", "password": secrets.token_urlsafe(24)},
        )
    assert len(arrivals) == 2 and arrivals[0] is not arrivals[1]
    assert sorted(result.status_code for result, _ in results) == [200, 404]
    accepted, identity = next(item for item in results if item[0].status_code == 200)
    rejected, _ = next(item for item in results if item[0].status_code == 404)
    assert identity == accepted.json()
    assert identity["user"]["role"] == "member"
    assert "set-cookie" not in rejected.headers
    with app.state.db.session() as session:
        users = list(session.scalars(select(User).where(User.email == "invited@example.test")))
        assert len(users) == 1
        assert users[0].id == identity["user"]["id"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(LoginSession)
                .where(LoginSession.user_id == users[0].id)
            )
            == 1
        )
        assert session.get(Invite, invitation_id).accepted_at is not None


async def test_concurrent_confirmation_approval_dispatches_one_write(postgres_app, monkeypatch):
    app = postgres_app
    cookie = secrets.token_urlsafe(32)
    with app.state.db.session() as session:
        user = session.scalar(select(User).where(User.email == "admin@example.test"))
        login = LoginSession(
            token_hash=digest(cookie), user_id=user.id, expires_at=now() + timedelta(hours=1)
        )
        tool = {
            "name": "create_product",
            "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
        }
        upstream = Upstream(
            name="Fixture catalog",
            slug="fixture",
            url="http://fixture-upstream:8001/mcp",
            tools=[tool],
            permissions={"create_product": "write"},
        )
        session.add_all([login, upstream])
        session.flush()
        expected_target = target_hash(upstream, tool)
        confirmation = Confirmation(
            user_id=user.id,
            session_id=login.id,
            tool_name=tool_alias("fixture", "create_product"),
            target_hash=expected_target,
            arguments={"name": "Exactly once"},
            expires_at=now() + timedelta(minutes=5),
        )
        session.add(confirmation)
        session.flush()
        confirmation_id, login_id = confirmation.id, login.id
    writes = []
    mutex = Lock()

    async def dispatch(principal, name, arguments, expected_target=None, require_read=True):
        assert require_read is False
        assert expected_target == confirmation.target_hash
        assert principal.session_id == login_id
        with mutex:
            writes.append(dict(arguments))
        await asyncio.sleep(0.05)
        return {
            "name": name,
            "arguments": arguments,
            "is_error": False,
            "duration_ms": 50,
            "result": {"structuredContent": {"id": 1, "name": "Exactly once"}},
        }

    monkeypatch.setattr(app.state.agent, "call_gateway", dispatch)
    with simultaneous_row_locks(app, "confirmations") as arrivals:
        results = await competing_posts(
            app,
            "/api/chat/confirm",
            {"id": confirmation_id, "approve": True},
            cookie=cookie,
            headers={"X-CSRF-Token": csrf(app.state.settings.session_secret, login_id)},
        )
    assert len(arrivals) == 2 and arrivals[0] is not arrivals[1]
    assert sorted(result.status_code for result, _ in results) == [200, 404]
    assert writes == [{"name": "Exactly once"}]
    successful = next(result.json() for result, _ in results if result.status_code == 200)
    assert len(successful["tool_calls"]) == 1
    assert successful["tool_calls"][0]["is_error"] is False
    with app.state.db.session() as session:
        assert session.get(Confirmation, confirmation_id).used_at is not None

import asyncio
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import OperationalError

from backend import app as app_module
from backend.models import LoginSession, Upstream, User, now
from backend.security import RateLimiter, authenticate, csrf, digest
from tests.backend.test_backend import environment as environment
from tests.backend.test_backend import seed_upstream
from tests.test_postgres_concurrency import postgres_app as postgres_app


@pytest.mark.parametrize("change", ["demoted", "disabled", "signed_out"])
@pytest.mark.parametrize("operation", ["create", "edit", "remove", "permissions"])
async def test_registry_mutations_recheck_authority_after_waiting(
    environment, monkeypatch, change, operation
):
    app, http = environment
    upstream_id = seed_upstream(app)
    principal = authenticate(
        app.state.db, app.state.settings, cookie=http.cookies.get("harbor_session")
    )
    waiting, release = asyncio.Event(), asyncio.Event()

    class HeldRegistry:
        async def __aenter__(self):
            waiting.set()
            await release.wait()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(app.state.gateway, "registry_lock", HeldRegistry())
    refresh = AsyncMock()
    write_credential = AsyncMock()
    monkeypatch.setattr(app.state.gateway, "refresh", refresh)
    monkeypatch.setattr(app.state.credentials, "put", write_credential)
    requests = {
        "create": (
            "POST",
            "/api/servers",
            {"name": "New", "slug": "new", "url": "http://woo:8001/mcp"},
        ),
        "edit": ("PATCH", f"/api/servers/{upstream_id}", {"name": "Changed"}),
        "remove": ("DELETE", f"/api/servers/{upstream_id}", None),
        "permissions": (
            "PATCH",
            f"/api/servers/{upstream_id}/tools/permissions",
            {"permissions": {"create_product": "read"}},
        ),
    }
    method, path, body = requests[operation]
    task = asyncio.create_task(http.request(method, path, json=body))
    await asyncio.wait_for(waiting.wait(), 2)
    try:
        with app.state.db.session() as session:
            user = session.get(User, principal.id)
            if change == "demoted":
                user.role = "member"
            elif change == "disabled":
                user.active = False
            else:
                session.get(LoginSession, principal.session_id).revoked_at = now()
    finally:
        release.set()
    response = await task
    assert response.status_code == (403 if change == "demoted" else 401)
    write_credential.assert_not_awaited()
    refresh.assert_not_awaited()
    with app.state.db.session() as session:
        upstream = session.get(Upstream, upstream_id)
        assert upstream and upstream.enabled and upstream.name == "Woo"
        assert upstream.permissions == {"list_orders": "read"}


async def test_non_ascii_csrf_is_rejected_without_crashing(environment):
    _, http = environment
    response = await http.post(
        "/api/keys",
        json={"name": "Invalid CSRF"},
        headers={b"X-CSRF-Token": b"\xff"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token is missing or invalid"


@pytest.mark.parametrize("path", ["/api/auth/me", "/mcp"])
async def test_database_outage_returns_sanitized_retryable_response(environment, monkeypatch, path):
    app, http = environment

    @contextmanager
    def unavailable():
        raise OperationalError("private database query", {}, Exception("private password"))
        yield  # pragma: no cover

    with monkeypatch.context() as patch:
        patch.setattr(app.state.db, "session", unavailable)
        response = await http.get(path, headers={"Authorization": "Bearer synthetic-key"})
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert "private" not in response.text
    assert (await http.get("/health/live")).status_code == 200
    assert (await http.get("/api/auth/me")).status_code == 200


async def test_slow_mcp_authentication_does_not_block_event_loop(environment, monkeypatch):
    app, _ = environment
    started, release = threading.Event(), threading.Event()
    timed_out = []

    def slow_authentication(*args, **kwargs):
        started.set()
        if not release.wait(1):
            timed_out.append(True)
        raise HTTPException(401, "Synthetic invalid credential")

    monkeypatch.setattr(app_module, "authenticate", slow_authentication)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        task = asyncio.create_task(http.get("/mcp", headers={"Authorization": "Bearer slow"}))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            async with asyncio.timeout(0.5):
                assert (await http.get("/health/live")).status_code == 200
            assert not timed_out, "MCP authentication blocked other event-loop work"
        finally:
            release.set()
            response = await task
        assert response.status_code == 401


async def test_slow_rejection_audit_does_not_block_event_loop(environment, monkeypatch):
    app, _ = environment
    started, release = threading.Event(), threading.Event()
    timed_out = []

    def denied(*args, **kwargs):
        raise HTTPException(401, "Synthetic invalid credential")

    def slow_audit(*args, **kwargs):
        started.set()
        if not release.wait(1):
            timed_out.append(True)

    monkeypatch.setattr(app_module, "authenticate", denied)
    monkeypatch.setattr(app_module.BoundaryMiddleware, "record_rejected_call", slow_audit)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        task = asyncio.create_task(
            http.post(
                "/mcp",
                headers={"Authorization": "Bearer revoked"},
                json={"method": "tools/call", "params": {"name": "untrusted"}},
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            async with asyncio.timeout(0.5):
                assert (await http.get("/health/live")).status_code == 200
            assert not timed_out, "Rejection auditing blocked other event-loop work"
        finally:
            release.set()
            response = await task
        assert response.status_code == 401


@pytest.mark.parametrize("failure", ["cancelled", "lost_response", "duplicate"])
async def test_failed_server_creation_cleans_up_credential(environment, monkeypatch, failure):
    app, http = environment
    if failure == "duplicate":
        seed_upstream(app)
    applied, cleaning, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    data = {}

    async def put(key, value):
        if value is None:
            cleaning.set()
            await release_cleanup.wait()
            data.pop(key, None)
        else:
            data[key] = value
            applied.set()
            if failure == "cancelled":
                await asyncio.Event().wait()
            if failure == "lost_response":
                raise TimeoutError("Synthetic lost response after remote write")

    monkeypatch.setattr(app.state.credentials, "put", put)
    task = asyncio.create_task(
        http.post(
            "/api/servers",
            json={
                "name": "Woo",
                "slug": "woo",
                "url": "http://woo:8001/mcp",
                "auth_type": "bearer",
                "credential": "synthetic-private-credential",
            },
        )
    )
    try:
        await asyncio.wait_for(applied.wait(), 2)
        if failure == "cancelled":
            task.cancel()
        await asyncio.wait_for(cleaning.wait(), 2)
        if failure == "cancelled":
            task.cancel()  # A repeated disconnect must not abandon the compensating delete.
        await asyncio.sleep(0)
        assert not task.done()
        assert app.state.gateway.registry_lock.locked()
    finally:
        release_cleanup.set()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
    if failure == "cancelled":
        assert isinstance(result, asyncio.CancelledError)
    else:
        assert result.status_code == (409 if failure == "duplicate" else 503)
        assert "synthetic-private-credential" not in result.text
    assert data == {}
    assert not app.state.gateway.registry_lock.locked()
    with app.state.db.session() as session:
        rows = list(session.scalars(select(Upstream)))
        assert len(rows) == (1 if failure == "duplicate" else 0)


@pytest.mark.parametrize("reconciliation_available", [True, False])
async def test_uncertain_database_commit_never_deletes_saved_connection_credential(
    environment, monkeypatch, reconciliation_available
):
    app, http = environment
    original_session = app.state.db.session
    commit_response_lost = False

    @contextmanager
    def lose_commit_response():
        nonlocal commit_response_lost
        if commit_response_lost and not reconciliation_available:
            raise OperationalError("private query", {}, Exception("private credential"))
        with original_session() as session:
            yield session
            creating = any(isinstance(row, Upstream) for row in session.identity_map.values())
        if creating and not commit_response_lost:
            commit_response_lost = True
            raise OperationalError("private query", {}, Exception("commit response lost"))

    write = AsyncMock(wraps=app.state.credentials.put)
    monkeypatch.setattr(app.state.credentials, "put", write)
    with monkeypatch.context() as patch:
        patch.setattr(app.state.db, "session", lose_commit_response)
        response = await http.post(
            "/api/servers",
            json={
                "name": "Woo",
                "slug": "woo",
                "url": "http://woo:8001/mcp",
                "auth_type": "bearer",
                "credential": "synthetic-private-credential",
            },
        )
    assert commit_response_lost
    assert response.status_code == 503
    assert "Refresh connections" in response.json()["detail"]
    assert "private" not in response.text
    assert write.await_count == 1
    with app.state.db.session() as session:
        server = session.scalar(select(Upstream).where(Upstream.slug == "woo"))
        assert server and server.enabled
        assert (
            await app.state.credentials.get(server.credential_key) == "synthetic-private-credential"
        )


def test_rate_limit_returns_remaining_cooldown(monkeypatch):
    instant = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: instant[0])
    limiter = RateLimiter()
    limiter.check("actor", 1)
    instant[0] = 103.4
    with pytest.raises(HTTPException) as error:
        limiter.check("actor", 1)
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "57"
    instant[0] = 160
    limiter.check("actor", 1)


def test_rate_limit_memory_is_bounded_without_resetting_existing_limits(monkeypatch):
    instant = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: instant[0])
    limiter = RateLimiter()
    for index in range(4096):
        limiter.check(str(index), 1)
    with pytest.raises(HTTPException) as full:
        limiter.check("new-actor", 1)
    assert full.value.status_code == 429
    assert len(limiter.buckets) == 4096
    with pytest.raises(HTTPException):
        limiter.check("0", 1)
    instant[0] = 160
    limiter.check("new-actor", 1)
    assert len(limiter.buckets) == 1


async def test_postgres_connection_limits_preserve_schema(postgres_app):
    app = postgres_app
    assert app.state.db.engine.pool.timeout() == 5
    with app.state.db.session() as session:
        assert session.scalar(text("SHOW statement_timeout")) == "15s"
        assert session.scalar(text("SHOW lock_timeout")) == "5s"
        assert session.scalar(text("SHOW search_path")).startswith("audit_concurrency_")


@pytest.mark.parametrize("change", ["demoted", "disabled", "signed_out"])
async def test_member_edit_rechecks_authority_after_postgres_lock(postgres_app, change):
    app = postgres_app
    cookie = secrets.token_urlsafe(32)
    with app.state.db.session() as session:
        administrator = session.scalar(select(User).where(User.role == "admin"))
        target = User(
            email="target@example.test", name="Target", role="member", password_hash="unused"
        )
        login = LoginSession(
            user_id=administrator.id,
            token_hash=digest(cookie),
            expires_at=now() + timedelta(hours=1),
        )
        session.add_all([target, login])
        session.flush()
        admin_id, target_id, session_id = administrator.id, target.id, login.id
    waiting = asyncio.Event()
    loop = asyncio.get_running_loop()

    def before_query(connection, cursor, statement, parameters, context, executemany):
        if "FROM users" in statement and "FOR UPDATE" in statement:
            loop.call_soon_threadsafe(waiting.set)

    def edit():
        async def request():
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
                cookies={"harbor_session": cookie},
                headers={"X-CSRF-Token": csrf(app.state.settings.session_secret, session_id)},
            ) as http:
                return await http.patch(f"/api/members/{target_id}", json={"role": "admin"})

        return asyncio.run(request())

    with app.state.db.engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(select(User.id).where(User.id == admin_id).with_for_update())
        event.listen(app.state.db.engine, "before_cursor_execute", before_query)
        task = asyncio.create_task(asyncio.to_thread(edit))
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            if change == "signed_out":
                connection.execute(
                    update(LoginSession)
                    .where(LoginSession.id == session_id)
                    .values(revoked_at=now())
                )
            else:
                connection.execute(
                    update(User)
                    .where(User.id == admin_id)
                    .values(**({"role": "member"} if change == "demoted" else {"active": False}))
                )
            transaction.commit()
            response = await asyncio.wait_for(task, 5)
        finally:
            if transaction.is_active:
                transaction.rollback()
            event.remove(app.state.db.engine, "before_cursor_execute", before_query)
            await task
    assert response.status_code == (403 if change == "demoted" else 401)
    with app.state.db.session() as session:
        assert session.get(User, target_id).role == "member"


async def test_liveness_does_not_wait_for_saturated_authentication_pool(environment, monkeypatch):
    import anyio.to_thread

    app, _ = environment
    all_started, release = threading.Event(), threading.Event()
    mutex = threading.Lock()
    count = 0

    def slow_authentication(*args, **kwargs):
        nonlocal count
        with mutex:
            count += 1
            if count == 2:
                all_started.set()
        release.wait(5)
        raise HTTPException(401, "Synthetic invalid credential")

    monkeypatch.setattr(app_module, "authenticate", slow_authentication)
    limiter = anyio.to_thread.current_default_thread_limiter()
    previous_tokens = limiter.total_tokens
    limiter.total_tokens = 2
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        tasks = [
            asyncio.create_task(http.get("/mcp", headers={"Authorization": "Bearer slow"}))
            for _ in range(2)
        ]
        try:
            assert await asyncio.to_thread(all_started.wait, 2)
            async with asyncio.timeout(0.5):
                assert (await http.get("/health/live")).status_code == 200
        finally:
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            limiter.total_tokens = previous_tokens
        assert all(result.status_code == 401 for result in results)

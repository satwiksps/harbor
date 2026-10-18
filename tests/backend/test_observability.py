from datetime import datetime, timedelta

import httpx2
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from backend import app as app_module
from backend.models import Audit, Upstream, User
from backend.observability import OUTCOMES, audit_summary
from backend.security import Principal
from tests.backend.test_backend import environment as environment
from tests.backend.test_backend import member_client, seed_upstream, use_client
from tests.test_postgres_concurrency import postgres_app as postgres_app

UNTIL = datetime(2026, 10, 20, 12)


def add_call(session, user_id, outcome="success", duration=100, **overrides):
    values = {
        "user_id": user_id,
        "user_name": "Test user",
        "user_email": "test@example.test",
        "server_id": "deleted-server",
        "server_name": "Removed connection",
        "tool_name": "test_tool",
        "request_id": "test-request",
        "timestamp": UNTIL - timedelta(minutes=10),
        "duration_ms": duration,
        "outcome": outcome,
    }
    session.add(Audit(**(values | overrides)))


def admin_id(app):
    with app.state.db.session() as session:
        return session.scalar(select(User.id).where(User.role == "admin"))


def principal(app, role="admin"):
    return Principal(id=admin_id(app), name="Admin", email="admin@example.test", role=role)


def seed_known_data(app):
    user_id = admin_id(app)
    upstream_id = seed_upstream(app)
    with app.state.db.session() as session:
        for outcome, duration in (
            ("success", 10),
            ("success", 20),
            ("tool_error", 30),
            ("error", 40),
            ("timeout", 100),
        ):
            add_call(
                session,
                user_id,
                outcome,
                duration,
                server_id=upstream_id,
                server_name="Original name",
            )
        # These terminal states must not distort execution failure rate or latency.
        for outcome in ("denied", "invalid_arguments", "cancelled", "pending", "future_outcome"):
            add_call(session, user_id, outcome, 90000, server_id=upstream_id)
        for duration in (200, 300):
            add_call(session, user_id, duration=duration)
        add_call(session, user_id, "denied", 0, server_id=None, server_name="Gateway")
        add_call(session, user_id, "denied", 0, server_id=None, server_name="Unknown")
        session.get(Upstream, upstream_id).name = "Current name"
    return upstream_id


def assert_known_summary(summary, upstream_id):
    total = summary["totals"]
    assert total["calls"] == 14
    assert total["completed_calls"] == total["latency_samples"] == 7
    assert total["failed_calls"] == 3
    assert total["failure_rate"] == pytest.approx(3 / 7)
    assert total["p50_latency_ms"] == 40
    assert total["p95_latency_ms"] == pytest.approx(270)
    assert total["outcomes"] == {
        "success": 4,
        "tool_error": 1,
        "error": 1,
        "timeout": 1,
        "pending": 1,
        "denied": 3,
        "invalid_arguments": 1,
        "cancelled": 1,
        "other": 1,
    }
    assert sum(total["outcomes"].values()) == total["calls"]
    by_server = {item["server_id"]: item for item in summary["servers"]}
    active = by_server[upstream_id]
    assert active["server_name"] == "Current name"
    assert not active["server_deleted"]
    assert active["failure_rate"] == pytest.approx(3 / 5)
    assert active["p50_latency_ms"] == 30
    assert active["p95_latency_ms"] == pytest.approx(88)
    removed = by_server["deleted-server"]
    assert removed["server_deleted"]
    assert removed["server_name"] == "Removed connection"
    assert removed["p50_latency_ms"] == 250
    assert removed["p95_latency_ms"] == 295
    unattributed = by_server[None]
    assert unattributed["server_name"] == "Unattributed"
    assert not unattributed["server_deleted"]
    assert unattributed["calls"] == 2
    assert unattributed["completed_calls"] == unattributed["latency_samples"] == 0
    assert unattributed["failure_rate"] is None
    assert unattributed["p50_latency_ms"] is None
    assert unattributed["p95_latency_ms"] is None


async def test_summary_uses_all_outcomes_and_continuous_percentiles(environment, monkeypatch):
    app, http = environment
    upstream_id = seed_known_data(app)
    monkeypatch.setattr(app_module, "now", lambda: UNTIL)
    statements = []

    def before_query(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(app.state.db.engine, "before_cursor_execute", before_query)
    try:
        response = await http.get("/api/audit/summary")
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", before_query)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["window"] == "24h"
    assert summary["scope"] == "team"
    assert summary["user_id"] is None
    assert summary["server_id"] is None
    assert_known_summary(summary, upstream_id)
    aggregate_statements = [query for query in statements if "summary_scope" in query]
    assert len(aggregate_statements) == 1
    assert "row_number() OVER" in aggregate_statements[0]
    assert all("FROM audit" not in query or query in aggregate_statements for query in statements)


@pytest.mark.parametrize("window,hours", [("1h", 1), ("24h", 24), ("7d", 168)])
async def test_summary_window_is_start_inclusive_end_exclusive(
    environment, monkeypatch, window, hours
):
    app, http = environment
    user_id = admin_id(app)
    start = UNTIL - timedelta(hours=hours)
    with app.state.db.session() as session:
        for timestamp, duration in (
            (start - timedelta(microseconds=1), 90000),
            (start, 10),
            (UNTIL - timedelta(microseconds=1), 30),
            (UNTIL, 90000),
            (UNTIL + timedelta(seconds=1), 90000),
        ):
            add_call(session, user_id, duration=duration, timestamp=timestamp)
    monkeypatch.setattr(app_module, "now", lambda: UNTIL)
    response = await http.get("/api/audit/summary", params={"window": window})
    assert response.status_code == 200
    summary = response.json()
    assert summary["from"] == start.isoformat() + "Z"
    assert summary["to"] == UNTIL.isoformat() + "Z"
    assert summary["totals"]["calls"] == 2
    assert summary["totals"]["p50_latency_ms"] == 20
    assert summary["totals"]["p95_latency_ms"] == 29


async def test_summary_empty_and_zero_duration_singleton(environment, monkeypatch):
    app, http = environment
    monkeypatch.setattr(app_module, "now", lambda: UNTIL)
    response = await http.get("/api/audit/summary")
    empty = response.json()
    assert response.status_code == 200
    assert empty["servers"] == []
    assert empty["totals"] == {
        "calls": 0,
        "completed_calls": 0,
        "failed_calls": 0,
        "failure_rate": None,
        "latency_samples": 0,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "outcomes": dict.fromkeys((*OUTCOMES, "other"), 0),
    }
    with app.state.db.session() as session:
        add_call(session, admin_id(app), duration=0)
    singleton = (await http.get("/api/audit/summary")).json()["totals"]
    assert singleton["calls"] == singleton["latency_samples"] == 1
    assert singleton["failure_rate"] == 0
    assert singleton["p50_latency_ms"] == singleton["p95_latency_ms"] == 0


async def test_summary_groups_renamed_and_deleted_servers_by_id(environment, monkeypatch):
    app, http = environment
    upstream_id = seed_upstream(app)
    user_id = admin_id(app)
    with app.state.db.session() as session:
        add_call(session, user_id, server_id=upstream_id, server_name="Z Old name")
        add_call(
            session,
            user_id,
            server_id=upstream_id,
            server_name="A Latest name",
            timestamp=UNTIL - timedelta(minutes=1),
        )
        add_call(session, user_id, server_id="another-deleted-id", server_name="A Latest name")
        session.delete(session.get(Upstream, upstream_id))
    monkeypatch.setattr(app_module, "now", lambda: UNTIL)
    summary = (await http.get("/api/audit/summary")).json()
    servers = {item["server_id"]: item for item in summary["servers"]}
    assert len(servers) == 2
    assert servers[upstream_id]["calls"] == 2
    assert servers[upstream_id]["server_deleted"]
    assert servers[upstream_id]["server_name"] == "A Latest name"
    filtered = await http.get("/api/audit/summary", params={"server_id": upstream_id})
    assert filtered.json()["totals"]["calls"] == 2
    assert len(filtered.json()["servers"]) == 1
    missing = await http.get("/api/audit/summary", params={"server_id": "not-found"})
    assert missing.json()["totals"]["calls"] == 0


async def test_summary_permissions_and_combined_filters(environment, monkeypatch):
    app, http = environment
    member, user = await member_client(app, http)
    user_id = admin_id(app)
    with app.state.db.session() as session:
        add_call(session, user_id, "error", 400)
        add_call(session, user["id"], "success", 100)
        add_call(session, user["id"], "timeout", 200, server_id="other-server")
    monkeypatch.setattr(app_module, "now", lambda: UNTIL)
    async with use_client(member):
        for requested_user in (None, user_id, user["id"], "unknown"):
            response = await member.get(
                "/api/audit/summary",
                params={"user_id": requested_user} if requested_user else {},
            )
            summary = response.json()
            assert response.status_code == 200
            assert summary["scope"] == "self"
            assert summary["user_id"] == user["id"]
            assert summary["totals"]["calls"] == 2
            assert summary["totals"]["outcomes"]["error"] == 0
            assert summary["totals"]["p50_latency_ms"] == 150
        filtered = await member.get(
            "/api/audit/summary", params={"server_id": "deleted-server", "user_id": user_id}
        )
        assert filtered.json()["totals"]["calls"] == 1
        assert filtered.json()["totals"]["p95_latency_ms"] == 100
    all_team = (await http.get("/api/audit/summary")).json()
    assert all_team["totals"]["calls"] == 3
    selected = await http.get(
        "/api/audit/summary", params={"user_id": user["id"], "server_id": "other-server"}
    )
    assert selected.json()["totals"]["calls"] == 1
    assert selected.json()["totals"]["failure_rate"] == 1
    assert selected.json()["user_id"] == user["id"]
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as anonymous:
        assert (await anonymous.get("/api/audit/summary")).status_code == 401


@pytest.mark.parametrize(
    "params,field",
    [
        ({"window": "30d"}, "window"),
        ({"window": ""}, "window"),
        ({"server_id": "x" * 37}, "server_id"),
        ({"user_id": "x" * 37}, "user_id"),
    ],
)
async def test_summary_validation_uses_normal_api_errors(environment, params, field):
    _, http = environment
    response = await http.get("/api/audit/summary", params=params)
    assert response.status_code == 422
    assert response.json()["detail"].startswith(field + ":")


async def test_summary_database_failure_is_sanitized_and_retryable(environment, monkeypatch):
    _, http = environment

    def unavailable(*args, **kwargs):
        raise OperationalError("SELECT secret_database_value", {}, RuntimeError("private host"))

    monkeypatch.setattr(app_module, "audit_summary", unavailable)
    response = await http.get("/api/audit/summary")
    assert response.status_code == 503
    assert response.json() == {"detail": "Activity summary is temporarily unavailable"}
    assert response.headers["Retry-After"] == "1"


async def test_summary_postgres_matches_sqlite_percentiles(postgres_app):
    upstream_id = seed_known_data(postgres_app)
    summary = audit_summary(postgres_app.state.db, principal(postgres_app), "24h", UNTIL)
    assert_known_summary(summary, upstream_id)

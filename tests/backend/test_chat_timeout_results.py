from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from backend.gateway import tool_alias
from backend.models import Confirmation
from tests.backend.test_backend import environment as environment
from tests.backend.test_backend import seed_upstream
from tests.backend.test_chat_resilience import answer, ask, provider_error
from tests.backend.test_chat_resilience import provider_wire as provider_wire


@pytest.mark.parametrize("failure", [
    provider_error(408),
    provider_error(504),
    httpx.ReadTimeout("private-provider-key and confidential tool data"),
    TimeoutError("private-provider-key and confidential tool data"),
])
async def test_summary_timeout_preserves_completed_read_without_replay(
    environment, provider_wire, monkeypatch, caplog, failure
):
    app, http = environment
    seed_upstream(app)
    name = tool_alias("woo", "list_orders")
    requests = provider_wire([
        answer([{"functionCall": {"name": name, "args": {}}}]),
        failure,
    ])
    result = {
        "name": name, "arguments": {}, "is_error": False,
        "result": {"structuredContent": {"orders": [{"id": 42, "status": "processing"}]}},
        "duration_ms": 1,
    }
    read = AsyncMock(return_value=result)
    monkeypatch.setattr(app.state.agent, "call_gateway", read)

    response = await ask(http)

    assert response.status_code == 200
    assert response.json() == {
        "message": (
            "The AI response timed out. Available tool results are shown below; "
            "this answer may be incomplete."
        ),
        "tool_calls": [result],
    }
    assert len(requests) == 2
    read.assert_awaited_once()
    assert read.call_args.kwargs["require_read"] is True
    with app.state.db.session() as session:
        assert session.scalar(select(Confirmation)) is None
    assert "private-provider-key" not in response.text + caplog.text
    assert "confidential tool data" not in response.text + caplog.text

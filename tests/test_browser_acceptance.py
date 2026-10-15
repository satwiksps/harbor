"""Activity-only checks cannot certify or overwrite a full browser journey."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests import browser_acceptance


@pytest.mark.parametrize("previous_complete", [None, False, True])
@pytest.mark.parametrize("audit_passes", [False, True])
async def test_activity_check_preserves_full_journey(
    tmp_path, monkeypatch, previous_complete, audit_passes
):
    secret_dir = tmp_path / ".secrets"
    secret_dir.mkdir()
    (secret_dir / "development.json").write_text(
        json.dumps({"admin_email": "fixture@example.test", "admin_password": "fixture"}),
        encoding="utf-8",
    )
    output = tmp_path / "browser"
    output.mkdir()
    full_report = output / "report.json"
    previous = None
    if previous_complete is not None:
        previous = json.dumps(
            {"complete": previous_complete, "checks": ["Browser administrator login"]}
        ).encode()
        full_report.write_bytes(previous)

    page = MagicMock()
    page.get_by_role.return_value.click = AsyncMock()
    page.screenshot = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=playwright)
    manager.__aexit__ = AsyncMock(return_value=False)
    assertion = MagicMock()
    assertion.to_be_visible = AsyncMock(
        side_effect=None if audit_passes else AssertionError("Activity row is missing")
    )
    monkeypatch.setattr(browser_acceptance, "ROOT", tmp_path)
    monkeypatch.setattr(browser_acceptance, "OUT", output)
    monkeypatch.setattr(browser_acceptance, "async_playwright", lambda: manager)
    monkeypatch.setattr(browser_acceptance, "expect", lambda _: assertion)
    monkeypatch.setattr(browser_acceptance, "login", AsyncMock())

    if audit_passes:
        await browser_acceptance.main(audit_only=True)
    else:
        with pytest.raises(AssertionError, match="Activity row is missing"):
            await browser_acceptance.main(audit_only=True)

    if previous is None:
        assert not full_report.exists()
    else:
        assert full_report.read_bytes() == previous
    audit_report = json.loads((output / "audit-report.json").read_text("utf-8"))
    assert audit_report["scope"] == "activity-only"
    assert audit_report["complete"] is audit_passes
    assert "Browser administrator login" not in audit_report["checks"]
    if audit_passes:
        assert "Tool activity visible in browser" in audit_report["checks"]
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()

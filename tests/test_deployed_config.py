"""Public deployment checks must not reuse local URLs, secrets or evidence paths."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("public_target", [False, True])
def test_harness_targets_and_private_files_stay_together(tmp_path, public_target):
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("HARBOR_")
    }
    secret_root = tmp_path / "private-vps" if public_target else ROOT / ".secrets"
    output_root = tmp_path / "vps-evidence" if public_target else ROOT / ".runtime"
    console = "https://console.example.test" if public_target else "http://harbor.localhost:8080"
    store = "https://store.example.test" if public_target else "http://store.localhost:8080"
    if public_target:
        environment.update(
            HARBOR_URL=console + "/",
            HARBOR_STORE_URL=store + "/",
            HARBOR_TEST_CREDENTIALS=str(secret_root / "credentials.json"),
            HARBOR_TEST_OUTPUT_DIR=str(output_root),
        )
    # Import the actual entry points in a clean process, without opening a browser
    # or contacting any deployment. No credential file is read by this check.
    code = """
import json
from tests import acceptance, browser_acceptance, browser_invitation, browser_ui
from tests import browser_visual, browser_accessibility, browser_observability, external_agent
from tests.deployed_config import credentials_path, private_path, output_path
print(json.dumps({
    'console': [module.BASE for module in
        [browser_acceptance, browser_invitation, browser_ui, browser_visual]],
    'fixture_console': [browser_accessibility.BASE, browser_observability.BASE],
    'store': [browser_acceptance.STORE, browser_visual.STORE],
    'credentials': str(credentials_path(acceptance.ROOT)),
    'member': str(private_path(acceptance.ROOT, 'demo-member.json')),
    'reports': [str(path) for path in [
        output_path(acceptance.ROOT, 'acceptance.json'),
        browser_acceptance.OUT / 'report.json',
        output_path(browser_invitation.ROOT, 'browser-invitation', 'report.json'),
        output_path(browser_ui.ROOT, 'browser-ui', 'report.json'),
        browser_visual.OUT / 'report.json',
        browser_accessibility.OUT / 'report.json',
        browser_observability.OUT / 'report.json',
        external_agent.REPORT,
    ]],
    'external_work': str(external_agent.WORK),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    target = json.loads(result.stdout)
    assert target["console"] == [console] * 4
    assert target["fixture_console"] == (
        [console] * 2 if public_target else ["http://127.0.0.1:4173", "http://127.0.0.1:5175"]
    )
    assert target["store"] == [store] * 2
    assert Path(target["credentials"]) == secret_root / (
        "credentials.json" if public_target else "development.json"
    )
    assert Path(target["member"]) == secret_root / "demo-member.json"
    assert Path(target["external_work"]) == secret_root / "external-client"
    assert all(Path(report).is_relative_to(output_root) for report in target["reports"])
    assert len(set(target["reports"])) == 8

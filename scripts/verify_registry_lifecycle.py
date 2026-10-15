"""Delete one seeded registration, restart its services, and restore it securely."""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from local_settings import KUBE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.acceptance import Acceptance  # noqa: E402


async def read_servers(admin):
    deadline = time.monotonic() + 60
    last_error = None
    while time.monotonic() < deadline:
        try:
            response = await admin.get("/api/servers")
            response.raise_for_status()
            return response.json()
        except Exception as error:
            last_error = error
            await asyncio.sleep(2)
    raise RuntimeError("Gateway ingress did not recover within 60 seconds") from last_error


def kube(*arguments):
    result = subprocess.run(
        [*KUBE, *arguments],
        capture_output=True,
        text=True,
        timeout=240,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


async def main():
    credentials = json.loads((ROOT / ".secrets/development.json").read_text(encoding="utf-8-sig"))
    admin, _ = await Acceptance("http://harbor.localhost:8080").login(
        credentials["admin_email"], credentials["admin_password"]
    )
    original = None
    deleted = False
    report = {}
    try:
        servers = await read_servers(admin)
        assert len(servers) == 3, "Start with only the three demonstration connections"
        original = next(server for server in servers if server["slug"] == "shipping")
        assert original["enabled"] and original["status"] == "Connected"
        permissions = {tool["name"]: tool["permission"] for tool in original["tools"]}
        (ROOT / ".runtime/registry-lifecycle-original.json").write_text(
            json.dumps(original, indent=2), encoding="utf-8"
        )
        report["original_registration_id"] = original["id"]
        deleted = True
        (await admin.delete(f"/api/servers/{original['id']}")).raise_for_status()
        for deployment in ("shipping-mcp", "gateway"):
            kube("rollout", "restart", "deployment/" + deployment)
            kube("rollout", "status", "deployment/" + deployment, "--timeout=180s")
        after_restart = await read_servers(admin)
        assert not any(server["slug"] == "shipping" for server in after_restart)
        report["deleted_connection_stays_deleted_after_restart"] = True
        report["upstream_restarts_without_gateway_credential"] = True
        deployment = json.loads(kube("get", "deployment/shipping-mcp", "-o", "json"))
        token = next(
            entry
            for entry in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
            if entry["name"] == "UPSTREAM_TOKEN"
        )
        assert token["valueFrom"]["secretKeyRef"] == {
            "name": "harbor-app-secrets",
            "key": "upstream_shipping",
        }
        report["service_token_uses_separate_application_secret"] = True
    finally:
        try:
            if deleted and original:
                # Preserve the administrator-facing registration and permissions. Its new ID is expected.
                existing = await read_servers(admin)
                restored = next(
                    (server for server in existing if server["slug"] == "shipping"), None
                )
                if restored is None:
                    created = await admin.post(
                        "/api/servers",
                        json={
                            **{
                                field: original[field]
                                for field in (
                                    "name",
                                    "slug",
                                    "url",
                                    "auth_type",
                                    "header_name",
                                    "enabled",
                                )
                            },
                            "credential": credentials["upstream_shipping"],
                        },
                    )
                    created.raise_for_status()
                    restored = created.json()
                (await admin.post(f"/api/servers/{restored['id']}/refresh")).raise_for_status()
                (
                    await admin.patch(
                        f"/api/servers/{restored['id']}/tools/permissions",
                        json={"permissions": permissions},
                    )
                ).raise_for_status()
                final = await read_servers(admin)
                assert len(final) == 3 and all(server["status"] == "Connected" for server in final)
                restored = next(server for server in final if server["slug"] == "shipping")
                assert {
                    tool["name"]: tool["permission"] for tool in restored["tools"]
                } == permissions
                report["restored_registration_id"] = restored["id"]
                report["all_three_connections_healthy"] = True
        finally:
            await admin.aclose()
    destination = ROOT / ".runtime/registry-lifecycle-verification.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        "PASS: deleted seeded connection stays deleted; upstream restarts; registration and permissions restored"
    )
    print(f"Evidence written to {destination}; no credentials were printed.")


if __name__ == "__main__":
    asyncio.run(main())

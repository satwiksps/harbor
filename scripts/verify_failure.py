"""Hold one upstream down, verify isolation, then restore it even after a failure."""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.acceptance import Acceptance, unpack  # noqa: E402


def kube(*args):
    result = subprocess.run(
        ["kubectl", "--context", "k3d-urumi-commerce", "-n", "commerce", *args],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


async def main():
    credentials = json.loads((ROOT / ".secrets/development.json").read_text(encoding="utf-8-sig"))
    base = "http://console.localhost:8080"
    admin, _ = await Acceptance(base).login(
        credentials["admin_email"], credentials["admin_password"]
    )
    key_id = None
    scaled_down = False
    report = {}
    try:
        servers = (await admin.get("/api/servers")).json()
        shipping = next(server for server in servers if server["slug"] == "shipping")
        assert shipping["enabled"], "Enable the shipping service before this controlled outage test"
        original = json.loads(kube("get", "deployment/shipping-mcp", "-o", "json"))
        assert original["spec"]["replicas"] == 1, "Expected the single-replica local demonstration"
        before = json.loads(kube("get", "pods", "-l", "app=shipping-mcp", "-o", "json"))
        report["deleted_pod_uid"] = before["items"][0]["metadata"]["uid"]
        response = await admin.post(
            "/api/keys", json={"name": "Infrastructure outage verification"}
        )
        response.raise_for_status()
        key = response.json()
        key_id = key["record"]["id"]
        async with (
            httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + key["key"]}, trust_env=False
            ) as http,
            Client(streamable_http_client(base + "/mcp", http_client=http), cache=None) as client,
        ):
            names = {tool.name for tool in (await client.list_tools()).tools}
            quote = next(name for name in names if "__quote_shipping_" in name)
            policy = next(name for name in names if "__get_policy_" in name)
            products = next(name for name in names if "__list_products_" in name)
            scaled_down = True
            kube("scale", "deployment/shipping-mcp", "--replicas=0")
            # Scaling the controller to zero deletes its actual Pod and holds the outage for observation.
            kube(
                "wait",
                "--for=delete",
                "pod/" + before["items"][0]["metadata"]["name"],
                "--timeout=60s",
            )
            refreshed = await admin.post(f"/api/servers/{shipping['id']}/refresh")
            refreshed.raise_for_status()
            state = next(
                s for s in (await admin.get("/api/servers")).json() if s["slug"] == "shipping"
            )
            assert state["status"] == "Unreachable", state["status"]
            report["dashboard_status_during_outage"] = state["status"]
            started = time.monotonic()
            listed = (await client.list_tools()).tools
            report["tool_listing_seconds"] = round(time.monotonic() - started, 3)
            assert report["tool_listing_seconds"] < 5
            assert any(tool.name == policy for tool in listed)
            failed = await client.call_tool(quote, {"subtotal": "1000.00"})
            assert failed.is_error
            report["failed_upstream_returns_tool_error"] = True
            for name, arguments in [(policy, {"topic": "shipping"}), (products, {})]:
                result = await client.call_tool(name, arguments)
                assert not result.is_error, unpack(result)
            report["other_two_upstreams_continue"] = True
        kube("scale", "deployment/shipping-mcp", "--replicas=1")
        scaled_down = False
        kube("rollout", "status", "deployment/shipping-mcp", "--timeout=120s")
        (await admin.post(f"/api/servers/{shipping['id']}/refresh")).raise_for_status()
        recovered = next(
            s for s in (await admin.get("/api/servers")).json() if s["slug"] == "shipping"
        )
        assert recovered["status"] == "Connected"
        report["recovered_status"] = recovered["status"]
        after = json.loads(kube("get", "pods", "-l", "app=shipping-mcp", "-o", "json"))
        report["replacement_pod_uid"] = after["items"][0]["metadata"]["uid"]
        assert report["replacement_pod_uid"] != report["deleted_pod_uid"]
        destination = ROOT / ".runtime/failure-verification.json"
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PASS: actual upstream Pod deletion, fast discovery, isolated failure and recovery")
        print(f"Evidence written to {destination}")
    finally:
        try:
            if scaled_down:
                kube("scale", "deployment/shipping-mcp", "--replicas=1")
                kube("rollout", "status", "deployment/shipping-mcp", "--timeout=120s")
                (await admin.post(f"/api/servers/{shipping['id']}/refresh")).raise_for_status()
        finally:
            try:
                if key_id:
                    (await admin.delete(f"/api/keys/{key_id}")).raise_for_status()
            finally:
                await admin.aclose()


if __name__ == "__main__":
    asyncio.run(main())

"""Run against a deployed cluster. Creates a real member and revokes its test keys.

Usage: uv run python -m tests.acceptance [--chat]
Secrets are loaded from ignored local files and never printed.
"""

import argparse
import asyncio
import json
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]


def unpack(result):
    if result.structured_content is not None:
        return result.structured_content
    for block in result.content:
        if block.type == "text":
            try:
                return json.loads(block.text)
            except ValueError:
                return {"text": block.text}
    return {}


class Acceptance:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.report = []

    def record(self, name, detail=""):
        self.report.append({"check": name, "passed": True, "detail": detail})
        print("PASS", name, flush=True)

    async def login(self, email, password):
        http = httpx.AsyncClient(
            base_url=self.base, timeout=120, trust_env=False, headers={"Origin": self.base}
        )
        response = await http.post("/api/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return http, response.json()["user"]

    async def run(self, chat=False):
        credentials = json.loads(
            (ROOT / ".secrets/development.json").read_text(encoding="utf-8-sig")
        )
        admin, admin_user = await self.login(
            credentials["admin_email"], credentials["admin_password"]
        )
        member = None
        keys = []
        disabled = None
        added_server = None
        try:
            self.record("Administrator session and CSRF")
            for _ in range(40):
                servers = (await admin.get("/api/servers")).json()
                if len(servers) >= 3 and all(s["status"] == "Connected" for s in servers):
                    break
                await asyncio.sleep(2)
            assert len(servers) >= 3, "Expected three registered upstreams"
            assert all(s["status"] == "Connected" for s in servers), [
                (s["name"], s["last_error"]) for s in servers
            ]
            self.record("All three upstreams connected")
            # UI stores each credential once; registry reads may only describe it.
            serialized = json.dumps(servers)
            for name in ("upstream_woo", "upstream_policies", "upstream_shipping", "woo_secret"):
                assert credentials[name] not in serialized
            self.record("Registry never returns stored credentials")

            email = f"member-{secrets.token_hex(3)}@example.test"
            password = secrets.token_urlsafe(20)
            invite = await admin.post("/api/invites", json={"email": email, "role": "member"})
            invite.raise_for_status()
            invite_url = invite.json()["invite_url"]
            parts = urlsplit(invite_url)
            token = parse_qs(parts.query).get("token", [parts.path.rsplit("/", 1)[-1]])[0]
            async with httpx.AsyncClient(base_url=self.base, trust_env=False) as anonymous:
                accepted = await anonymous.post(
                    "/api/auth/accept-invite",
                    json={"token": token, "name": "Demo member", "password": password},
                )
                accepted.raise_for_status()
            member, member_user = await self.login(email, password)
            (ROOT / ".secrets/demo-member.json").write_text(
                json.dumps({"email": email, "password": password, "id": member_user["id"]}),
                encoding="utf-8",
            )
            self.record("Single-use invitation and second user login")
            replay = await admin.post(
                "/api/auth/accept-invite",
                json={"token": token, "name": "Replay", "password": password},
            )
            assert replay.status_code >= 400
            self.record("Invitation replay rejected")

            key_a = await admin.post("/api/keys", json={"name": "Acceptance admin"})
            key_b = await member.post("/api/keys", json={"name": "Acceptance member"})
            key_a.raise_for_status()
            key_b.raise_for_status()
            a, b = key_a.json(), key_b.json()
            keys.extend([a["record"]["id"], b["record"]["id"]])
            async with (
                httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + a["key"]}, trust_env=False
                ) as ah,
                httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + b["key"]}, trust_env=False
                ) as bh,
                Client(
                    streamable_http_client(self.base + "/mcp", http_client=ah), cache=None
                ) as ac,
                Client(
                    streamable_http_client(self.base + "/mcp", http_client=bh), cache=None
                ) as bc,
            ):
                admin_tools = (await ac.list_tools()).tools
                member_tools = (await bc.list_tools()).tools
                a_names, b_names = {t.name for t in admin_tools}, {t.name for t in member_tools}
                create = next(n for n in a_names if "__create_product_" in n)
                assert create not in b_names
                assert len([n for n in b_names if "__search_" in n]) == 2
                self.record(
                    "External MCP discovery and collision-safe names",
                    f"{len(b_names)} member tools",
                )
                denied = await bc.call_tool(create, {"name": "Must not exist", "price": "1.00"})
                assert denied.is_error
                self.record("Hidden write tool rejected on direct member call")
                for fragment, arguments in [
                    ("__get_policy_", {"topic": "shipping"}),
                    ("__quote_shipping_", {"subtotal": "1500.00"}),
                    ("__list_products_", {}),
                    ("__list_orders_", {}),
                ]:
                    name = next(n for n in b_names if fragment in n)
                    result = await bc.call_tool(name, arguments)
                    assert not result.is_error, unpack(result)
                self.record("Tools from all three upstreams return real results")

                policies = next(s for s in servers if s["slug"] == "policies")
                added = await admin.post(
                    "/api/servers",
                    json={
                        "name": "Registry acceptance",
                        "slug": "acceptance",
                        "url": policies["url"],
                        "auth_type": "bearer",
                        "credential": credentials["upstream_policies"],
                        "enabled": True,
                    },
                )
                added.raise_for_status()
                added_server = added.json()["id"]
                (await admin.post(f"/api/servers/{added_server}/refresh")).raise_for_status()
                discovered = (await ac.list_tools()).tools
                new_alias = next(
                    t.name for t in discovered if t.name.startswith("acceptance__get_policy_")
                )
                assert new_alias not in {t.name for t in (await bc.list_tools()).tools}
                permission = await admin.patch(
                    f"/api/servers/{added_server}/tools/permissions",
                    json={"permissions": {"get_policy": "read"}},
                )
                permission.raise_for_status()
                assert new_alias in {t.name for t in (await bc.list_tools()).tools}
                edited = await admin.patch(
                    f"/api/servers/{added_server}",
                    json={"name": "Registry acceptance edited", "credential": ""},
                )
                edited.raise_for_status()
                read_back = await bc.call_tool(new_alias, {"topic": "returns"})
                assert not read_back.is_error, unpack(read_back)
                assert credentials["upstream_policies"] not in json.dumps(edited.json())
                (await admin.delete(f"/api/servers/{added_server}")).raise_for_status()
                added_server = None
                assert new_alias not in {t.name for t in (await ac.list_tools()).tools}
                assert (await bc.call_tool(new_alias, {"topic": "returns"})).is_error
                self.record("Registry add, edit, permission change and delete over real MCP")

                shipping = next(s for s in servers if s["slug"] == "shipping")
                changed = await admin.patch(
                    f"/api/servers/{shipping['id']}", json={"enabled": False}
                )
                changed.raise_for_status()
                disabled = shipping["id"]
                listing = (await bc.list_tools()).tools
                assert not any(t.name.startswith("shipping__") for t in listing)
                stale = await bc.call_tool(
                    next(n for n in b_names if "__quote_shipping_" in n), {"subtotal": "10"}
                )
                assert stale.is_error
                healthy = await bc.call_tool(
                    next(n for n in b_names if "__get_policy_" in n), {"topic": "returns"}
                )
                assert not healthy.is_error
                self.record("Disabled tools vanish and stale calls fail; other services work")
                (
                    await admin.patch(f"/api/servers/{shipping['id']}", json={"enabled": True})
                ).raise_for_status()
                disabled = None

                (await admin.delete(f"/api/keys/{b['record']['id']}")).raise_for_status()
                # Use the same established external-client transport after revocation.
                rejected = False
                try:
                    await bc.call_tool(next(n for n in b_names if "__list_orders_" in n), {})
                except Exception:
                    rejected = True
                assert rejected, "Revoked key was accepted on its existing connection"
                self.record("Revocation rejects next call on an existing connection")

            forbidden = await member.post(
                "/api/servers",
                json={"name": "Denied", "slug": "denied", "url": "https://example.com/mcp"},
            )
            assert forbidden.status_code == 403
            ssrf = await admin.post(
                "/api/servers",
                json={
                    "name": "Metadata",
                    "slug": "metadata",
                    "url": "http://169.254.169.254/latest/meta-data/",
                },
            )
            assert ssrf.status_code in {400, 422}
            self.record("Registry roles and metadata SSRF guard enforced")

            if chat:
                result = await admin.post(
                    "/api/chat",
                    json={
                        "messages": [
                            {
                                "role": "user",
                                "content": "Read the shipping policy, then use the shipping quote tool for an INR 1700 order to Karnataka, India. Tell me the shipping charge. Use both tools.",
                            }
                        ]
                    },
                )
                result.raise_for_status()
                data = result.json()
                names = [c["name"] for c in data["tool_calls"]]
                assert any("policies__" in n for n in names) and any(
                    "shipping__" in n for n in names
                ), names
                assert all(not c["is_error"] for c in data["tool_calls"])
                self.record("Live Gemini chat invokes two upstreams through gateway")

            audit = (await admin.get("/api/audit?limit=100")).json()
            rows = audit["items"]
            assert any(
                r["user_id"] == member_user["id"] and r["outcome"] == "success" for r in rows
            )
            assert any(r["user_id"] == member_user["id"] and r["outcome"] == "denied" for r in rows)
            own = (await member.get("/api/audit?limit=100")).json()["items"]
            assert all(r["user_id"] == member_user["id"] for r in own)
            self.record("Audit attribution and member record isolation")
            # This member was created during the run, so all its calls are in
            # the bounded overview. A forged teammate filter must remain scoped.
            personal_response = await member.get(
                "/api/audit/summary", params={"window": "24h", "user_id": admin_user["id"]}
            )
            personal_response.raise_for_status()
            personal = personal_response.json()
            team_response = await admin.get(
                "/api/audit/summary", params={"window": "24h", "user_id": member_user["id"]}
            )
            team_response.raise_for_status()
            team = team_response.json()
            assert personal["scope"] == "self"
            assert personal["user_id"] == member_user["id"]
            assert personal["totals"] == team["totals"]
            assert personal["servers"] == team["servers"]
            totals = personal["totals"]
            assert totals["calls"] >= len(own) > 0
            assert totals["calls"] == sum(totals["outcomes"].values())
            assert totals["calls"] == sum(row["calls"] for row in personal["servers"])
            assert totals["failed_calls"] == sum(
                totals["outcomes"][name] for name in ("error", "tool_error", "timeout")
            )
            assert totals["latency_samples"] > 0
            assert 0 <= totals["p50_latency_ms"] <= totals["p95_latency_ms"]
            self.record("Activity metrics match live calls and preserve member isolation")
        finally:
            if added_server:
                await admin.delete(f"/api/servers/{added_server}")
            if disabled:
                await admin.patch(f"/api/servers/{disabled}", json={"enabled": True})
            for key_id in keys:
                await admin.delete(f"/api/keys/{key_id}")
            if member:
                await member.aclose()
            await admin.aclose()
            path = ROOT / ".runtime/acceptance.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(
                json.dumps({"timestamp": time.time(), "checks": self.report}, indent=2),
                encoding="utf-8",
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://harbor.localhost:8080")
    parser.add_argument("--chat", action="store_true")
    args = parser.parse_args()
    asyncio.run(Acceptance(args.base).run(args.chat))

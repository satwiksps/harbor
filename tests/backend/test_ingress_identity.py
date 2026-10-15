"""Exercise the deployed Uvicorn proxy boundary against real auth routes."""

import httpx2
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from backend.models import User
from backend.security import hash_password
from tests.backend.test_backend import environment as environment


@pytest.mark.parametrize(
    "method,path,body,allowance,rejected",
    [
        ("POST", "/api/auth/login", {"email": "missing@example.test", "password": "invalid"}, 10, 401),
        ("GET", "/api/invites/missing", None, 30, 404),
        (
            "POST",
            "/api/auth/accept-invite",
            {"token": "missing", "name": "A tester", "password": "a-valid-test-password"},
            10,
            404,
        ),
    ],
)
async def test_ingress_clients_have_independent_auth_limits(
    environment, method, path, body, allowance, rejected
):
    app, _ = environment
    proxy = ProxyHeadersMiddleware(app, trusted_hosts=["10.42.0.0/24"])
    transport = httpx2.ASGITransport(app=proxy, client=("10.42.0.96", 40000))
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for _ in range(allowance):
            result = await client.request(
                method, path, json=body, headers={"X-Forwarded-For": "198.51.100.10"}
            )
            assert result.status_code == rejected
        result = await client.request(
            method, path, json=body, headers={"X-Forwarded-For": "198.51.100.10"}
        )
        assert result.status_code == 429
        result = await client.request(
            method, path, json=body, headers={"X-Forwarded-For": "198.51.100.11"}
        )
        assert result.status_code == rejected
        if path == "/api/auth/login":
            password = "isolated-ingress-test-password"
            with app.state.db.session() as session:
                session.add(
                    User(
                        email="member@example.test",
                        name="Member",
                        password_hash=hash_password(password),
                    )
                )
            result = await client.post(
                path,
                json={"email": "member@example.test", "password": password},
                headers={"X-Forwarded-For": "198.51.100.11"},
            )
            assert result.status_code == 200
            assert result.json()["user"]["role"] == "member"


@pytest.mark.parametrize("peer", ["198.51.100.10", "127.0.0.1"])
async def test_direct_clients_cannot_rotate_forwarded_headers_to_bypass_limits(environment, peer):
    app, _ = environment
    proxy = ProxyHeadersMiddleware(app, trusted_hosts=["10.42.0.0/24"])
    transport = httpx2.ASGITransport(app=proxy, client=(peer, 40000))
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for attempt in range(31):
            result = await client.get(
                "/api/invites/missing", headers={"X-Forwarded-For": f"203.0.113.{attempt + 1}"}
            )
            assert result.status_code == (404 if attempt < 30 else 429)


async def test_forwarded_chain_uses_nearest_untrusted_client(environment):
    app, _ = environment
    proxy = ProxyHeadersMiddleware(app, trusted_hosts=["10.42.0.0/24"])
    transport = httpx2.ASGITransport(app=proxy, client=("10.42.0.96", 40000))
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for attempt in range(31):
            # Even if another trusted proxy retains client-supplied prefixes, the
            # nearest untrusted peer stays the identity for the entire allowance.
            result = await client.get(
                "/api/invites/missing",
                headers=[
                    ("X-Forwarded-For", f"203.0.113.{attempt + 1}"),
                    ("X-Forwarded-For", "198.51.100.10, 10.42.0.95"),
                ],
            )
            assert result.status_code == (404 if attempt < 30 else 429)

"""Exercise real HTTP and official SDK serialization, not mocked MCP calls."""

import asyncio
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


@pytest.mark.asyncio
async def test_real_streamable_http_roundtrip():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = {**os.environ, "UPSTREAM_KIND": "policies", "PORT": str(port), "UPSTREAM_TOKEN": token}
    process = subprocess.Popen(
        [sys.executable, "-m", "upstreams.server"],
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(trust_env=False) as health:
            for _ in range(100):
                try:
                    if (await health.get(base + "/health/ready")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                pytest.fail("MCP service did not become ready")
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + token}, trust_env=False
        ) as http:
            async with Client(
                streamable_http_client(base + "/mcp", http_client=http), cache=None
            ) as client:
                listing = await client.list_tools()
                assert "get_policy" in {tool.name for tool in listing.tools}
                result = await client.call_tool("get_policy", {"topic": "shipping"})
                assert not result.is_error
                assert result.structured_content["free_shipping_threshold"] == "1500.00"
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)

"""Outbound MCP transport with validation at the actual DNS/connect boundary."""

import asyncio
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit

import httpcore2
import httpx2


def validate_url(url: str, internal_hosts: set[str]):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
        if host in internal_hosts:
            if parsed.scheme not in {"http", "https"}:
                raise ValueError()
        elif parsed.scheme != "https":
            raise ValueError()
        elif host == "localhost" or host.endswith(
            (".localhost", ".local", ".internal", ".svc", ".cluster.local")
        ):
            raise ValueError()
        if host not in internal_hosts:
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address and not address.is_global:
                raise ValueError()
        return parsed
    except ValueError:
        raise ValueError(
            "Use a public HTTPS MCP URL, or an exact configured internal service host. URL credentials, query strings and fragments are forbidden."
        ) from None


def validate_header(name: str | None):
    if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,99}", name):
        raise ValueError("A valid credential header name is required")
    if name.lower() in {
        "host",
        "cookie",
        "content-length",
        "content-type",
        "connection",
        "transfer-encoding",
        "origin",
        "accept",
        "authorization",
    } or name.lower().startswith(("x-forwarded-", "mcp-", "proxy-", "sec-")):
        raise ValueError(
            "That header cannot be used for custom credentials; use bearer for Authorization"
        )


class SafeNetworkBackend(httpcore2.AsyncNetworkBackend):
    def __init__(self, internal_hosts: set[str]):
        self.internal_hosts = internal_hosts
        self.backend = httpcore2.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        hostname = host.decode() if isinstance(host, bytes) else host
        hostname = hostname.lower().rstrip(".")
        loop = asyncio.get_running_loop()
        records = await asyncio.wait_for(
            loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM), timeout or 5
        )
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses:
            raise httpcore2.ConnectError("MCP hostname did not resolve")
        if hostname not in self.internal_hosts and any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise httpcore2.ConnectError("MCP destination resolved to a forbidden address")
        # Connect to the validated numeric address, preserving original TLS SNI in httpcore.
        return await self.backend.connect_tcp(
            addresses[0], port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore2.ConnectError("Unix sockets are not allowed")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


class LimitedStream(httpx2.AsyncByteStream):
    def __init__(self, stream, maximum=1024 * 1024):
        self.stream = stream
        self.maximum = maximum

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.maximum:
                raise ValueError("Upstream response exceeded the 1 MiB limit")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class SafeTransport(httpx2.AsyncBaseTransport):
    def __init__(self, internal_hosts):
        self.pool = httpcore2.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=4,
            network_backend=SafeNetworkBackend(internal_hosts),
            retries=0,
        )

    async def handle_async_request(self, request):
        # Bound the bytes the application consumes. Compression could expand beyond
        # the transport limit before httpx exposes a decoded chunk to the caller.
        request.headers["Accept-Encoding"] = "identity"
        response = await self.pool.handle_async_request(
            httpcore2.Request(
                method=request.method,
                url=httpcore2.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        if 300 <= response.status < 400:
            await response.aclose()
            raise ValueError("Upstream redirects are disabled; register its final MCP endpoint")
        encoding = httpx2.Headers(response.headers).get("content-encoding", "identity")
        if encoding.strip().lower() not in {"", "identity"}:
            await response.aclose()
            raise ValueError(
                "Compressed upstream responses are disabled to enforce the response size limit"
            )
        return httpx2.Response(
            response.status,
            headers=response.headers,
            stream=LimitedStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.pool.aclose()

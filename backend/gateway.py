import asyncio
import hashlib
import json
import logging
import re
import time
from contextlib import asynccontextmanager

import httpx2
import jsonschema
from fastapi import HTTPException
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.caching import CacheHint
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from backend.models import Audit, Upstream, now, uid
from backend.network import SafeTransport, validate_url
from backend.security import refresh_principal
from backend.tool_safety import redact_credential, schema_validator, target_hash

logger = logging.getLogger(__name__)


def tool_alias(slug: str, original: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_]", "_", original)[:24]
    suffix = hashlib.sha256(original.encode()).hexdigest()[:8]
    return f"{slug[:24]}__{readable}_{suffix}"


def allowed(principal, upstream, original: str) -> bool:
    return principal.role == "admin" or upstream.permissions.get(original) == "read"


def tool_error(message: str):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


class Gateway:
    def __init__(self, db, settings, credentials):
        self.db, self.settings, self.credentials = db, settings, credentials
        self.refresh_slots = asyncio.Semaphore(4)
        self.registry_lock = asyncio.Lock()
        self.call_slots = {}
        self.refresh_locks = {}
        self.server = Server(
            "commerce-console",
            version="0.1.0",
            on_list_tools=self.list_handler,
            on_call_tool=self.call_handler,
            cache_hints={"tools/list": CacheHint(ttl_ms=0, scope="private")},
        )
        from urllib.parse import urlsplit

        hosts = {
            urlsplit(settings.gateway_url).netloc,
            urlsplit(settings.internal_gateway_url).netloc,
        }
        if settings.testing:
            hosts.add("testserver")
        self.mcp_app = self.server.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            max_request_body_size=128 * 1024,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=sorted(hosts),
                allowed_origins=sorted(settings.origins),
            ),
        )

    @asynccontextmanager
    async def connect(self, upstream):
        async with self.registry_lock:
            # A Secret update and its database revision form one application mutation.
            # Never mix a newly written credential with the previous registry snapshot.
            with self.db.session() as session:
                current = session.get(Upstream, upstream.id)
                if not current or not current.enabled or current.revision != upstream.revision:
                    raise ValueError("Connection changed before credential retrieval")
            validate_url(upstream.url, self.settings.internal_hosts)
            credential = await self.credentials.get(upstream.credential_key)
        headers = {}
        if upstream.auth_type != "none":
            if not credential:
                raise ValueError("Upstream credential is missing")
            if "\r" in credential or "\n" in credential:
                raise ValueError("Invalid credential header")
            headers["Authorization" if upstream.auth_type == "bearer" else upstream.header_name] = (
                "Bearer " + credential if upstream.auth_type == "bearer" else credential
            )
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(self.settings.tool_timeout_seconds, connect=5),
            transport=SafeTransport(self.settings.internal_hosts),
            trust_env=False,
        ) as http:
            async with Client(
                streamable_http_client(upstream.url, http_client=http),
                cache=None,
                read_timeout_seconds=self.settings.tool_timeout_seconds,
            ) as client:
                yield client, credential

    def list_for(self, principal):
        tools = []
        with self.db.session() as session:
            upstreams = session.scalars(select(Upstream).where(Upstream.enabled.is_(True))).all()
            for upstream in upstreams:
                for tool in upstream.tools:
                    if not allowed(principal, upstream, tool["name"]):
                        continue
                    value = dict(tool)
                    value["name"] = tool_alias(upstream.slug, tool["name"])
                    value["description"] = f"[{upstream.name}] {tool.get('description', '')}"[:4000]
                    value["annotations"] = {
                        **(value.get("annotations") or {}),
                        "readOnlyHint": upstream.permissions.get(tool["name"]) == "read",
                    }
                    tools.append(types.Tool.model_validate(value))
        return sorted(tools, key=lambda tool: tool.name)

    def lookup(self, name):
        with self.db.session() as session:
            for upstream in session.scalars(select(Upstream)).all():
                for tool in upstream.tools:
                    if tool_alias(upstream.slug, tool["name"]) == name:
                        return upstream, tool
        return None, None

    async def list_handler(self, ctx, params):
        principal = ctx.request.scope["commerce.principal"]
        tools = self.list_for(principal)
        try:
            offset = int(params.cursor or "0") if params else 0
            if offset < 0:
                raise ValueError()
        except ValueError:
            from mcp.shared.exceptions import MCPError

            raise MCPError(code=-32602, message="Invalid tools cursor") from None
        page = tools[offset : offset + 100]
        return types.ListToolsResult(
            tools=page, next_cursor=str(offset + 100) if offset + 100 < len(tools) else None
        )

    async def call_handler(self, ctx, params):
        return await self.execute(
            ctx.request.scope["commerce.principal"],
            params.name,
            params.arguments or {},
            str(ctx.request_id or uid()),
            expected_target=ctx.request.headers.get("x-commerce-target"),
            require_read=ctx.request.headers.get("x-commerce-read-only") == "true",
        )

    async def execute(
        self, principal, name, arguments, request_id=None, expected_target=None, require_read=False
    ):
        started = time.monotonic()
        upstream, tool = self.lookup(name)
        audit_id = uid()
        with self.db.session() as session:
            session.add(
                Audit(
                    id=audit_id,
                    user_id=principal.id,
                    user_name=principal.name,
                    user_email=principal.email,
                    server_id=upstream.id if upstream else None,
                    server_name=upstream.name if upstream else "Unknown",
                    tool_name=name[:150],
                    request_id=(request_id or uid())[:100],
                    outcome="pending",
                )
            )
        outcome, error = "error", None
        try:
            if (
                not upstream
                or not upstream.enabled
                or not allowed(principal, upstream, tool["name"])
            ):
                outcome, error = "denied", "Tool is unavailable or not permitted for this user"
                return tool_error(error)
            slot = self.call_slots.setdefault(upstream.id, asyncio.Semaphore(4))
            original_upstream_id = upstream.id
            async with asyncio.timeout(self.settings.tool_timeout_seconds):
                async with slot:
                    principal = refresh_principal(self.db, principal)
                    upstream, tool = self.lookup(name)
                    if (
                        not upstream
                        or upstream.id != original_upstream_id
                        or not upstream.enabled
                        or not allowed(principal, upstream, tool["name"])
                    ):
                        outcome, error = (
                            "denied",
                            "Tool is unavailable or not permitted for this user",
                        )
                        return tool_error(error)
                    if expected_target and expected_target != target_hash(upstream, tool):
                        outcome, error = (
                            "denied",
                            "Connection or tool changed; request a new confirmation",
                        )
                        return tool_error(error)
                    if require_read and upstream.permissions.get(tool["name"]) != "read":
                        outcome, error = "denied", "This tool requires review and confirmation"
                        return tool_error(error)
                    try:
                        schema_validator(tool["inputSchema"]).validate(arguments)
                    except (jsonschema.ValidationError, jsonschema.SchemaError, ValueError):
                        outcome, error = "invalid_arguments", "Tool arguments or schema are invalid"
                        return tool_error(error)
                    connected_target = target_hash(upstream, tool)
                    async with self.connect(upstream) as (client, credential):
                        # Secret retrieval and the MCP handshake can wait too. Recheck
                        # before dispatching an operation to that established connection.
                        principal = refresh_principal(self.db, principal)
                        current_upstream, current_tool = self.lookup(name)
                        if (
                            not current_upstream
                            or not current_upstream.enabled
                            or not allowed(principal, current_upstream, current_tool["name"])
                            or connected_target != target_hash(current_upstream, current_tool)
                        ):
                            outcome, error = (
                                "denied",
                                "Connection or permissions changed before the tool could run",
                            )
                            return tool_error(error)
                        result = await client.call_tool(tool["name"], arguments)
            encoded = result.model_dump_json(by_alias=True)
            if len(encoded.encode()) > 256 * 1024:
                error = "Tool result exceeded the 256 KiB gateway limit"
                return tool_error(error)
            result = types.CallToolResult.model_validate(
                redact_credential(json.loads(encoded), credential)
            )
            outcome = "tool_error" if result.is_error else "success"
            error = "Upstream tool returned an error" if result.is_error else None
            return result
        except HTTPException:
            outcome, error = "denied", "User access changed while the call was waiting"
            return tool_error(error)
        except TimeoutError:
            outcome, error = (
                "timeout",
                "Upstream deadline exceeded. A write may have completed; verify before retrying.",
            )
            return tool_error(error)
        except asyncio.CancelledError:
            outcome, error = "cancelled", "Client disconnected or request was cancelled"
            raise
        except Exception as exc:
            # Exception messages can contain URLs, headers or response data. Do not persist them.
            error = f"Upstream request failed ({type(exc).__name__}). Check server health and credentials."
            return tool_error(error)
        finally:
            with self.db.session() as session:
                row = session.get(Audit, audit_id)
                row.duration_ms = int((time.monotonic() - started) * 1000)
                row.outcome, row.error = outcome, error

    async def refresh(self, server_id):
        lock = self.refresh_locks.setdefault(server_id, asyncio.Lock())
        async with lock, self.refresh_slots:
            with self.db.session() as session:
                upstream = session.get(Upstream, server_id)
                if not upstream or not upstream.enabled:
                    return
                revision = upstream.revision
            tools, error = [], None
            try:
                async with asyncio.timeout(self.settings.discovery_timeout_seconds):
                    async with self.connect(upstream) as (client, credential):
                        cursor, seen, aliases = None, set(), set()
                        for _ in range(10):
                            page = await client.list_tools(cursor=cursor)
                            for item in page.tools:
                                if item.name in seen:
                                    raise ValueError("Duplicate upstream tool names")
                                alias = tool_alias(upstream.slug, item.name)
                                if alias in aliases or (credential and credential in item.name):
                                    raise ValueError("Unsafe or ambiguous upstream tool name")
                                aliases.add(alias)
                                seen.add(item.name)
                                value = redact_credential(
                                    item.model_dump(by_alias=True, exclude_none=True), credential
                                )
                                schema_validator(value["inputSchema"])
                                if value.get("outputSchema") is not None:
                                    schema_validator(value["outputSchema"])
                                tools.append(value)
                            if not page.next_cursor:
                                break
                            cursor = page.next_cursor
                        else:
                            raise ValueError("Too many tool pages")
                        if len(tools) > 200 or len(json.dumps(tools)) > 256 * 1024:
                            raise ValueError("Upstream catalog is too large")
            except TimeoutError:
                error = "Discovery timed out"
            except Exception as exc:
                error = f"Discovery failed ({type(exc).__name__}); verify endpoint, credentials and protocol"
            with self.db.session() as session:
                current = session.get(Upstream, server_id)
                if not current or not current.enabled or current.revision != revision:
                    return
                current.last_checked_at = now()
                current.last_error = error
                current.status = "Unreachable" if error else "Connected"
                if not error:
                    current.tools = tools

    async def supervise(self):
        while True:
            try:
                with self.db.session() as session:
                    ids = list(
                        session.scalars(select(Upstream.id).where(Upstream.enabled.is_(True)))
                    )
                await asyncio.gather(*(self.refresh(server_id) for server_id in ids))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Registry health cycle failed; retrying on the next cycle")
            await asyncio.sleep(self.settings.health_interval_seconds)

"""Bound untrusted schema work and disable external reference retrieval."""

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
from referencing import Registry


class SchemaBoundary:
    """Reuse one killable worker so schema CPU never runs on the API event loop."""

    def __init__(self, timeout_seconds=2.0, startup_timeout_seconds=5.0):
        self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self._lock = asyncio.Lock()
        self._process = None
        self._closed = False

    async def check(self, schema):
        await self._request({"schema": schema})

    async def validate(self, schema, arguments):
        await self._request({"schema": schema, "arguments": arguments})

    async def _request(self, request):
        _check_structure(request)
        try:
            payload = json.dumps(request, allow_nan=False, ensure_ascii=False).encode() + b"\n"
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Tool schema and arguments must be JSON") from exc
        if len(payload) > 512 * 1024:
            raise ValueError("Tool schema and arguments exceed the validation limit")
        # Only one validation can consume CPU. The caller's tool/discovery deadline
        # bounds queueing and startup; the shorter worker deadline bounds untrusted work.
        async with self._lock:
            if self._closed:
                raise ValueError("Schema validation is closed")
            try:
                if self._process is None:
                    # Python imports can be slow on a busy machine. Wait for trusted
                    # startup to finish before charging the schema's execution budget.
                    async with asyncio.timeout(self.startup_timeout_seconds):
                        self._process = await asyncio.create_subprocess_exec(
                            sys.executable,
                            "-u",
                            str(Path(__file__).resolve()),
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                        )
                        if await self._process.stdout.readline() != b"ready\n":
                            raise ValueError("Schema validation worker did not become ready")
                async with asyncio.timeout(self.timeout_seconds):
                    process = self._process
                    process.stdin.write(payload)
                    await process.stdin.drain()
                    result = await process.stdout.readline()
                    if result not in (b"valid\n", b"invalid\n"):
                        raise ValueError("Schema validation worker failed")
            except BaseException:
                # A cancelled request must not leave expensive work running or
                # allow the next request to consume the previous result.
                await self._stop()
                raise
            if result == b"invalid\n":
                raise ValueError("Tool schema or arguments are invalid")

    async def _stop(self):
        process, self._process = self._process, None
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            if process.stdin:
                process.stdin.close()
            await process.wait()

    async def close(self):
        async with self._lock:
            self._closed = True
            await self._stop()


def _check_structure(value):
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > 20_000:
            raise ValueError("Tool schema and arguments exceed the structural limit")
        if isinstance(item, dict):
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


def schema_validator(schema):
    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
                reference = value.get(keyword)
                if reference is not None and (
                    not isinstance(reference, str) or not reference.startswith("#")
                ):
                    raise ValueError("Tool schemas may only reference their own document")
            # Property maps and instance data are not themselves schemas. A property
            # named '$ref', or a literal $ref inside default/const/examples, is valid data.
            for keyword in (
                "$defs",
                "definitions",
                "properties",
                "patternProperties",
                "dependentSchemas",
                "dependencies",
            ):
                mapping = value.get(keyword)
                if isinstance(mapping, dict):
                    pending.extend(
                        item for item in mapping.values() if isinstance(item, (dict, bool))
                    )
            for keyword in (
                "additionalProperties",
                "unevaluatedProperties",
                "propertyNames",
                "contains",
                "unevaluatedItems",
                "items",
                "additionalItems",
                "not",
                "if",
                "then",
                "else",
            ):
                item = value.get(keyword)
                if isinstance(item, (dict, bool)):
                    pending.append(item)
            for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
                items = value.get(keyword)
                if isinstance(items, list):
                    pending.extend(items)
    jsonschema.Draft202012Validator.check_schema(schema)
    # Explicitly avoid jsonschema's default compatibility registry, which can fetch URLs.
    return jsonschema.Draft202012Validator(schema, registry=Registry())


def redact_credential(value, credential):
    if not credential:
        return value
    if isinstance(value, str):
        representations = {
            credential,
            json.dumps(credential, ensure_ascii=False)[1:-1],
            json.dumps(credential, ensure_ascii=True)[1:-1],
        }
        for representation in sorted(representations, key=len, reverse=True):
            value = value.replace(representation, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {
            redact_credential(key, credential): redact_credential(item, credential)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_credential(item, credential) for item in value]
    return value


def target_hash(upstream, tool):
    if not upstream or not tool:
        return None
    target = {
        "server_id": upstream.id,
        "revision": upstream.revision,
        "url": upstream.url,
        "name": upstream.name,
        "tool": tool,
        "permission": upstream.permissions.get(tool["name"], "write"),
    }
    return hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()


def _validation_worker():
    if sys.platform == "linux":
        import resource

        # A short regex with a large counted repeat can exhaust memory before the
        # wall deadline. Limit this child independently of the gateway container.
        memory_limit = 128 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    sys.stdout.buffer.write(b"ready\n")
    sys.stdout.buffer.flush()
    for line in sys.stdin.buffer:
        try:
            request = json.loads(line)
            validator = schema_validator(request["schema"])
            if "arguments" in request:
                validator.validate(request["arguments"])
        except Exception:
            result = "invalid\n"
        else:
            result = "valid\n"
        sys.stdout.buffer.write(result.encode())
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    _validation_worker()

"""Validate untrusted tool schemas without network or filesystem reference retrieval."""

import hashlib
import json

import jsonschema
from referencing import Registry


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

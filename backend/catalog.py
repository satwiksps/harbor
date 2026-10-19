"""Bounded model declarations and local discovery over permission-filtered tools."""

import json
import re
from dataclasses import dataclass

from google.genai import types as genai_types
from mcp import types

MAX_DIRECT_TOOLS = 40
MAX_DECLARATION_BYTES = 48 * 1024
SEARCH_NAME = "harbor_search_tools"
SEARCH_DECLARATION = genai_types.FunctionDeclaration(
    name=SEARCH_NAME,
    description=(
        "Find available tools by task, tool name or server name. An empty query browses tools. "
        "Matching tools marked enabled become callable on your next turn with their full schemas. "
        "Each search replaces the previous selection. Refine the query or use next_offset for more."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 200},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True)
class CatalogEntry:
    tool: types.Tool
    target_hash: str


def declaration(entry):
    return genai_types.FunctionDeclaration(
        name=entry.tool.name,
        description=(entry.tool.description or "")[:4000],
        parameters_json_schema=entry.tool.input_schema,
    )


def declaration_bytes(declarations):
    # Measure the serialized declarations, including descriptions and complete
    # schemas. This is an application budget, not a provider token-limit estimate.
    return len(
        json.dumps(
            [item.model_dump(by_alias=True, exclude_none=True) for item in declarations],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


class ChatCatalog:
    def __init__(self, entries):
        direct = [declaration(entry) for entry in entries]
        self.progressive = (
            len(entries) > MAX_DIRECT_TOOLS or declaration_bytes(direct) > MAX_DECLARATION_BYTES
        )
        self.active = {} if self.progressive else {entry.tool.name: entry for entry in entries}

    def declarations(self):
        return ([SEARCH_DECLARATION] if self.progressive else []) + [
            declaration(entry) for entry in self.active.values()
        ]

    def reconcile(self, entries):
        current = {entry.tool.name: entry for entry in entries}
        self.active = {
            name: entry
            for name, entry in self.active.items()
            if name in current and current[name].target_hash == entry.target_hash
        }

    def search(self, entries, arguments):
        def integer(value):
            # Providers may encode JSON Schema integers as 5.0 in function args.
            return type(value) is int or (type(value) is float and value.is_integer())

        query, offset, limit = (
            arguments.get("query"),
            arguments.get("offset", 0),
            arguments.get("limit", 5),
        )
        if (
            set(arguments) - {"query", "offset", "limit"}
            or not isinstance(query, str)
            or len(query) > 200
            or not integer(offset)
            or offset < 0
            or not integer(limit)
            or not 1 <= limit <= 5
        ):
            return {"error": "Use a query of at most 200 characters, offset >= 0 and limit 1 to 5."}
        offset, limit = int(offset), int(limit)
        tokens = re.findall(r"\w+", query.casefold())
        matches = []
        for entry in entries:
            name = entry.tool.name.casefold()
            description = (entry.tool.description or "").casefold()
            score = sum(
                3 if token in name else 1 if token in description else 0 for token in tokens
            )
            if not tokens or score:
                matches.append((score, entry))
        matches.sort(key=lambda item: (-item[0], item[1].tool.name))
        self.active = {}
        results = []
        for _, entry in matches[offset : offset + limit]:
            candidate = declaration(entry)
            single_fits = (
                declaration_bytes([SEARCH_DECLARATION, candidate]) <= MAX_DECLARATION_BYTES
            )
            enabled = single_fits and (
                declaration_bytes(self.declarations() + [candidate]) <= MAX_DECLARATION_BYTES
            )
            result = {
                "name": entry.tool.name,
                "description": (entry.tool.description or "")[:240],
                "read_only": bool(
                    entry.tool.annotations and entry.tool.annotations.read_only_hint is True
                ),
                "enabled": enabled,
            }
            if enabled:
                self.active[entry.tool.name] = entry
            else:
                result["reason"] = (
                    "Schema exceeds the chat declaration budget. Use an external MCP client."
                    if not single_fits
                    else "Search for this exact tool name to load its schema."
                )
            results.append(result)
        next_offset = offset + limit
        return {
            "matches": len(matches),
            "results": results,
            "next_offset": next_offset if next_offset < len(matches) else None,
        }

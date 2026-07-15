from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from threading import RLock
from typing import Any, Literal, cast

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .models import Purpose, SearchRequest
from .search import DeepLaw

Operation = Literal["search", "get", "verify", "release_info"]

_DESCRIPTION = (
    "Read-only Chinese-law research gateway. Call only after an explicit Chinese-law "
    "research request; never call for ordinary code, data, document, or analytics work, "
    "and never activate from a lone legal-looking keyword. Operations: search bounded "
    "evidence, get an exact segment, verify a receipt, or inspect the active immutable release."
)
_INSTRUCTIONS = (
    "Read-only, version-aware Chinese legal research. Use only for explicit legal questions. "
    "Search returns at most five evidence cards; fetch full text only by selected segment_id. "
    "Never treat retrieval as proof of case facts or applicability."
)
_OUTPUT_CONTRACTS = {
    "search": "law-search-response.v2.schema.json",
    "segment": "law-segment.v2.schema.json",
    "verification": "law-verification.v1.schema.json",
    "release_info": "law-release-info.v2.schema.json",
    "evidence": "legal-evidence-card.v2.schema.json",
    "release_manifest": "corpus-release-manifest.v2.schema.json",
}


@dataclass(frozen=True)
class _RuntimeContext:
    law: DeepLaw
    lock: RLock


def _contract_path(name: str) -> Path:
    packaged = Path(__file__).resolve().parent / "contracts" / name
    if packaged.is_file():
        return packaged
    repository = Path(__file__).resolve().parents[2] / "contracts" / name
    if repository.is_file():
        return repository
    raise RuntimeError(f"DeepLaw contract is missing: {name}")


@cache
def _load_contract(name: str) -> dict[str, Any]:
    return json.loads(_contract_path(name).read_text(encoding="utf-8"))


def _rewrite_refs(value: Any, references: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_rewrite_refs(item, references) for item in value]
    if not isinstance(value, dict):
        return value
    rewritten = {key: _rewrite_refs(item, references) for key, item in value.items()}
    reference = rewritten.get("$ref")
    if isinstance(reference, str):
        basename = reference.rsplit("/", 1)[-1]
        if reference in references:
            rewritten["$ref"] = references[reference]
        elif basename in references:
            rewritten["$ref"] = references[basename]
    return rewritten


@lru_cache(maxsize=1)
def bundled_output_schema() -> dict[str, Any]:
    schema = deepcopy(_load_contract("law-support.output.v2.schema.json"))
    references: dict[str, str] = {}
    for name, filename in _OUTPUT_CONTRACTS.items():
        target = f"#/$defs/{name}"
        references[filename] = target
        schema_id = _load_contract(filename).get("$id")
        if isinstance(schema_id, str):
            references[schema_id] = target
    definitions: dict[str, Any] = {}
    for name, filename in _OUTPUT_CONTRACTS.items():
        definition = deepcopy(_load_contract(filename))
        definition.pop("$schema", None)
        definition.pop("$id", None)
        definitions[name] = _rewrite_refs(definition, references)
    schema.pop("$id", None)
    schema["$defs"] = definitions
    return _rewrite_refs(schema, references)


def tool_definition() -> types.Tool:
    return types.Tool(
        name="law_support",
        description=_DESCRIPTION,
        inputSchema=deepcopy(_load_contract("law-support.input.v1.schema.json")),
        outputSchema=deepcopy(bundled_output_schema()),
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )


def _execute_support(
    law: DeepLaw,
    *,
    operation: Operation,
    query: str = "",
    purpose: Purpose = "auto",
    as_of: str | None = None,
    limit: int = 5,
    max_chars: int = 3500,
    document_types: list[str] | None = None,
    segment_id: str | None = None,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    if operation == "release_info":
        return law.release_info()
    if operation == "get":
        if not segment_id:
            raise ValueError("segment_id is required for operation=get")
        return law.get(segment_id, max_chars=max_chars)
    if operation == "verify":
        if not segment_id or not receipt_id:
            raise ValueError("segment_id and receipt_id are required for operation=verify")
        return law.verify(segment_id, receipt_id)
    if operation != "search":
        raise ValueError(f"unsupported operation: {operation}")
    request = SearchRequest(
        query=query,
        purpose=purpose,
        as_of=as_of,
        limit=limit,
        max_chars=max_chars,
        document_types=tuple(document_types or ()),
    )
    return law.search(request).to_dict()


def handle_support(
    *,
    operation: Operation = "search",
    query: str = "",
    purpose: Purpose = "auto",
    as_of: str | None = None,
    limit: int = 5,
    max_chars: int = 3500,
    document_types: list[str] | None = None,
    segment_id: str | None = None,
    receipt_id: str | None = None,
    database: str | Path | None = None,
) -> dict[str, Any]:
    """Execute one read-only DeepLaw operation outside the MCP transport."""

    with DeepLaw(database) as law:
        return _execute_support(
            law,
            operation=operation,
            query=query,
            purpose=purpose,
            as_of=as_of,
            limit=limit,
            max_chars=max_chars,
            document_types=document_types,
            segment_id=segment_id,
            receipt_id=receipt_id,
        )


def create_mcp_server() -> Server[_RuntimeContext]:
    @asynccontextmanager
    async def lifespan(_: Server[_RuntimeContext]) -> AsyncIterator[_RuntimeContext]:
        with DeepLaw() as law:
            yield _RuntimeContext(law=law, lock=RLock())

    server: Server[_RuntimeContext] = Server(
        "DeepLaw",
        version=__version__,
        instructions=_INSTRUCTIONS,
        lifespan=lifespan,
    )
    definition = tool_definition()

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [definition]

    @server.call_tool(validate_input=True)
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != "law_support":
            raise ValueError(f"unknown DeepLaw tool: {name}")
        runtime = server.request_context.lifespan_context
        with runtime.lock:
            return _execute_support(
                runtime.law,
                operation=cast(Operation, arguments.get("operation", "search")),
                query=str(arguments.get("query", "")),
                purpose=cast(Purpose, arguments.get("purpose", "auto")),
                as_of=cast(str | None, arguments.get("as_of")),
                limit=int(arguments.get("limit", 5)),
                max_chars=int(arguments.get("max_chars", 3500)),
                document_types=cast(list[str] | None, arguments.get("document_types")),
                segment_id=cast(str | None, arguments.get("segment_id")),
                receipt_id=cast(str | None, arguments.get("receipt_id")),
            )

    return server


def run_mcp(*, transport: str = "stdio") -> None:
    if transport != "stdio":
        raise ValueError("DeepLaw 0.2 supports only the local stdio MCP transport")

    async def serve() -> None:
        server = create_mcp_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(serve)

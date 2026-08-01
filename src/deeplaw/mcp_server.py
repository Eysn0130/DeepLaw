from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
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
from .knowledge_autonomy import AutonomousKnowledgeStore, autonomous_core_installed
from .knowledge_store import default_knowledge_vault
from .models import Purpose, SearchRequest
from .official import active_official_release_id
from .private_library import active_private_release_id, resolve_private_database
from .search import DeepLaw
from .util import (
    assert_provider_output_safe,
    canonical_json,
    provider_safe_exception,
    sha256_bytes,
)

Operation = Literal[
    "search",
    "get",
    "verify",
    "release_info",
    "private_search",
    "private_get",
    "private_verify",
    "private_info",
    "federated_context",
    "capabilities",
    "challenge_trace",
    "challenge_get",
    "challenge_replay",
]

_DESCRIPTION = (
    "Read-only Chinese-law research gateway. Call only after an explicit Chinese-law "
    "research request; never call for ordinary code, data, document, or analytics work, "
    "and never activate from a lone legal-looking keyword. Operations: search bounded "
    "official evidence, get an exact segment, verify a receipt, or inspect the active immutable "
    "release. Explicit private_* operations search a separate local user-private legal-reference "
    "library; they never blend its ranking or authority with the official catalog. "
    "federated_context compiles explicitly partitioned official, private, and optionally "
    "Agent-derived context without treating ranking as authority. capabilities and the "
    "challenge_* operations expose deterministic evidence predicates and replayable traces."
)
_INSTRUCTIONS = (
    "Read-only, version-aware Chinese legal research. Use only for explicit legal questions. "
    "Search returns at most five evidence cards; fetch full text only by selected segment_id. "
    "Never treat retrieval as proof of case facts or applicability. Private results are "
    "user-provided and never official DeepLaw sources. federated_context requires an explicit "
    "no-case-data confirmation and never performs legal adjudication."
)
_OUTPUT_CONTRACTS = {
    "search": "law-search-response.v2.schema.json",
    "segment": "law-segment.v2.schema.json",
    "verification": "law-verification.v1.schema.json",
    "release_info": "law-release-info.v3.schema.json",
    "evidence": "legal-evidence-card.v2.schema.json",
    "release_manifest": "corpus-release-manifest.v3.schema.json",
    "legacy_release_info": "law-release-info.v2.schema.json",
    "legacy_release_manifest": "corpus-release-manifest.v2.schema.json",
    "federated_context": "law-federated-context.v1.schema.json",
    "legacy_output": "law-support.output.v3.schema.json",
    "evidence_capabilities": "evidence-capabilities.v1.schema.json",
    "segment_capabilities": "segment-evidence-capabilities.v1.schema.json",
    "challenge_trace": "authoritative-challenge-trace.v1.schema.json",
    "challenge_replay": "authoritative-challenge-replay.v1.schema.json",
}
_INPUT_CONTRACTS = {
    "legacy_input": "law-support.input.v3.schema.json",
    "evidence_capabilities": "evidence-capabilities.v1.schema.json",
    "challenge_trace": "authoritative-challenge-trace.v1.schema.json",
}


@dataclass(frozen=True)
class _RuntimeContext:
    official: DeepLaw | None
    private: DeepLaw | None
    guard_official_epoch: bool
    guard_private_epoch: bool
    knowledge_vault: Path | None
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


def _rebase_local_refs(value: Any, *, base: str) -> Any:
    """Rebase document-local JSON pointers before nesting a schema in $defs."""

    if isinstance(value, list):
        return [_rebase_local_refs(item, base=base) for item in value]
    if not isinstance(value, dict):
        return value
    rebased = {
        key: _rebase_local_refs(item, base=base) for key, item in value.items()
    }
    reference = rebased.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        rebased["$ref"] = f"{base}{reference[1:]}"
    return rebased


@lru_cache(maxsize=1)
def bundled_output_schema() -> dict[str, Any]:
    schema = deepcopy(_load_contract("law-support.output.v4.schema.json"))
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
        definition = _rebase_local_refs(definition, base=f"#/$defs/{name}")
        definitions[name] = _rewrite_refs(definition, references)
    schema.pop("$id", None)
    schema["$defs"] = definitions
    return _rewrite_refs(schema, references)


@lru_cache(maxsize=1)
def bundled_input_schema() -> dict[str, Any]:
    schema = deepcopy(_load_contract("law-support.input.v4.schema.json"))
    references: dict[str, str] = {}
    for name, filename in _INPUT_CONTRACTS.items():
        target = f"#/$defs/{name}"
        references[filename] = target
        schema_id = _load_contract(filename).get("$id")
        if isinstance(schema_id, str):
            references[schema_id] = target
    definitions: dict[str, Any] = {}
    for name, filename in _INPUT_CONTRACTS.items():
        definition = deepcopy(_load_contract(filename))
        definition.pop("$schema", None)
        definition.pop("$id", None)
        definition = _rebase_local_refs(definition, base=f"#/$defs/{name}")
        definitions[name] = _rewrite_refs(definition, references)
    legacy_branches = definitions["legacy_input"]["oneOf"]
    schema["oneOf"] = [*legacy_branches, *schema["oneOf"][1:]]
    schema["$defs"] = definitions
    return _rewrite_refs(schema, references)


def tool_definition() -> types.Tool:
    return types.Tool(
        name="law_support",
        description=_DESCRIPTION,
        inputSchema=deepcopy(bundled_input_schema()),
        outputSchema=deepcopy(bundled_output_schema()),
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )


def _execute_law_operation(
    law: DeepLaw,
    *,
    operation: Literal["search", "get", "verify", "release_info"],
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


def _legal_partition(
    *,
    name: str,
    origin: str,
    authority: str,
    legal_authority: bool,
    status: str,
    results: list[dict[str, Any]],
    receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "name": name,
        "origin": origin,
        "authority": authority,
        "legal_authority": legal_authority,
        "status": status,
        "results": results[:5],
        "selected_count": min(5, len(results)),
        "receipt": receipt,
        "ranking_is_authority": False,
    }


def _execute_federated_context(
    runtime: _RuntimeContext,
    *,
    query: str,
    purpose: Purpose,
    as_of: str | None,
    limit: int,
    max_chars: int,
    document_types: list[str] | None,
    include_private: bool,
    include_agent_interpretation: bool,
    confirm_no_case_data: bool,
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "federated legal context requires confirmation that no case data is present"
        )
    gaps: list[str] = []

    def search_partition(law: DeepLaw | None, *, label: str) -> dict[str, Any] | None:
        if law is None:
            gaps.append(f"{label} legal release is unavailable")
            return None
        return _execute_law_operation(
            law,
            operation="search",
            query=query,
            purpose=purpose,
            as_of=as_of,
            limit=min(limit, 5),
            max_chars=max_chars,
            document_types=document_types,
        )

    official_search = search_partition(runtime.official, label="official")
    official_results = official_search.get("evidence", []) if official_search else []
    official = _legal_partition(
        name="official",
        origin="official",
        authority="official",
        legal_authority=True,
        status=(
            "unavailable"
            if official_search is None
            else "available"
            if official_results
            else "empty"
        ),
        results=cast(list[dict[str, Any]], official_results),
        receipt=(
            {
                "release_id": official_search["release_id"],
                "query_plan": official_search["query_plan"],
                "gaps": official_search["gaps"],
            }
            if official_search is not None
            else None
        ),
    )
    if not official_results and official_search is not None:
        gaps.append("official search returned no admitted evidence; no fallback was relabeled")

    private_search = (
        search_partition(runtime.private, label="user-private") if include_private else None
    )
    private_results = private_search.get("evidence", []) if private_search else []
    user_private = _legal_partition(
        name="user_private",
        origin="user_source",
        authority="user_provided",
        legal_authority=False,
        status=(
            "disabled"
            if not include_private
            else "unavailable"
            if private_search is None
            else "available"
            if private_results
            else "empty"
        ),
        results=cast(list[dict[str, Any]], private_results),
        receipt=(
            {
                "release_id": private_search["release_id"],
                "query_plan": private_search["query_plan"],
                "gaps": private_search["gaps"],
            }
            if private_search is not None
            else None
        ),
    )

    agent_results: list[dict[str, Any]] = []
    agent_receipt: dict[str, Any] | None = None
    agent_status = "disabled"
    if include_agent_interpretation:
        if runtime.knowledge_vault is None:
            agent_status = "unavailable"
            gaps.append("Agent legal interpretation Vault is not explicitly enabled")
        else:
            with AutonomousKnowledgeStore(runtime.knowledge_vault, read_only=True) as store:
                verification = store.verify()
                if not verification["valid"]:
                    raise RuntimeError(
                        "Agent legal interpretation Vault failed integrity verification"
                    )
                recalled = store.recall(
                    query,
                    scope=cast(Any, store.vault_scope),
                    max_sensitivity="private",
                    limit=min(limit, 5),
                    max_chars=max_chars,
                    max_tokens=4_000,
                    max_sources=5,
                    graph_hops=1,
                    retrieval_mode="hybrid",
                    kinds=("claim", "concept", "synthesis", "comparison"),
                    required_tags=("legal_interpretation",),
                    force_canonical_lexical=not verification["derived_ready"],
                )
                agent_results = [
                    {
                        **item,
                        "origin": "agent_derived",
                        "authority": "agent_derived",
                        "legal_authority": False,
                    }
                    for item in recalled["results"]
                ][:5]
                agent_receipt = {
                    "query_plan": recalled["query_plan"],
                    "query_plan_sha256": recalled["query_plan_sha256"],
                    "audit_head": recalled["audit_head"],
                    "gaps": recalled["gaps"],
                }
                agent_status = "available" if agent_results else "empty"
                if not agent_results:
                    gaps.append("no admitted Agent legal interpretation matched")
    agent_interpretation = _legal_partition(
        name="agent_interpretation",
        origin="agent_derived",
        authority="agent_derived",
        legal_authority=False,
        status=agent_status,
        results=agent_results,
        receipt=agent_receipt,
    )
    result = {
        "schema_version": "deeplaw.law-federated-context/v1",
        "query": query,
        "purpose": purpose,
        "as_of": as_of,
        "official": official,
        "user_private": user_private,
        "agent_interpretation": agent_interpretation,
        "gaps": gaps[:12],
        "budget": {
            "official_limit": min(limit, 5),
            "private_limit": min(limit, 5) if include_private else 0,
            "agent_limit": min(limit, 5) if include_agent_interpretation else 0,
            "selected_items": len(official["results"])
            + len(user_private["results"])
            + len(agent_interpretation["results"]),
            "max_characters": max_chars
            * (1 + int(include_private) + int(include_agent_interpretation)),
        },
        "authority_partitions_preserved": True,
        "legal_adjudication": False,
    }
    result["context_digest"] = sha256_bytes(canonical_json(result).encode("utf-8"))
    return result


def _guard_runtime_epoch(runtime: _RuntimeContext, *, private: bool) -> None:
    law = runtime.private if private else runtime.official
    if law is None:
        return
    if private:
        if runtime.guard_private_epoch:
            current_release_id = active_private_release_id()
            if current_release_id != law.release_id:
                raise RuntimeError(
                    "user-private library changed; restart the DeepLaw MCP process "
                    "before reading it"
                )
        elif not law.database.is_file():
            raise RuntimeError(
                "user-private snapshot was removed; restart the DeepLaw MCP process "
                "before reading it"
            )
        return
    if runtime.guard_official_epoch:
        current_release_id = active_official_release_id()
        if current_release_id != law.release_id:
            raise RuntimeError(
                "official library changed or was disabled; restart the DeepLaw MCP "
                "process before reading it"
            )


def _execute_support(
    runtime: _RuntimeContext,
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
    include_private: bool = True,
    include_agent_interpretation: bool = False,
    confirm_no_case_data: bool = False,
    trace_id: str | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if operation == "federated_context":
        _guard_runtime_epoch(runtime, private=False)
        if include_private:
            _guard_runtime_epoch(runtime, private=True)
        result = _execute_federated_context(
            runtime,
            query=query,
            purpose=purpose,
            as_of=as_of,
            limit=limit,
            max_chars=max_chars,
            document_types=document_types,
            include_private=include_private,
            include_agent_interpretation=include_agent_interpretation,
            confirm_no_case_data=confirm_no_case_data,
        )
        assert_provider_output_safe(result, interface="law_support")
        return result
    private_operation = operation.startswith("private_")
    law = runtime.private if private_operation else runtime.official
    if law is None:
        scope = "user-private" if private_operation else "official"
        raise FileNotFoundError(f"DeepLaw has no active {scope} release")
    _guard_runtime_epoch(runtime, private=private_operation)
    if operation == "capabilities":
        if not segment_id:
            raise ValueError("segment_id is required for operation=capabilities")
        result = law.evidence_capabilities(segment_id, as_of=as_of)
        assert_provider_output_safe(result, interface="law_support")
        return result
    if operation == "challenge_trace":
        result = law.challenge_trace(
            SearchRequest(
                query=query,
                purpose=purpose,
                as_of=as_of,
                limit=limit,
                max_chars=max_chars,
                document_types=tuple(document_types or ()),
            )
        )
        assert_provider_output_safe(result, interface="law_support")
        return result
    if operation == "challenge_get":
        if not trace_id:
            raise ValueError("trace_id is required for operation=challenge_get")
        result = law.get_challenge_trace(trace_id)
        assert_provider_output_safe(result, interface="law_support")
        return result
    if operation == "challenge_replay":
        if not isinstance(trace, dict):
            raise ValueError("trace is required for operation=challenge_replay")
        result = law.replay_challenge_trace(trace)
        assert_provider_output_safe(result, interface="law_support")
        return result
    normalized_operation = operation.removeprefix("private_")
    if normalized_operation == "info":
        normalized_operation = "release_info"
    result = _execute_law_operation(
        law,
        operation=cast(
            Literal["search", "get", "verify", "release_info"], normalized_operation
        ),
        query=query,
        purpose=purpose,
        as_of=as_of,
        limit=limit,
        max_chars=max_chars,
        document_types=document_types,
        segment_id=segment_id,
        receipt_id=receipt_id,
    )
    assert_provider_output_safe(result, interface="law_support")
    return result


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
    private_database: str | Path | None = None,
    knowledge_vault: str | Path | None = None,
    include_private: bool = True,
    include_agent_interpretation: bool = False,
    confirm_no_case_data: bool = False,
    trace_id: str | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one read-only DeepLaw operation outside the MCP transport."""

    if operation == "federated_context":
        if not confirm_no_case_data:
            raise ValueError(
                "federated legal context requires confirmation that no case data is present"
            )
        with ExitStack() as stack:
            official: DeepLaw | None = None
            private: DeepLaw | None = None
            try:
                official = stack.enter_context(
                    DeepLaw(database, expected_scope="official")
                )
            except FileNotFoundError:
                if database is not None:
                    raise
            if include_private:
                selected_private = private_database
                if selected_private is None:
                    selected_private = resolve_private_database()
                try:
                    private = stack.enter_context(
                        DeepLaw(selected_private, expected_scope="user_private")
                    )
                except FileNotFoundError:
                    if private_database is not None:
                        raise
            selected_vault = Path(knowledge_vault) if knowledge_vault is not None else None
            if selected_vault is not None and not autonomous_core_installed(selected_vault):
                raise FileNotFoundError(
                    "DeepLaw Agent Knowledge Vault is not initialized"
                )
            runtime = _RuntimeContext(
                official=official,
                private=private,
                guard_official_epoch=False,
                guard_private_epoch=False,
                knowledge_vault=selected_vault,
                lock=RLock(),
            )
            return _execute_support(
                runtime,
                operation=operation,
                query=query,
                purpose=purpose,
                as_of=as_of,
                limit=limit,
                max_chars=max_chars,
                document_types=document_types,
                include_private=include_private,
                include_agent_interpretation=include_agent_interpretation,
                confirm_no_case_data=confirm_no_case_data,
            )

    private_operation = operation.startswith("private_")
    selected_database = private_database if private_operation else database
    if private_operation and selected_database is None:
        selected_database = resolve_private_database()
    expected_scope: Literal["official", "user_private"] = (
        "user_private" if private_operation else "official"
    )
    with DeepLaw(selected_database, expected_scope=expected_scope) as law:
        runtime = _RuntimeContext(
            official=None if private_operation else law,
            private=law if private_operation else None,
            guard_official_epoch=False,
            guard_private_epoch=False,
            knowledge_vault=None,
            lock=RLock(),
        )
        return _execute_support(
            runtime,
            operation=operation,
            query=query,
            purpose=purpose,
            as_of=as_of,
            limit=limit,
            max_chars=max_chars,
            document_types=document_types,
            segment_id=segment_id,
            receipt_id=receipt_id,
            trace_id=trace_id,
            trace=trace,
        )


def create_mcp_server() -> Server[_RuntimeContext]:
    @asynccontextmanager
    async def lifespan(_: Server[_RuntimeContext]) -> AsyncIterator[_RuntimeContext]:
        official: DeepLaw | None = None
        private: DeepLaw | None = None
        official_explicit = os.environ.get("DEEPLAW_DB") is not None
        private_explicit = os.environ.get("DEEPLAW_PRIVATE_DB") is not None
        knowledge_vault: Path | None = None
        try:
            try:
                active_release_id = (
                    None if official_explicit else active_official_release_id()
                )
                official = DeepLaw(expected_scope="official")
                official.signed_catalog_verified = bool(
                    active_release_id is not None
                    and active_release_id == official.release_id
                )
            except FileNotFoundError:
                if os.environ.get("DEEPLAW_DB") is not None:
                    raise
            try:
                private_database = resolve_private_database()
                private = DeepLaw(private_database, expected_scope="user_private")
            except FileNotFoundError:
                if private_explicit:
                    raise
            if os.environ.get("DEEPLAW_LAW_FEDERATED_KNOWLEDGE") == "1":
                configured_vault = os.environ.get("DEEPLAW_KNOWLEDGE_VAULT")
                knowledge_vault = (
                    Path(configured_vault).expanduser()
                    if configured_vault
                    else default_knowledge_vault()
                )
                if not autonomous_core_installed(knowledge_vault):
                    raise FileNotFoundError(
                        "DEEPLAW_LAW_FEDERATED_KNOWLEDGE=1 requires an initialized "
                        "DeepLaw Agent Knowledge Vault"
                    )
            yield _RuntimeContext(
                official=official,
                private=private,
                guard_official_epoch=(
                    official is not None
                    and not official_explicit
                    and active_official_release_id() == official.release_id
                ),
                guard_private_epoch=private is not None and not private_explicit,
                knowledge_vault=knowledge_vault,
                lock=RLock(),
            )
        finally:
            if private is not None:
                private.close()
            if official is not None:
                official.close()

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
            raise ValueError("unknown DeepLaw tool")
        runtime = server.request_context.lifespan_context
        with runtime.lock:
            try:
                return _execute_support(
                    runtime,
                    operation=cast(Operation, arguments.get("operation", "search")),
                    query=str(arguments.get("query", "")),
                    purpose=cast(Purpose, arguments.get("purpose", "auto")),
                    as_of=cast(str | None, arguments.get("as_of")),
                    limit=int(arguments.get("limit", 5)),
                    max_chars=int(arguments.get("max_chars", 3500)),
                    document_types=cast(list[str] | None, arguments.get("document_types")),
                    segment_id=cast(str | None, arguments.get("segment_id")),
                    receipt_id=cast(str | None, arguments.get("receipt_id")),
                    include_private=bool(arguments.get("include_private", True)),
                    include_agent_interpretation=bool(
                        arguments.get("include_agent_interpretation", False)
                    ),
                    confirm_no_case_data=bool(
                        arguments.get("confirm_no_case_data", False)
                    ),
                    trace_id=cast(str | None, arguments.get("trace_id")),
                    trace=cast(dict[str, Any] | None, arguments.get("trace")),
                )
            except Exception as error:
                raise provider_safe_exception(error, interface="law_support") from None

    return server


def run_mcp(*, transport: str = "stdio") -> None:
    if transport != "stdio":
        raise ValueError("DeepLaw supports only the local stdio MCP transport")

    async def serve() -> None:
        server = create_mcp_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(serve)

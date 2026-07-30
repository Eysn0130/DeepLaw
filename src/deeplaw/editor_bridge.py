from __future__ import annotations

from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from .knowledge_autonomy import AutonomousKnowledgeStore
from .retrieval import PurposeAwareRetrievalService
from .util import canonical_json, strict_json_loads

EditorFrontend = Literal["obsidian", "tolaria"]

_WRITABLE_ROOTS = {
    "obsidian": ("drafts", "notes", "sources/inbox"),
    "tolaria": ("drafts", "notes"),
}
_READONLY_ROOTS = (
    ".deeplaw",
    "canvas",
    "knowledge",
    "memory",
    "sources",
    "wiki",
)


def _contract_path(name: str) -> Path:
    packaged = Path(__file__).resolve().parent / "contracts" / name
    if packaged.is_file():
        return packaged
    repository = Path(__file__).resolve().parents[2] / "contracts" / name
    if repository.is_file():
        return repository
    raise RuntimeError(f"DeepLaw editor bridge contract is missing: {name}")


@cache
def _validator(name: str) -> Draft202012Validator:
    schema = strict_json_loads(_contract_path(name).read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_editor_context(envelope: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise TypeError("Editor Context Envelope must be an object")
    error = next(
        _validator("editor-context-envelope.v1.schema.json").iter_errors(envelope),
        None,
    )
    if error is not None:
        raise ValueError(f"Editor Context Envelope is invalid: {error.message}")
    selected = strict_json_loads(canonical_json(envelope))
    if not isinstance(selected, dict):
        raise RuntimeError("Editor Context Envelope canonicalization failed")
    selected_text = selected["selected_text"]
    if selected_text is not None and len(selected_text) > selected["budgets"][
        "max_selected_characters"
    ]:
        raise ValueError("Editor selection exceeds its declared budget")
    selection_range = selected["selection_range"]
    if selected_text is not None and (
        selection_range["start"] > selection_range["end"]
        or selection_range["end"] - selection_range["start"] != len(selected_text)
    ):
        raise ValueError("Editor selection range does not bind the selected text")
    return selected


def bridge_contract(frontend: EditorFrontend) -> dict[str, Any]:
    if frontend == "obsidian":
        value = {
            "schema_version": "deeplaw.obsidian-bridge/v1",
            "frontend": "obsidian",
            "context_contract": "deeplaw.editor-context-envelope/v1",
            "event_activation": "workspace.onLayoutReady",
            "writable_roots": ["drafts", "notes", "sources/inbox"],
            "canonical_roots": ["knowledge", "memory"],
            "derived_roots": ["canvas", "wiki"],
            "forbidden_metadata": [
                "authority",
                "deeplaw_id",
                "ledger_event",
                "revision_id",
                "scope",
                "sensitivity",
                "source_refs",
            ],
            "deep_law_owns_canonical_mutation": True,
            "ephemeral_context_default": True,
        }
        contract = "obsidian-bridge.v1.schema.json"
    elif frontend == "tolaria":
        value = {
            "schema_version": "deeplaw.tolaria-bridge/v1",
            "frontend": "tolaria",
            "context_contract": "deeplaw.editor-context-envelope/v1",
            "writable_roots": ["drafts", "notes"],
            "readonly_roots": [
                ".deeplaw",
                "canvas",
                "knowledge",
                "memory",
                "sources",
                "wiki",
            ],
            "recommended_mcp_processes": [
                "tolaria",
                "knowledge_support",
                "knowledge_sink",
                "law_support",
            ],
            "deep_law_owns_canonical_mutation": True,
            "ephemeral_context_default": True,
        }
        contract = "tolaria-bridge.v1.schema.json"
    else:
        raise ValueError("editor frontend is invalid")
    error = next(_validator(contract).iter_errors(value), None)
    if error is not None:
        raise RuntimeError(f"Editor Bridge Contract is invalid: {error.message}")
    return value


def validate_editor_write_target(
    frontend: EditorFrontend,
    relative_path: str,
) -> str:
    if frontend not in _WRITABLE_ROOTS:
        raise ValueError("editor frontend is invalid")
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
    ):
        raise ValueError("editor write target is invalid")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ValueError("editor write target must be a canonical relative path")
    normalized = path.as_posix()
    if any(
        normalized == root or normalized.startswith(f"{root}/")
        for root in _WRITABLE_ROOTS[frontend]
    ):
        return normalized
    if any(
        normalized == root or normalized.startswith(f"{root}/")
        for root in _READONLY_ROOTS
    ):
        raise PermissionError("editor cannot write DeepLaw canonical or derived roots")
    raise PermissionError("editor write target is outside its writable roots")


def context_for_editor(
    vault_path: str | Path,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    selected = validate_editor_context(envelope)
    with AutonomousKnowledgeStore(vault_path, read_only=True) as store:
        if selected["vault_identity"] != store.vault_id:
            raise PermissionError("Editor Context Envelope targets another Vault identity")
    query_parts = [selected["user_intent"]]
    if selected["selected_text"]:
        query_parts.append(selected["selected_text"])
    query_parts.extend(selected["explicit_note_references"][:8])
    query = "\n".join(query_parts)
    retrieval = PurposeAwareRetrievalService(vault_path).query(
        query,
        purpose="answer",
        scope=selected["scope"],
        max_sensitivity=selected["max_sensitivity"],
        limit=min(selected["budgets"]["max_notes"], 20),
        max_chars=selected["budgets"]["max_context_characters"],
    )
    result = {
        "schema_version": "deeplaw.editor-context-result/v1",
        "frontend": selected["frontend"],
        "active_note": selected["active_note"],
        "selection_included": selected["selected_text"] is not None,
        "ephemeral_context": True,
        "persistence_requested": selected["persistence_allowed"],
        "persistence_performed": False,
        "retrieval": retrieval,
    }
    error = next(
        _validator("editor-context-result.v1.schema.json").iter_errors(result),
        None,
    )
    if error is not None:
        raise RuntimeError(f"Editor Context Result is invalid: {error.message}")
    return result

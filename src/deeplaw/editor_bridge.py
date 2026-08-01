from __future__ import annotations

from functools import cache
from hashlib import sha256
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


def tolaria_mcp_servers(
    *,
    deeplaw_executable: str,
    vault_path: str | Path,
    compiler_grant_id: str | None = None,
    include_law_support: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return standard-MCP entries without mutating a host configuration."""
    if not isinstance(deeplaw_executable, str) or not deeplaw_executable:
        raise ValueError("DeepLaw executable must not contain arguments")
    executable_path = Path(deeplaw_executable)
    if "\x00" in deeplaw_executable or (
        any(character.isspace() for character in deeplaw_executable)
        and not executable_path.is_absolute()
    ):
        raise ValueError("DeepLaw executable must not contain arguments")
    root = Path(vault_path).expanduser().absolute()
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("DeepLaw Vault must be an absolute non-symlink path")
    root_text = str(root)
    servers: dict[str, dict[str, Any]] = {
        "deeplaw_knowledge": {
            "command": deeplaw_executable,
            "args": ["knowledge", "mcp", "--vault", root_text, "--stdio"],
        }
    }
    if compiler_grant_id is not None:
        if (
            not isinstance(compiler_grant_id, str)
            or not compiler_grant_id
            or len(compiler_grant_id) > 200
            or "\x00" in compiler_grant_id
        ):
            raise ValueError("compiler grant identity is invalid")
        servers["deeplaw_knowledge_sink"] = {
            "command": deeplaw_executable,
            "args": [
                "knowledge",
                "sink",
                "mcp",
                "--vault",
                root_text,
                "--grant-id",
                compiler_grant_id,
                "--stdio",
            ],
        }
    if include_law_support:
        servers["deeplaw_law"] = {
            "command": deeplaw_executable,
            "args": ["mcp", "--stdio"],
        }
    return servers


def merge_standard_mcp_config(
    existing: dict[str, Any],
    servers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge bounded DeepLaw entries while preserving every unrelated setting."""
    if not isinstance(existing, dict) or not isinstance(servers, dict):
        raise TypeError("MCP configuration and entries must be objects")
    selected = strict_json_loads(canonical_json(existing))
    current = selected.get("mcpServers")
    if current is None:
        current = {}
        selected["mcpServers"] = current
    if not isinstance(current, dict):
        raise ValueError("mcpServers must be an object")
    for name, entry in servers.items():
        if not isinstance(name, str) or not name.startswith("deeplaw_"):
            raise ValueError("only namespaced DeepLaw MCP entries may be merged")
        if not isinstance(entry, dict):
            raise ValueError("DeepLaw MCP entry must be an object")
        if name in current and current[name] != entry:
            raise FileExistsError(
                f"MCP entry already exists with different settings: {name}"
            )
        current[name] = strict_json_loads(canonical_json(entry))
    return selected


def tolaria_context_envelope(
    snapshot: dict[str, Any],
    *,
    vault_identity: str,
    user_intent: str,
    frontend_version: str,
    scope: str = "project",
    max_sensitivity: str = "private",
) -> dict[str, Any]:
    """Map the documented Tolaria context snapshot to DeepLaw's closed envelope."""
    if not isinstance(snapshot, dict):
        raise TypeError("Tolaria context snapshot must be an object")
    active = snapshot.get("activeNote")
    if not isinstance(active, dict):
        raise ValueError("Tolaria context snapshot requires activeNote")
    active_path = active.get("path")
    active_body = active.get("body")
    if not isinstance(active_path, str) or not active_path or len(active_path) > 500:
        raise ValueError("Tolaria active note path is invalid")
    if not isinstance(active_body, str) or len(active_body) > 48_000:
        raise ValueError("Tolaria active note body is invalid or exceeds its bound")

    def note_paths(field: str, *, maximum: int) -> list[str]:
        value = snapshot.get(field, [])
        if not isinstance(value, list) or len(value) > maximum:
            raise ValueError(f"Tolaria {field} is invalid or exceeds its bound")
        paths: list[str] = []
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError(f"Tolaria {field} entry is invalid")
            path = item["path"]
            if not path or len(path) > 500:
                raise ValueError(f"Tolaria {field} path is invalid")
            if path not in paths and path != active_path:
                paths.append(path)
        return paths

    open_tabs = note_paths("openTabs", maximum=32)
    references = note_paths("referencedNotes", maximum=32)
    envelope = {
        "schema_version": "deeplaw.editor-context-envelope/v1",
        "frontend": "tolaria",
        "frontend_version": frontend_version,
        "vault_identity": vault_identity,
        "active_note": {
            "note_id": active_path,
            "content_sha256": sha256(active_body.encode("utf-8")).hexdigest(),
        },
        "selected_text": None,
        "selection_range": None,
        "open_tabs": open_tabs,
        "explicit_note_references": references,
        "backlinks": [],
        "outlinks": [],
        "active_canvas": None,
        "active_bases_view": None,
        "user_intent": user_intent,
        "persistence_allowed": False,
        "scope": scope,
        "max_sensitivity": max_sensitivity,
        "budgets": {
            "max_notes": 8,
            "max_context_characters": 8_000,
            "max_selected_characters": 0,
            "max_provider_characters": 65_536,
        },
        "confirm_no_case_data": True,
    }
    return validate_editor_context(envelope)


def tolaria_open_note_request(
    relative_path: str,
    *,
    vault_path: str | Path,
) -> dict[str, Any]:
    """Build a Tolaria MCP UI intent; it performs no filesystem write itself."""
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise ValueError("Tolaria open-note target is invalid")
    target = PurePosixPath(relative_path)
    if target.is_absolute() or "." in target.parts or ".." in target.parts:
        raise ValueError("Tolaria open-note target must be a canonical relative path")
    canonical = target.as_posix()
    allowed_roots = ("drafts", "notes", "knowledge", "memory", "wiki")
    if not any(
        canonical == root or canonical.startswith(f"{root}/")
        for root in allowed_roots
    ):
        raise PermissionError("Tolaria open-note target is outside the display roots")
    root = Path(vault_path).expanduser().absolute()
    return {
        "tool": "open_note",
        "arguments": {"path": canonical, "vaultPath": str(root)},
        "mutation": "ui_only",
    }

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import cache
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from .agent_context import (
    build_agent_context,
    validate_agent_context,
)
from .knowledge_autonomy import AutonomousKnowledgeStore
from .retrieval import PurposeAwareRetrievalService
from .util import canonical_json, strict_json_loads

EditorFrontend = Literal["obsidian", "tolaria"]
HostFrontend = Literal["obsidian", "opencode", "tolaria"]

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


def bridge_contract(frontend: EditorFrontend | HostFrontend) -> dict[str, Any]:
    # OpenCode has no legacy editor manifest.  Route its explicit host binding
    # to the closed v1 contract while preserving the Obsidian/Tolaria manifest
    # shape used by existing editor integrations.
    if frontend == "opencode":
        return host_bridge_contract("opencode")
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
        # The legacy editor result contract is v1.  Keep this compatibility
        # path on the legacy retrieval projection; host-neutral callers use
        # ``host_context_envelope`` and may request Query Plan v6 explicitly.
        query_plan_version="4",
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


# The old Editor Context Envelope remains the compatibility surface for Obsidian
# and Tolaria.  Host adapters that need a provider-visible context must use the
# host-neutral Agent Context Envelope below instead.  In particular, this layer
# intentionally does not map active-note bodies or chat summaries to selected
# text: only an explicit host selection is eligible for provider delivery.
_HOST_SUMMARY_KEYS = frozenset(
    {
        "chat_summary",
        "chatSummary",
        "conversation_summary",
        "conversationSummary",
        "summary",
        "transcript",
        "messages",
        "conversation",
        "chat",
    }
)
_HOST_PATH_KEYS = {
    "obsidian": {
        "active_files": ("active_files", "activeFiles"),
        "open_tabs": ("open_tabs", "openTabs"),
        "current_note": ("current_note", "currentNote"),
    },
    "opencode": {
        "active_files": ("active_files", "activeFiles"),
        "open_tabs": ("open_tabs", "openTabs"),
        "current_note": ("current_note", "currentNote"),
    },
    "tolaria": {
        "active_files": ("active_files", "activeFiles"),
        "open_tabs": ("open_tabs", "openTabs"),
        "current_note": ("current_note", "currentNote"),
    },
}


def _host_value(
    snapshot: Mapping[str, Any],
    keys: Iterable[str],
    *,
    default: Any = None,
) -> Any:
    for key in keys:
        if key in snapshot:
            return snapshot[key]
    return default


def _host_optional_value(
    explicit: Any,
    snapshot: Mapping[str, Any],
    keys: Iterable[str],
    *,
    default: Any = None,
) -> Any:
    if explicit is not None:
        return explicit
    return _host_value(snapshot, keys, default=default)


def _host_path(value: Any, *, field: str) -> str:
    if isinstance(value, str):
        path = value
    elif isinstance(value, Mapping):
        path = value.get("path", value.get("relative_path"))
    else:
        path = None
    if not isinstance(path, str) or not path:
        raise ValueError(f"{field} contains an invalid relative path")
    return path


def _host_paths(
    value: Any,
    *,
    field: str,
    maximum: int,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} is invalid or exceeds its bound")
    paths = [_host_path(item, field=f"{field}[{index}]") for index, item in enumerate(value)]
    return list(dict.fromkeys(paths))


def _host_selected_text(snapshot: Mapping[str, Any]) -> str | None:
    value = _host_value(snapshot, ("selected_text", "selectedText"))
    if value is None:
        selection = snapshot.get("selection")
        if isinstance(selection, Mapping):
            value = selection.get("text")
    if value is not None and not isinstance(value, str):
        raise ValueError("host selection text is invalid")
    return value


def _host_tool_digests(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = _host_value(snapshot, ("tool_result_digests", "toolResultDigests"), default=[])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tool_result_digests must be an array")
    return value


def _reject_host_summary(snapshot: Mapping[str, Any]) -> None:
    for key in _HOST_SUMMARY_KEYS:
        if key in snapshot and snapshot[key] not in (None, "", [], {}):
            raise ValueError(
                "chat or conversation summaries are not accepted as Agent Context"
            )


def host_context_envelope(
    host: HostFrontend,
    snapshot: Mapping[str, Any],
    *,
    workspace_identity: str | None = None,
    repository_identity: str | None = None,
    task: str | None = None,
    goal: str | None = None,
    commit: str | None = None,
    branch: str | None = None,
    requested_purpose: str | None = None,
    scope: str | None = None,
    max_sensitivity: str | None = None,
    budget: Mapping[str, Any] | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    """Build one host-neutral Agent Context Envelope from a host snapshot.

    The returned object is exactly ``deeplaw.agent-context-envelope/v1``.  The
    host name is deliberately not copied into it, so equivalent OpenCode and
    Tolaria snapshots produce the same canonical payload and hash.  Host
    bindings and upstream capability are reported separately by
    :func:`host_bridge_contract`.
    """

    if host not in {"obsidian", "opencode", "tolaria"}:
        raise ValueError("host must be obsidian, opencode or tolaria")
    if not isinstance(snapshot, Mapping):
        raise TypeError("host context snapshot must be an object")
    _reject_host_summary(snapshot)

    host_keys = _HOST_PATH_KEYS[host]
    active_files = _host_paths(
        _host_value(snapshot, host_keys["active_files"], default=[]),
        field="active_files",
        maximum=64,
    )
    open_tabs = _host_paths(
        _host_value(snapshot, host_keys["open_tabs"], default=[]),
        field="open_tabs",
        maximum=32,
    )

    current_note_value = _host_optional_value(
        None,
        snapshot,
        host_keys["current_note"],
    )
    if host == "tolaria":
        active_note = snapshot.get("activeNote")
        if isinstance(active_note, Mapping):
            current_note_value = active_note.get("path", current_note_value)
        elif isinstance(active_note, str):
            current_note_value = active_note
        # Tolaria's referenced notes are paths only.  They are useful active
        # files, but their bodies are intentionally not read or copied.
        active_files.extend(
            _host_paths(
                snapshot.get("referencedNotes", []),
                field="referencedNotes",
                maximum=32,
            )
        )

    if current_note_value is not None:
        current_note = _host_path(current_note_value, field="current_note")
        active_files.append(current_note)
    else:
        current_note = None
    active_files = list(dict.fromkeys(active_files))

    selected_task = _host_optional_value(
        task,
        snapshot,
        ("task", "user_intent", "userIntent"),
    )
    if not isinstance(selected_task, str) or not selected_task:
        raise ValueError("host context requires a bounded task")
    selected_goal = _host_optional_value(
        goal,
        snapshot,
        ("goal",),
    )
    selected_workspace = _host_optional_value(
        workspace_identity,
        snapshot,
        ("workspace_identity", "workspaceIdentity"),
    )
    selected_repository = _host_optional_value(
        repository_identity,
        snapshot,
        ("repository_identity", "repositoryIdentity"),
    )
    selected_commit = _host_optional_value(commit, snapshot, ("commit",))
    selected_branch = _host_optional_value(branch, snapshot, ("branch",))
    selected_purpose = _host_optional_value(
        requested_purpose,
        snapshot,
        ("requested_purpose", "requestedPurpose", "purpose"),
        default="answer",
    )
    selected_scope = _host_optional_value(
        scope,
        snapshot,
        ("scope",),
        default="project",
    )
    selected_sensitivity = _host_optional_value(
        max_sensitivity,
        snapshot,
        ("max_sensitivity", "maxSensitivity"),
        default="private",
    )

    selected_budget = budget
    if selected_budget is None:
        snapshot_budget = _host_value(snapshot, ("budget", "budgets"))
        if isinstance(snapshot_budget, Mapping):
            selected_budget = snapshot_budget
    if selected_budget is None and token_budget is None:
        token_budget = 4_000

    return build_agent_context(
        task=selected_task,
        goal=selected_goal,
        workspace_identity=selected_workspace,
        repository_identity=selected_repository,
        commit=selected_commit,
        branch=selected_branch,
        requested_purpose=selected_purpose,
        scope=selected_scope,
        max_sensitivity=selected_sensitivity,
        active_files=active_files,
        selected_text=_host_selected_text(snapshot),
        open_tabs=open_tabs,
        current_note=current_note,
        tool_result_digests=_host_tool_digests(snapshot),
        budget=selected_budget,
        token_budget=token_budget,
    )


def validate_host_context(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a host-produced context without adding host-specific fields."""

    return validate_agent_context(envelope)


def host_bridge_contract(host: HostFrontend) -> dict[str, Any]:
    """Return the closed host binding and its exact capability declaration."""

    if host == "obsidian":
        value = {
            "schema_version": "deeplaw.host-context-bridge/v1",
            "host": "obsidian",
            "agent_context_schema": "deeplaw.agent-context-envelope/v1",
            "bridge_api": "host_context_envelope",
            "domain_apis": [
                {
                    "name": "knowledge_support",
                    "mode": "read_only",
                    "grant_required": False,
                },
                {
                    "name": "knowledge_sink",
                    "mode": "mutation",
                    "grant_required": True,
                },
            ],
            "integration_status": "supported_local_only",
            "host_surface": "official_plugin_api_and_domain_cli",
            "exact_upstream": {
                "name": "Obsidian",
                "version": "1.13.2",
                "commit": "cc1744324150c632416857c98964f87b1574a5fc",
                "plugin_api_status": "supported",
                "stable_active_note_preview_promote": False,
            },
            "ephemeral": True,
            "persistence_allowed": False,
            "persistence_performed": False,
            "authority": "none",
            "legal_authority": False,
            "max_provider_characters": 65_536,
        }
    elif host == "tolaria":
        value = {
            "schema_version": "deeplaw.host-context-bridge/v1",
            "host": "tolaria",
            "agent_context_schema": "deeplaw.agent-context-envelope/v1",
            "bridge_api": "host_context_envelope",
            "domain_apis": [
                {
                    "name": "knowledge_support",
                    "mode": "read_only",
                    "grant_required": False,
                },
                {
                    "name": "knowledge_sink",
                    "mode": "mutation",
                    "grant_required": True,
                },
            ],
            "integration_status": "integration_limited",
            "host_surface": "stdio_mcp_config_and_ui_only_open_note",
            "exact_upstream": {
                "name": "Tolaria",
                "version": "v2026-08-11",
                "commit": "cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d",
                "plugin_api_status": "not_available",
                "stable_active_note_preview_promote": False,
            },
            "ephemeral": True,
            "persistence_allowed": False,
            "persistence_performed": False,
            "authority": "none",
            "legal_authority": False,
            "max_provider_characters": 65_536,
        }
    elif host == "opencode":
        value = {
            "schema_version": "deeplaw.host-context-bridge/v1",
            "host": "opencode",
            "agent_context_schema": "deeplaw.agent-context-envelope/v1",
            "bridge_api": "host_context_envelope",
            "domain_apis": [
                {
                    "name": "knowledge_support",
                    "mode": "read_only",
                    "grant_required": False,
                },
                {
                    "name": "knowledge_sink",
                    "mode": "mutation",
                    "grant_required": True,
                },
            ],
            "integration_status": "supported_local_only",
            "host_surface": "native_mcp_and_agent_guidance",
            "exact_upstream": {
                "name": "OpenCode",
                "version": "v2-plugin-api",
                "commit": "unversioned",
                "plugin_api_status": "beta",
                "stable_active_note_preview_promote": False,
            },
            "ephemeral": True,
            "persistence_allowed": False,
            "persistence_performed": False,
            "authority": "none",
            "legal_authority": False,
            "max_provider_characters": 65_536,
        }
    else:
        raise ValueError("host must be opencode or tolaria")

    error = next(_validator("host-context-bridge.v1.schema.json").iter_errors(value), None)
    if error is not None:
        raise RuntimeError(f"Host Context Bridge Contract is invalid: {error.message}")
    return value


# Explicit aliases make the shared seam easy for thin host adapters to discover
# while keeping one implementation and one validation path.
build_host_agent_context = host_context_envelope
build_agent_context_for_host = host_context_envelope
validate_host_agent_context = validate_host_context
host_context_bridge_contract = host_bridge_contract

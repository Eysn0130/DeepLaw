"""Pure, host-neutral Agent Context Envelope v1.

This module deliberately has no Vault, filesystem, database, network, model, or
host-adapter dependency.  It builds an ephemeral request envelope and validates
its canonical hash before a caller may pass it to a provider.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = "deeplaw.agent-context-envelope/v1"
POLICY_ID = "ephemeral-agent-context/v1"
MAX_ENVELOPE_BYTES = 65_536
MAX_TASK_CHARS = 5_000
MAX_GOAL_CHARS = 2_000
MAX_IDENTITY_CHARS = 500
MAX_COMMIT_CHARS = 128
MAX_BRANCH_CHARS = 200
MAX_PATH_CHARS = 500
MAX_ACTIVE_FILES = 64
MAX_OPEN_TABS = 32
MAX_TOOL_DIGESTS = 32
MAX_SELECTED_CHARS = 12_000
MAX_TOKEN_BUDGET = 32_000
MAX_PROVIDER_CHARS = 65_536

_PURPOSES = frozenset(
    {"answer", "verify", "quote", "historical", "legal", "debug", "freshness_check"}
)
_SCOPES = frozenset({"personal", "project", "domain"})
_SENSITIVITIES = frozenset({"public", "internal", "private", "restricted"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN(?: [A-Z0-9 ]+)?-----"),
    re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"),
    re.compile(r"\b(?:api[_-]?key|password|client_secret)\s*[:=]", re.IGNORECASE),
)
_FORBIDDEN_KEYS = frozenset(
    {
        "chat_summary",
        "conversation_summary",
        "knowledge_body",
        "knowledge_revision",
        "source_body",
        "raw_result",
        "result_body",
        "payload",
        "secret",
        "secrets",
        "credential",
        "credentials",
    }
)


class AgentContextError(ValueError):
    """Raised when an envelope cannot be admitted under the closed contract."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AgentContextError("Agent Context value is not canonical JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _check_text(value: Any, *, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value) or len(value) > maximum:
        raise AgentContextError(f"{field} is invalid or exceeds its bound")
    _check_forbidden_text(value, field=field)
    return value


def _check_forbidden_text(value: str, *, field: str) -> None:
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise AgentContextError(f"{field} contains a control character")
    if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        raise AgentContextError(f"{field} contains a forbidden secret-shaped value")


def _check_selected_text(value: Any) -> str:
    if not isinstance(value, str) or len(value) > MAX_SELECTED_CHARS:
        raise AgentContextError("selected_text is invalid or exceeds its bound")
    if any(
        (ord(char) < 0x20 and char not in {"\n", "\t"}) or ord(char) == 0x7F
        for char in value
    ):
        raise AgentContextError("selected_text contains a forbidden control character")
    if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        raise AgentContextError("selected_text contains a forbidden secret-shaped value")
    return value


def _check_path(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_CHARS:
        raise AgentContextError(f"{field} is not a bounded workspace-relative path")
    if value.startswith(("/", "\\")) or _WINDOWS_PATH.match(value):
        raise AgentContextError(f"{field} must be workspace-relative POSIX")
    if "\\" in value or "//" in value:
        raise AgentContextError(f"{field} must use normalized POSIX separators")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AgentContextError(f"{field} contains an unsafe path component")
    _check_forbidden_text(value, field=field)
    return value


def _check_identity(value: Any, *, field: str) -> str:
    checked = _check_text(value, field=field, maximum=MAX_IDENTITY_CHARS)
    if checked.startswith(("/", "\\", "~")) or _WINDOWS_PATH.match(checked):
        raise AgentContextError(f"{field} must not disclose an absolute path")
    return checked


def _sorted_unique_strings(values: Any, *, field: str, maximum: int, paths: bool) -> list[str]:
    if not isinstance(values, list) or len(values) > maximum:
        raise AgentContextError(f"{field} is not a bounded array")
    checked = [
        (_check_path(value, field=f"{field}[{index}]") if paths else _check_text(
            value, field=f"{field}[{index}]", maximum=MAX_PATH_CHARS
        ))
        for index, value in enumerate(values)
    ]
    if checked != sorted(checked) or len(checked) != len(set(checked)):
        raise AgentContextError(f"{field} must be sorted and unique")
    return checked


def _check_digest(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise AgentContextError(f"{field} is not a lowercase SHA-256 digest")
    return value


def _normalize_tool_digests(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list) or len(values) > MAX_TOOL_DIGESTS:
        raise AgentContextError("tool_result_digests is not a bounded array")
    result: list[dict[str, str]] = []
    for index, item in enumerate(values):
        if not isinstance(item, Mapping) or set(item) != {"tool_name", "result_type", "sha256"}:
            raise AgentContextError(f"tool_result_digests[{index}] contains forbidden fields")
        tool_name = _check_text(item["tool_name"], field="tool_name", maximum=100)
        result_type = _check_text(item["result_type"], field="result_type", maximum=100)
        digest = _check_digest(item["sha256"], field="tool_result_digests.sha256")
        result.append({"tool_name": tool_name, "result_type": result_type, "sha256": digest})
    keys = [(item["tool_name"], item["result_type"], item["sha256"]) for item in result]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise AgentContextError("tool_result_digests must be sorted and unique")
    return result


def _check_keys(value: Mapping[str, Any]) -> None:
    for key in value:
        if not isinstance(key, str) or key in _FORBIDDEN_KEYS:
            raise AgentContextError("Agent Context contains a forbidden or unknown field")


def _validate_shape(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentContextError("Agent Context envelope must be an object")
    _check_keys(value)
    expected = {
        "schema_version", "task", "goal", "workspace_identity", "repository_identity",
        "commit", "branch", "active_files", "selected_text", "open_tabs", "current_note",
        "tool_result_digests", "requested_purpose", "scope", "max_sensitivity", "policy",
        "budget", "ephemeral", "persistence_allowed", "persistence_performed", "authority",
        "legal_authority", "envelope_sha256",
    }
    if set(value) != expected:
        raise AgentContextError("Agent Context envelope has missing or unknown fields")
    if value["schema_version"] != SCHEMA_VERSION:
        raise AgentContextError("Agent Context schema version is invalid")
    _check_text(value["task"], field="task", maximum=MAX_TASK_CHARS)
    if value["goal"] is not None:
        _check_text(value["goal"], field="goal", maximum=MAX_GOAL_CHARS)
    _check_identity(value["workspace_identity"], field="workspace_identity")
    _check_identity(value["repository_identity"], field="repository_identity")
    for field, maximum in (("commit", MAX_COMMIT_CHARS), ("branch", MAX_BRANCH_CHARS)):
        if value[field] is not None:
            _check_text(value[field], field=field, maximum=maximum)
    _sorted_unique_strings(
        value["active_files"], field="active_files", maximum=MAX_ACTIVE_FILES, paths=True
    )
    selected = value["selected_text"]
    if selected is not None:
        _check_selected_text(selected)
    _sorted_unique_strings(value["open_tabs"], field="open_tabs", maximum=MAX_OPEN_TABS, paths=True)
    if value["current_note"] is not None:
        _check_path(value["current_note"], field="current_note")
    _normalize_tool_digests(value["tool_result_digests"])
    if value["requested_purpose"] not in _PURPOSES:
        raise AgentContextError("requested_purpose is invalid")
    if value["scope"] not in _SCOPES or value["max_sensitivity"] not in _SENSITIVITIES:
        raise AgentContextError("scope or max_sensitivity is invalid")
    policy = value["policy"]
    if policy != {"policy_id": POLICY_ID, "content_mode": "bounded_selected_text"}:
        raise AgentContextError("Agent Context policy is invalid")
    budget = value["budget"]
    if not isinstance(budget, Mapping) or set(budget) != {
        "max_tokens", "max_selected_characters", "max_provider_characters"
    }:
        raise AgentContextError("Agent Context budget is invalid")
    max_tokens = budget["max_tokens"]
    max_selected = budget["max_selected_characters"]
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not 128 <= max_tokens <= MAX_TOKEN_BUDGET
        or isinstance(max_selected, bool)
        or not isinstance(max_selected, int)
        or not 0 <= max_selected <= MAX_SELECTED_CHARS
        or budget["max_provider_characters"] != MAX_PROVIDER_CHARS
    ):
        raise AgentContextError("Agent Context budget exceeds its bound")
    if selected is not None and len(selected) > max_selected:
        raise AgentContextError("selected_text exceeds the selected-text budget")
    if value["ephemeral"] is not True or value["persistence_allowed"] is not False:
        raise AgentContextError("Agent Context persistence policy is invalid")
    if value["persistence_performed"] is not False:
        raise AgentContextError("Agent Context must not report persistence")
    if value["authority"] != "none" or value["legal_authority"] is not False:
        raise AgentContextError("Agent Context Authority fields are invalid")
    _check_digest(value["envelope_sha256"], field="envelope_sha256")
    return dict(value)


def _body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "envelope_sha256"}


def validate_agent_context(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate shape, bounds, policy and the canonical envelope hash."""

    envelope = _validate_shape(value)
    expected = _digest(_body(envelope))
    if envelope["envelope_sha256"] != expected:
        raise AgentContextError("Agent Context envelope hash is invalid")
    if len(_canonical(envelope)) > MAX_ENVELOPE_BYTES:
        raise AgentContextError("Agent Context envelope exceeds its byte bound")
    return envelope


def _normalise_budget(budget: Mapping[str, Any] | None, token_budget: int | None) -> dict[str, int]:
    if budget is not None and token_budget is not None:
        raise AgentContextError("provide budget or token_budget, not both")
    if budget is None and token_budget is None:
        raise AgentContextError("an explicit token budget is required")
    if budget is None:
        budget = {"max_tokens": token_budget if token_budget is not None else 4_000}
    if not isinstance(budget, Mapping):
        raise AgentContextError("budget must be an object")
    if not set(budget).issubset(
        {"max_tokens", "token_budget", "max_selected_characters"}
    ):
        raise AgentContextError("budget contains an unknown field")
    if "max_tokens" in budget and "token_budget" in budget:
        raise AgentContextError("budget has duplicate token budget fields")
    max_tokens = budget.get("max_tokens", budget.get("token_budget"))
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise AgentContextError("budget.max_tokens is invalid")
    max_selected = budget.get("max_selected_characters", MAX_SELECTED_CHARS)
    if isinstance(max_selected, bool) or not isinstance(max_selected, int):
        raise AgentContextError("budget.max_selected_characters is invalid")
    return {
        "max_tokens": max_tokens,
        "max_selected_characters": max_selected,
        "max_provider_characters": MAX_PROVIDER_CHARS,
    }


def build_agent_context(
    *,
    task: str,
    goal: str | None,
    workspace_identity: str,
    repository_identity: str,
    commit: str | None,
    branch: str | None,
    requested_purpose: str,
    scope: str,
    max_sensitivity: str,
    active_files: Iterable[str] = (),
    selected_text: str | None = None,
    open_tabs: Iterable[str] = (),
    current_note: str | None = None,
    tool_result_digests: Iterable[Mapping[str, Any]] = (),
    budget: Mapping[str, Any] | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    """Build a deterministic ephemeral envelope without reading or writing host state."""

    try:
        active = sorted(set(active_files))
        tabs = sorted(set(open_tabs))
    except (TypeError, ValueError) as exc:
        raise AgentContextError("path collections must contain scalar values") from exc
    digests = list(tool_result_digests)
    if any(
        not isinstance(item, Mapping)
        or set(item) != {"tool_name", "result_type", "sha256"}
        for item in digests
    ):
        raise AgentContextError("tool_result_digests contains a forbidden field")
    digest_keys = set()
    for item in digests:
        digest_keys.add(
            (
                _check_text(item["tool_name"], field="tool_name", maximum=100),
                _check_text(item["result_type"], field="result_type", maximum=100),
                _check_digest(item["sha256"], field="tool_result_digests.sha256"),
            )
        )
    if len(digest_keys) != len(digests):
        raise AgentContextError("tool_result_digests contains an invalid item")
    digests = [
        {"tool_name": key[0], "result_type": key[1], "sha256": key[2]}
        for key in sorted(digest_keys)
    ]
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task": task,
        "goal": goal,
        "workspace_identity": workspace_identity,
        "repository_identity": repository_identity,
        "commit": commit,
        "branch": branch,
        "active_files": sorted(active),
        "selected_text": selected_text,
        "open_tabs": sorted(tabs),
        "current_note": current_note,
        "tool_result_digests": sorted(
            digests,
            key=lambda item: (
                str(item.get("tool_name", "")),
                str(item.get("result_type", "")),
                str(item.get("sha256", "")),
            ),
        ),
        "requested_purpose": requested_purpose,
        "scope": scope,
        "max_sensitivity": max_sensitivity,
        "policy": {"policy_id": POLICY_ID, "content_mode": "bounded_selected_text"},
        "budget": _normalise_budget(budget, token_budget),
        "ephemeral": True,
        "persistence_allowed": False,
        "persistence_performed": False,
        "authority": "none",
        "legal_authority": False,
    }
    envelope["envelope_sha256"] = _digest(envelope)
    return validate_agent_context(envelope)


build_context_envelope = build_agent_context
validate_context_envelope = validate_agent_context

__all__ = [
    "MAX_ENVELOPE_BYTES",
    "AgentContextError",
    "build_agent_context",
    "build_context_envelope",
    "validate_agent_context",
    "validate_context_envelope",
]

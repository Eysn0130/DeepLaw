"""Versioned task-line and worktree binding primitives.

The binding is deliberately opaque: callers provide only already-derived
digests and an optional base revision.  Paths, branch names, diffs, source
content, and host metadata are not part of this contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = "deeplaw.task-context-binding/v1"

_FIELD_ORDER = (
    "schema_version",
    "project_sha256",
    "task_lineage_sha256",
    "parent_task_lineage_sha256",
    "repository_sha256",
    "worktree_sha256",
    "base_revision",
    "dirty_state_sha256",
    "binding_sha256",
)
_WORKTREE_FIELDS = (
    "repository_sha256",
    "worktree_sha256",
    "base_revision",
    "dirty_state_sha256",
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BASE_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

_ROUTE_FIELDS = (
    "project_sha256",
    "task_lineage_sha256",
    "repository_sha256",
    "worktree_sha256",
)
_SNAPSHOT_FIELDS = (
    "base_revision",
    "dirty_state_sha256",
)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_digest(value: Any, *, field: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _validate_base_revision(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _BASE_REVISION.fullmatch(value):
        raise ValueError("base_revision must be null or a lowercase 40- or 64-character Git oid")
    return value


def _normalized_without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    if not all(isinstance(field, str) for field in value):
        raise ValueError("task context binding fields must be strings")
    if set(value) != set(_FIELD_ORDER):
        missing = sorted(set(_FIELD_ORDER) - set(value))
        extra = sorted(set(value) - set(_FIELD_ORDER))
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        raise ValueError("task context binding fields are not closed: " + ", ".join(detail))
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported task context binding schema_version")

    project_sha256 = _validate_digest(value["project_sha256"], field="project_sha256")
    task_lineage_sha256 = _validate_digest(
        value["task_lineage_sha256"], field="task_lineage_sha256"
    )
    parent_task_lineage_sha256 = _validate_digest(
        value["parent_task_lineage_sha256"],
        field="parent_task_lineage_sha256",
        allow_none=True,
    )
    if parent_task_lineage_sha256 == task_lineage_sha256:
        raise ValueError("parent_task_lineage_sha256 must differ from task_lineage_sha256")

    worktree_values = {
        field: value[field]
        for field in _WORKTREE_FIELDS
    }
    worktree_present = [item is not None for item in worktree_values.values()]
    if any(worktree_present) and not all(worktree_present):
        raise ValueError("repository/worktree/base/dirty fields must be all present or all null")

    repository_sha256 = _validate_digest(
        value["repository_sha256"], field="repository_sha256", allow_none=True
    )
    worktree_sha256 = _validate_digest(
        value["worktree_sha256"], field="worktree_sha256", allow_none=True
    )
    base_revision = _validate_base_revision(value["base_revision"])
    dirty_state_sha256 = _validate_digest(
        value["dirty_state_sha256"], field="dirty_state_sha256", allow_none=True
    )
    if all(item is None for item in worktree_values.values()):
        repository_sha256 = worktree_sha256 = dirty_state_sha256 = None
        base_revision = None

    return {
        "schema_version": SCHEMA_VERSION,
        "project_sha256": project_sha256,
        "task_lineage_sha256": task_lineage_sha256,
        "parent_task_lineage_sha256": parent_task_lineage_sha256,
        "repository_sha256": repository_sha256,
        "worktree_sha256": worktree_sha256,
        "base_revision": base_revision,
        "dirty_state_sha256": dirty_state_sha256,
    }


def _with_binding_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalized_without_hash(value)
    binding_sha256 = hashlib.sha256(_canonical_json(normalized).encode("utf-8")).hexdigest()
    normalized["binding_sha256"] = binding_sha256
    return {field: normalized[field] for field in _FIELD_ORDER}


def build_task_context_binding(
    project_sha256: str,
    task_lineage_sha256: str,
    parent_task_lineage_sha256: str | None = None,
    repository_sha256: str | None = None,
    worktree_sha256: str | None = None,
    base_revision: str | None = None,
    dirty_state_sha256: str | None = None,
) -> dict[str, Any]:
    """Build and hash an exact v1 task-context binding object."""

    value = {
        "schema_version": SCHEMA_VERSION,
        "project_sha256": project_sha256,
        "task_lineage_sha256": task_lineage_sha256,
        "parent_task_lineage_sha256": parent_task_lineage_sha256,
        "repository_sha256": repository_sha256,
        "worktree_sha256": worktree_sha256,
        "base_revision": base_revision,
        "dirty_state_sha256": dirty_state_sha256,
        "binding_sha256": "0" * 64,
    }
    return _with_binding_hash(value)


def normalize_task_context_binding(
    value: Mapping[str, Any] | None,
    *,
    allow_none: bool = True,
) -> dict[str, Any] | None:
    """Validate and return an exact canonical v1 binding, or explicit ``None``."""

    if value is None:
        if allow_none:
            return None
        raise ValueError("task context binding is required")
    if not isinstance(value, Mapping):
        raise ValueError("task context binding must be an object or null")

    normalized = _with_binding_hash(value)
    expected = normalized["binding_sha256"]
    if value["binding_sha256"] != expected:
        raise ValueError("task context binding_sha256 does not match canonical fields")
    return normalized


def task_route_identity(value: Mapping[str, Any]) -> dict[str, str | None]:
    """Return the canonical task-line routing identity.

    Route equality intentionally excludes the parent lineage and workspace
    snapshot fields.  The input is normalized through the same closed binding
    validator used by Run Records, so callers cannot derive a route from an
    unverified or host-specific object.
    """

    normalized = normalize_task_context_binding(value, allow_none=False)
    assert normalized is not None  # ``allow_none=False`` is an invariant.
    return {field: normalized[field] for field in _ROUTE_FIELDS}


def task_snapshot_identity(value: Mapping[str, Any]) -> dict[str, str | None]:
    """Return the canonical base/dirty workspace snapshot identity."""

    normalized = normalize_task_context_binding(value, allow_none=False)
    assert normalized is not None  # ``allow_none=False`` is an invariant.
    return {field: normalized[field] for field in _SNAPSHOT_FIELDS}


def task_route_sha256(value: Mapping[str, Any]) -> str:
    """Hash only the canonical task-line routing identity."""

    return hashlib.sha256(_canonical_json(task_route_identity(value)).encode("utf-8")).hexdigest()


def task_snapshot_sha256(value: Mapping[str, Any]) -> str:
    """Hash only the canonical workspace snapshot identity."""

    return hashlib.sha256(
        _canonical_json(task_snapshot_identity(value)).encode("utf-8")
    ).hexdigest()


__all__ = [
    "SCHEMA_VERSION",
    "build_task_context_binding",
    "normalize_task_context_binding",
    "task_route_identity",
    "task_route_sha256",
    "task_snapshot_identity",
    "task_snapshot_sha256",
]

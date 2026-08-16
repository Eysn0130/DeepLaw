"""Closed, read-only bridge from registered Host lifecycle events to task continuity."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from .host_runtime import safe_existing_path
from .task_continuity import (
    TASK_CONTINUITY_SCHEMA_VERSION,
    fork_task,
    locate_task,
    resume_task,
    start_task,
)
from .util import canonical_json, sha256_bytes, strict_json_loads

NATIVE_HOST_LIFECYCLE_RECEIPT = "deeplaw.native-host-lifecycle-receipt/v1"
MAX_CONFIG_BYTES = 64 * 1024
MAX_EVENT_BYTES = 64 * 1024
_EVENT_OPERATIONS = {
    "codex": {
        "thread/start": "start",
        "thread/resume": "resume",
        "thread/fork": "fork",
        "thread/compact/start": "compaction",
    },
    "opencode": {
        "cli.run": "start",
        "cli.run.session": "resume",
        "cli.run.fork": "fork",
        "session/summarize": "compaction",
        "session.compacted": "compaction",
    },
}
_CONFIG_FIELDS = {
    "schema_version",
    "enabled",
    "host",
    "host_version",
    "vault",
    "project",
    "task",
    "workspace",
    "workspace_class",
    "confirm_no_case_data",
    "fork",
}
_EVENT_FIELDS = {"event", "host_thread_or_session_id"}


class HostLifecycleError(ValueError):
    """Raised when a Host event is not explicitly registered or safely bounded."""


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        selected = safe_existing_path(
            path,
            directory=False,
            label="lifecycle config",
        )
    except RuntimeError as error:
        raise HostLifecycleError(str(error)) from None
    stat_result = selected.stat(follow_symlinks=False)
    if os.name != "nt":
        if stat_result.st_uid != os.geteuid() or stat_result.st_mode & 0o077:
            raise HostLifecycleError("lifecycle config permissions are not owner-only")
    else:
        from .windows_acl import native_windows_path_acl_report

        if native_windows_path_acl_report(selected).get("permissions_verified") is not True:
            raise HostLifecycleError("lifecycle config permissions are not owner-only")
    if not 1 <= selected.stat().st_size <= MAX_CONFIG_BYTES:
        raise HostLifecycleError("lifecycle config exceeds its byte bound")
    try:
        value = strict_json_loads(selected.read_bytes())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise HostLifecycleError("lifecycle config must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise HostLifecycleError("lifecycle config must be an object")
    return value


def _closed_config(value: Mapping[str, Any], *, expected_host: str) -> dict[str, Any]:
    if set(value) != _CONFIG_FIELDS:
        raise HostLifecycleError("lifecycle config does not match its closed fields")
    if value.get("schema_version") != "deeplaw.host-lifecycle-config/v1":
        raise HostLifecycleError("lifecycle config schema is unsupported")
    if value.get("enabled") is not True:
        raise HostLifecycleError("lifecycle integration is disabled")
    if value.get("host") != expected_host or expected_host not in _EVENT_OPERATIONS:
        raise HostLifecycleError("lifecycle config Host differs from the adapter")
    if value.get("workspace_class") != "project":
        raise HostLifecycleError("client and case workspaces are not admitted")
    if value.get("confirm_no_case_data") is not True:
        raise HostLifecycleError("lifecycle config requires no-case-data confirmation")
    for field, maximum in (
        ("host_version", 200),
        ("vault", 4_000),
        ("project", 500),
        ("task", 5_000),
        ("workspace", 4_000),
    ):
        item = value.get(field)
        if not isinstance(item, str) or not item or len(item.encode("utf-8")) > maximum:
            raise HostLifecycleError(f"lifecycle config {field} is invalid")
    fork = value.get("fork")
    if fork is not None:
        if not isinstance(fork, Mapping) or set(fork) != {"child_task", "child_workspace"}:
            raise HostLifecycleError("lifecycle fork registration is invalid")
        if not all(isinstance(item, str) and item for item in fork.values()):
            raise HostLifecycleError("lifecycle fork registration is invalid")
    return dict(value)


def _closed_event(value: Mapping[str, Any], *, host: str) -> dict[str, Any]:
    if set(value) - _EVENT_FIELDS or "event" not in value:
        raise HostLifecycleError("Host lifecycle event does not match its closed fields")
    event = value.get("event")
    if event not in _EVENT_OPERATIONS[host]:
        raise HostLifecycleError("Host lifecycle event is unsupported")
    hint = value.get("host_thread_or_session_id")
    if hint is not None and (
        not isinstance(hint, str) or not hint or len(hint.encode("utf-8")) > 500
    ):
        raise HostLifecycleError("Host lifecycle hint is invalid")
    return dict(value)


def _receipt(
    *,
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    operation: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": NATIVE_HOST_LIFECYCLE_RECEIPT,
        "host": config["host"],
        "host_version": config["host_version"],
        "event": event["event"],
        "operation": operation,
        "status": str(result.get("status", "invalid")),
        "native_seam_received": True,
        "host_hint_observed": event.get("host_thread_or_session_id") is not None,
        "vault_rebound": True,
        "project_task_rebound": True,
        "repository_worktree_rebound": True,
        "task_continuity_result_schema": TASK_CONTINUITY_SCHEMA_VERSION,
        "task_handle_sha256": result.get("task_handle_sha256"),
        "project_sha256": result.get("project_sha256"),
        "task_lineage_sha256": result.get("task_lineage_sha256"),
        "parent_task_lineage_sha256": result.get("parent_task_lineage_sha256"),
        "write_performed": False,
        "transcript_copied": False,
        "host_memory_read": False,
        "authentication_material_read": False,
        "raw_log_retained": False,
        "claim_eligible": False,
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json(receipt).encode("utf-8"))
    schema_path = Path(__file__).resolve().parents[2] / (
        "contracts/native-host-lifecycle-receipt.v1.schema.json"
    )
    schema = strict_json_loads(schema_path.read_bytes())
    if not isinstance(schema, dict):
        raise RuntimeError("native lifecycle receipt contract is invalid")
    error = next(Draft202012Validator(schema).iter_errors(receipt), None)
    if error is not None:
        raise RuntimeError(f"native lifecycle receipt is invalid: {error.message}")
    return receipt


def handle_host_lifecycle_event(
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    expected_host: Literal["codex", "opencode"],
) -> dict[str, Any]:
    """Rebind one closed event and delegate it to the existing continuity service."""

    selected = _closed_config(config, expected_host=expected_host)
    observed = _closed_event(event, host=expected_host)
    operation = _EVENT_OPERATIONS[expected_host][str(observed["event"])]
    common = {
        "vault_path": selected["vault"],
        "project": selected["project"],
        "task": selected["task"],
        "workspace": selected["workspace"],
    }
    if operation == "start":
        result = start_task(**common)
    elif operation in {"resume", "compaction"}:
        result = resume_task(
            **common,
            operation="compaction" if operation == "compaction" else "resume",
        )
    else:
        fork = selected["fork"]
        if not isinstance(fork, Mapping):
            raise HostLifecycleError("fork event has no owner-registered child route")
        located = locate_task(**common)
        if located.get("status") != "exact":
            result = located
        else:
            result = fork_task(
                vault_path=selected["vault"],
                task_handle=str(located["task_handle"]),
                workspace=selected["workspace"],
                child_workspace=str(fork["child_workspace"]),
                mode="child-task",
                child_task=str(fork["child_task"]),
            )
    if result.get("write_performed") is not False:
        raise RuntimeError("read-only lifecycle adapter attempted a mutation")
    return _receipt(
        config=selected,
        event=observed,
        operation=operation,
        result=result,
    )


def adapter_main(*, host: Literal["codex", "opencode"]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = _load_json_file(args.config)
        source = getattr(sys.stdin, "buffer", sys.stdin)
        raw = source.read(MAX_EVENT_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not 1 <= len(raw) <= MAX_EVENT_BYTES:
            raise HostLifecycleError("Host lifecycle event exceeds its byte bound")
        event = strict_json_loads(raw)
        if not isinstance(event, dict):
            raise HostLifecycleError("Host lifecycle event must be an object")
        receipt = handle_host_lifecycle_event(config, event, expected_host=host)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(canonical_json(receipt))
    return 0


__all__ = [
    "NATIVE_HOST_LIFECYCLE_RECEIPT",
    "HostLifecycleError",
    "adapter_main",
    "handle_host_lifecycle_event",
]

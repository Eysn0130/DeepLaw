"""Strict, content-minimized parsing for the current Native Host seam.

This module deliberately stops at observation.  It does not invoke a Host, read
authentication state, persist a session, write the Knowledge Ledger, or create a
checkpoint.  A receipt produced here is evidence of a parsed event only; a
compatibility bridge is never eligible as a qualification input.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .util import canonical_json, sha256_bytes, strict_json_loads

NATIVE_HOST_EVENT_SCHEMA_VERSION = "deeplaw.native-host-event/v2"
NATIVE_HOST_RECEIPT_SCHEMA_VERSION = "deeplaw.native-host-lifecycle-receipt/v2"
NATIVE_HOST_EVENT_V3_SCHEMA_VERSION = "deeplaw.native-host-event/v3"
NATIVE_HOST_RECEIPT_V3_SCHEMA_VERSION = "deeplaw.native-host-lifecycle-receipt/v3"
MAX_EVENT_BYTES = 64 * 1024

_EVENT_SCHEMA_NAME = "native-host-event.v2.schema.json"
_RECEIPT_SCHEMA_NAME = "native-host-lifecycle-receipt.v2.schema.json"
_EVENT_V3_SCHEMA_NAME = "native-host-event.v3.schema.json"
_RECEIPT_V3_SCHEMA_NAME = "native-host-lifecycle-receipt.v3.schema.json"
_CODEX_EVENTS = frozenset(
    {"SessionStart", "UserPromptSubmit", "PreCompact", "PostCompact", "SessionEnd"}
)
_OPENCODE_EVENTS = frozenset({"chat.message", "session", "fork", "compaction"})
_ROUTE_STATUSES = frozenset(
    {"exact", "unbound", "mismatch", "stale", "forgotten", "ambiguous"}
)
_FORBIDDEN_KEY_PARTS = (
    "transcript",
    "prompt",
    "reasoning",
    "auth",
    "secret",
    "credential",
    "password",
    "token",
)


class NativeHostObservationError(ValueError):
    """Raised when a Native Host observation is not safe or closed."""


def _repository_contract(name: str) -> Path:
    path = Path(__file__).resolve().parents[2] / "contracts" / name
    if not path.is_file() or path.is_symlink():
        raise NativeHostObservationError(f"Native Host contract is unavailable: {name}")
    return path


def _load_contract(name: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(_repository_contract(name).read_bytes())
    except (OSError, UnicodeError, ValueError) as error:
        raise NativeHostObservationError("Native Host contract is invalid") from error
    if not isinstance(value, dict):
        raise NativeHostObservationError("Native Host contract is invalid")
    Draft202012Validator.check_schema(value)
    return value


def _validate_schema(value: Mapping[str, Any], name: str) -> None:
    schema = _load_contract(name)
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error is not None:
        raise NativeHostObservationError(f"Native Host contract validation failed: {error.message}")


def _raw_bytes(value: bytes | bytearray | str | Mapping[str, Any]) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, bytearray):
        raw = bytes(value)
    elif isinstance(value, str):
        try:
            raw = value.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise NativeHostObservationError("Native Host event must be strict UTF-8") from error
    elif isinstance(value, Mapping):
        try:
            raw = canonical_json(dict(value)).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as error:
            raise NativeHostObservationError("Native Host event is not canonical JSON") from error
    else:
        raise TypeError("Native Host event must be bytes, text, or a mapping")
    if not 1 <= len(raw) <= MAX_EVENT_BYTES:
        raise NativeHostObservationError("Native Host event exceeds its byte bound")
    return raw


def _reject_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise NativeHostObservationError("Native Host event object key is invalid")
            lowered = key.casefold()
            # Codex's fixed request identity has a scalar ``reasoning`` mode.
            # Permit that one closed value while rejecting reasoning content.
            fixed_policy_keys = {
                "auth_status_command",
                "auth_material_access",
                "reasoning_effort",
                "runtime",
                "dotenv_policy",
                "secret_visibility",
            }
            fixed_reasoning_mode = key == "reasoning" and item == "max"
            if key not in fixed_policy_keys and not fixed_reasoning_mode and any(
                part in lowered for part in _FORBIDDEN_KEY_PARTS
            ):
                raise NativeHostObservationError("Native Host event contains a forbidden field")
            _reject_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_keys(item)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        return
    else:
        raise NativeHostObservationError("Native Host event contains an unsupported value")


def _non_placeholder_digest(value: str, *, field: str) -> None:
    # A real digest can theoretically be all zeroes, but accepting that value
    # would make an omitted executable/package identity indistinguishable from
    # a supplied one in a qualification receipt.
    if value == "0" * 64:
        raise NativeHostObservationError(f"{field} must identify an observed artifact")


def _non_placeholder_git(value: str, *, field: str) -> None:
    if value == "0" * 40:
        raise NativeHostObservationError(f"{field} must identify an observed source")


def _validate_event_value(value: Any, *, schema_version: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NativeHostObservationError("Native Host event must be an object")
    _reject_forbidden_keys(value)
    selected_version = value.get("schema_version") if schema_version is None else schema_version
    if selected_version == NATIVE_HOST_EVENT_SCHEMA_VERSION:
        schema_name = _EVENT_SCHEMA_NAME
    elif selected_version == NATIVE_HOST_EVENT_V3_SCHEMA_VERSION:
        schema_name = _EVENT_V3_SCHEMA_NAME
    else:
        raise NativeHostObservationError("Native Host event schema version is unsupported")
    _validate_schema(value, schema_name)
    host = value["host"]
    event_type = value["event_type"]
    if host == "codex" and event_type not in _CODEX_EVENTS:
        raise NativeHostObservationError("Codex Native Host event is unsupported")
    if host == "opencode" and event_type not in _OPENCODE_EVENTS:
        raise NativeHostObservationError("OpenCode Native Host event is unsupported")
    _non_placeholder_digest(value["session_sha256"], field="session_sha256")
    parent = value.get("parent_session_sha256")
    if event_type != "fork" and parent is not None:
        raise NativeHostObservationError("fork parent identity is only valid on a fork event")
    if parent is not None:
        _non_placeholder_digest(parent, field="parent_session_sha256")
    if host == "codex":
        identity = value["host_identity"]
        _non_placeholder_digest(identity["binary_sha256"], field="binary_sha256")
    if host == "opencode":
        identity = value["host_identity"]
        _non_placeholder_digest(identity["executable_sha256"], field="executable_sha256")
        _non_placeholder_digest(identity["package_sha256"], field="package_sha256")
        if selected_version == NATIVE_HOST_EVENT_V3_SCHEMA_VERSION:
            _non_placeholder_git(identity["source_commit"], field="source_commit")
    if selected_version == NATIVE_HOST_EVENT_V3_SCHEMA_VERSION:
        execution = value["execution_identity"]
        if host == "codex" and execution["selector_source_symlink"] is not False:
            raise NativeHostObservationError("Codex execution selector must not be a symlink")
        if (
            execution["execution_target_regular"] is not True
            or execution["execution_target_single_link"] is not True
        ):
            raise NativeHostObservationError(
                "Native Host execution target is not regular and single-link"
            )
    route = value.get("route")
    if route is not None and route["status"] not in _ROUTE_STATUSES:
        raise NativeHostObservationError("Native Host route status is invalid")
    return dict(value)


def parse_native_host_event(
    raw: bytes | bytearray | str | Mapping[str, Any],
) -> dict[str, Any]:
    """Parse one closed Native Host event without retaining Host content."""

    raw_bytes = _raw_bytes(raw)
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
        value = strict_json_loads(text)
    except (UnicodeError, ValueError) as error:
        raise NativeHostObservationError("Native Host event must be strict UTF-8 JSON") from error
    return _validate_event_value(value)


def _read_event_file(path: str | Path) -> bytes:
    selected = Path(path).expanduser().absolute()
    try:
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise NativeHostObservationError("Native Host event file is unavailable") from error
    if selected != resolved:
        raise NativeHostObservationError("Native Host event file must not be a symlink")
    for candidate in (selected, *selected.parents):
        if candidate.is_symlink():
            raise NativeHostObservationError("Native Host event path contains a symlink")
    try:
        descriptor = os.open(
            selected,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise NativeHostObservationError("Native Host event file is unavailable") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_EVENT_BYTES:
            raise NativeHostObservationError("Native Host event file is not a bounded regular file")
        raw = os.read(descriptor, MAX_EVENT_BYTES + 1)
        if len(raw) > MAX_EVENT_BYTES:
            raise NativeHostObservationError("Native Host event file exceeds its byte bound")
        return raw
    except OSError as error:
        raise NativeHostObservationError("Native Host event file could not be read") from error
    finally:
        os.close(descriptor)


def load_native_host_event(path: str | Path) -> dict[str, Any]:
    """Load one strict UTF-8 event from a regular, non-symlink file."""

    return parse_native_host_event(_read_event_file(path))


def _operation(host: str, event_type: str) -> str:
    if host == "codex":
        return {
            "SessionStart": "start",
            "UserPromptSubmit": "message",
            "PreCompact": "compaction_pre",
            "PostCompact": "compaction_post",
            "SessionEnd": "end",
        }[event_type]
    return {
        "chat.message": "message",
        "session": "resume",
        "fork": "fork",
        "compaction": "compaction",
    }[event_type]


def _route_receipt(route: Mapping[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    status = "unbound" if route is None else str(route["status"])
    values = {
        field: route.get(field) if route is not None else None
        for field in (
            "binding_sha256",
            "task_handle_sha256",
            "project_sha256",
            "repository_sha256",
            "worktree_sha256",
        )
    }
    complete = status == "exact" and all(
        isinstance(values[field], str) for field in values
    )
    source = "host_observed" if route is not None else "none"
    verified = False
    if status == "exact":
        gap = "route_unverified" if complete else "route_incomplete"
    else:
        gap = f"route_{status}"
    return (
        {
            "status": status if status in _ROUTE_STATUSES else "mismatch",
            "source": source,
            "verified": verified,
            **values,
        },
        gap,
    )


def derive_native_host_receipt(
    event: Mapping[str, Any],
    *,
    raw_observation: bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Derive a provenance-labelled receipt from one already parsed event.

    ``raw_observation`` is hashed only.  Its contents are never parsed, copied,
    or returned.  Callers that do not retain the original bytes get a digest of
    the canonical event instead.
    """

    normalized = _validate_event_value(dict(event))
    current_v3 = normalized["schema_version"] == NATIVE_HOST_EVENT_V3_SCHEMA_VERSION
    if raw_observation is None:
        raw = canonical_json(normalized).encode("utf-8")
    elif isinstance(raw_observation, (bytes, bytearray)):
        raw = bytes(raw_observation)
    else:
        raise TypeError("raw_observation must be bytes")
    if not 1 <= len(raw) <= MAX_EVENT_BYTES:
        raise NativeHostObservationError("raw Native Host observation exceeds its byte bound")
    route_binding, gap_code = _route_receipt(normalized.get("route"))
    receipt: dict[str, Any] = {
        "schema_version": (
            NATIVE_HOST_RECEIPT_V3_SCHEMA_VERSION
            if current_v3
            else NATIVE_HOST_RECEIPT_SCHEMA_VERSION
        ),
        "provenance_level": normalized["provenance_level"],
        "host": normalized["host"],
        "host_identity": dict(normalized["host_identity"]),
        "event_type": normalized["event_type"],
        "operation": _operation(normalized["host"], normalized["event_type"]),
        "event_sequence": dict(normalized["event_sequence"]),
        "raw_observation_digest": {
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
        },
        "session_sha256": normalized["session_sha256"],
        "parent_session_sha256": (
            normalized["parent_session_sha256"]
            if normalized["event_type"] == "fork"
            else None
        ),
        "route_binding_provenance": route_binding,
        "observed_methods": list(normalized["observation"]["methods_observed"]),
        "status": "observed" if gap_code is None else "gap",
        "gap": None if gap_code is None else {"code": gap_code},
        "claim_eligible": False,
        "write_performed": False,
    }
    if current_v3:
        receipt["execution_identity"] = dict(normalized["execution_identity"])
    receipt["receipt_sha256"] = sha256_bytes(
        canonical_json(receipt).encode("utf-8")
    )
    _validate_schema(
        receipt,
        _RECEIPT_V3_SCHEMA_NAME if current_v3 else _RECEIPT_SCHEMA_NAME,
    )
    return receipt


def observe_native_host_event(
    raw: bytes | bytearray | str | Mapping[str, Any],
) -> dict[str, Any]:
    """Parse one event and return its content-minimized receipt."""

    raw_bytes = _raw_bytes(raw)
    event = parse_native_host_event(raw_bytes)
    return derive_native_host_receipt(event, raw_observation=raw_bytes)


__all__ = [
    "MAX_EVENT_BYTES",
    "NATIVE_HOST_EVENT_SCHEMA_VERSION",
    "NATIVE_HOST_EVENT_V3_SCHEMA_VERSION",
    "NATIVE_HOST_RECEIPT_SCHEMA_VERSION",
    "NATIVE_HOST_RECEIPT_V3_SCHEMA_VERSION",
    "NativeHostObservationError",
    "derive_native_host_receipt",
    "load_native_host_event",
    "observe_native_host_event",
    "parse_native_host_event",
]

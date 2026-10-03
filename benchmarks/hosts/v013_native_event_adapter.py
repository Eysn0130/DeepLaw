"""Adapt actual Host observations to the current Native Host v3 contract.

This module is deliberately a small boundary adapter.  It accepts only the
content-minimized observations emitted by the Codex app-server hook projector
or the exact OpenCode project plugin.  Host identity, execution topology, and
task routing are supplied by the owner-controlled qualification driver; they
are never inferred from an observation and never copied from a legacy receipt.

The adapter does not start a Host, read a credential, call a model, or retain
Host content.  Every returned receipt is claim-ineligible.  Event validation
and receipt derivation are delegated to the public ``native_host`` seams so
that this adapter cannot create a second interpretation of the v3 contract.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableSet, Sequence
from copy import deepcopy
from typing import Any

from benchmarks.hosts import host_process_receipt_v2
from deeplaw.native_host import (
    NativeHostObservationError,
    derive_native_host_receipt,
    parse_native_host_event,
)
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

SCHEMA_VERSION = "deeplaw.native-host-event/v3"
OPENCODE_OBSERVATION_SCHEMA_VERSION = "deeplaw.opencode-native-event-observation/v1"
OPENCODE_MODEL_OBSERVATION_SCHEMA_VERSION = "deeplaw.opencode-model-observation/v1"
OPENCODE_PUBLIC_FORK_PROOF_SCHEMA_VERSION = (
    "deeplaw.opencode-public-fork-proof/v1"
)

CODEX_HOOK_EVENTS = {
    "sessionStart": "SessionStart",
    "SessionStart": "SessionStart",
    "userPromptSubmit": "UserPromptSubmit",
    "UserPromptSubmit": "UserPromptSubmit",
    "preCompact": "PreCompact",
    "PreCompact": "PreCompact",
    "postCompact": "PostCompact",
    "PostCompact": "PostCompact",
    "sessionEnd": "SessionEnd",
    "SessionEnd": "SessionEnd",
}
OPENCODE_NATIVE_EVENTS = {
    "session.created": "session",
    "session.updated": "session",
    "session.compacted": "compaction",
}

_SHA256 = set("0123456789abcdef")
_ROUTE_STATUSES = frozenset(
    {"exact", "unbound", "mismatch", "stale", "forgotten", "ambiguous"}
)
_ROUTE_DIGESTS = (
    "binding_sha256",
    "task_handle_sha256",
    "project_sha256",
    "repository_sha256",
    "worktree_sha256",
)
_EXECUTION_FIELDS = frozenset(
    {
        "selector_source_symlink",
        "execution_target_regular",
        "execution_target_single_link",
    }
)
_CODEX_HOOK_FIELDS = frozenset(
    {
        "method",
        "hook_event_name",
        "hook_status",
        "hook_source",
        "hook_handler_type",
        "hook_id_sha256",
        "hook_source_path_sha256",
        "thread_id_sha256",
        "session_id_sha256",
        "turn_id_sha256",
        "continuity_context_sha256",
        "continuity_context_bytes",
        "continuity_status",
        "continuity_statement_count",
        "continuity_gap_codes",
        "continuity_conflict_count",
    }
)
_OPENCODE_FIELDS = frozenset(
    {
        "schema_version",
        "event_type",
        "session_sha256",
        "parent_session_sha256",
        "parent_gap",
        "status",
        "gap",
    }
)
_OPENCODE_MODEL_FIELDS = frozenset(
    {
        "schema_version",
        "event_type",
        "session_sha256",
        "message_sha256",
        "role",
        "provider_id",
        "model_id",
        "summary",
        "mode",
        "finish",
        "tokens",
    }
)
_TOKEN_FIELDS = frozenset(
    {"input", "output", "reasoning", "total", "cache"}
)
_CACHE_FIELDS = frozenset({"read", "write"})
_FORK_RECEIPT_FIELDS = frozenset(
    {
        "operation",
        "status",
        "session_sha256",
        "parent_session_sha256",
        "request_sha256",
        "response_sha256",
        "gap_codes",
    }
)
_PUBLIC_FORK_PROOF_FIELDS = frozenset(
    {
        "schema_version",
        "route_observation",
        "request_body",
        "response",
        "child_plugin_observation",
        "event_barrier",
        "process_binding",
    }
)
_PUBLIC_FORK_ROUTE_FIELDS = frozenset({"method", "path", "status_code"})
_PUBLIC_FORK_BARRIER_FIELDS = frozenset(
    {
        "status",
        "response_release",
        "timed_out",
        "child_plugin_event_count",
        "event_type",
        "timeout_seconds",
        "elapsed_ms",
        "parent_source",
    }
)
_PUBLIC_FORK_PROCESS_FIELDS = frozenset(
    {
        "task_case",
        "run_id",
        "candidate_binding",
        "run_binding",
        "host_binary",
        "broker_source",
        "host_identity_sha256",
        "host_identity_source_sha256",
        "process_identity_sha256",
        "broker_instance_sha256",
        "nonce_sha256",
        "issued_at",
        "expires_at",
        "validation_reference_time",
        "selector_source_symlink",
        "execution_target_regular",
        "execution_target_single_link",
        "status",
        "exit_code",
        "isolation",
    }
)
_PUBLIC_FORK_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class NativeEventAdapterError(ValueError):
    """An actual Host observation cannot be admitted to the v3 adapter."""


def _fail(message: str) -> None:
    raise NativeEventAdapterError(message)


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        _fail(f"{label} field name is invalid")
    return value


def _observation_mapping(
    value: Mapping[str, Any] | bytes | bytearray | str,
    *,
    label: str,
) -> Mapping[str, Any]:
    """Decode a bounded observation while rejecting duplicate JSON keys."""

    if isinstance(value, Mapping):
        return _mapping(value, label=label)
    if isinstance(value, (bytes, bytearray, str)):
        raw = bytes(value) if isinstance(value, bytearray) else value
        if not len(raw) >= 1:
            _fail(f"{label} is empty")
        if len(raw) > 64 * 1024:
            _fail(f"{label} exceeds its byte bound")
        try:
            decoded = strict_json_loads(raw)
        except (UnicodeError, TypeError, ValueError) as error:
            raise NativeEventAdapterError(f"{label} is not strict JSON") from error
        return _mapping(decoded, label=label)
    _fail(f"{label} must be an object")


def _closed_fields(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
    exact: bool = False,
) -> None:
    keys = set(value)
    if (keys != set(expected)) if exact else not keys.issubset(expected):
        _fail(f"{label} fields are not closed")


def _digest(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or set(value) - _SHA256
        or value == "0" * 64
    ):
        _fail(f"{label} must be an observed lowercase SHA-256 digest")
    return value


def _nullable_digest(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _digest(value, label=label)


def _copy(value: Any, *, label: str) -> Any:
    try:
        return deepcopy(value)
    except (TypeError, ValueError) as error:
        raise NativeEventAdapterError(f"{label} cannot be copied safely") from error


def _identity_projection(identity: Mapping[str, Any], *, host: str) -> dict[str, Any]:
    """Accept either a projected v3 identity or a frozen external identity doc."""

    selected = _mapping(identity, label="frozen Host identity")
    if "hosts" in selected:
        if set(selected) != {"schema_version", "hosts"}:
            _fail("frozen Host identity fields are not closed")
        if selected.get("schema_version") != "deeplaw.host-exact-identity/v1":
            _fail("frozen Host identity schema version is unsupported")
        hosts = _mapping(selected.get("hosts"), label="frozen Host identities")
        if set(hosts) != {"codex", "opencode"} or host not in hosts:
            _fail("frozen Host identity Host set is not closed")
        item = _mapping(hosts[host], label="frozen Host identity projection")
        projected = dict(_copy(item, label="frozen Host identity"))
    else:
        projected = dict(_copy(selected, label="frozen Host identity"))
    return projected


def _execution_identity(value: Mapping[str, Any], *, host: str) -> dict[str, bool]:
    selected = _mapping(value, label="execution identity")
    _closed_fields(selected, _EXECUTION_FIELDS, label="execution identity", exact=True)
    result: dict[str, bool] = {}
    for field in sorted(_EXECUTION_FIELDS):
        if type(selected[field]) is not bool:
            _fail(f"execution identity {field} must be boolean")
        result[field] = selected[field]
    if host == "codex" and result["selector_source_symlink"] is not False:
        _fail("Codex execution selector must not be a symlink")
    if (
        result["execution_target_regular"] is not True
        or result["execution_target_single_link"] is not True
    ):
        _fail("execution target must be regular and single-link")
    return result


def _route(value: Mapping[str, Any]) -> dict[str, Any]:
    selected = _mapping(value, label="task route projection")
    allowed = frozenset({"status", *_ROUTE_DIGESTS})
    _closed_fields(selected, allowed, label="task route projection")
    status = selected.get("status")
    if status not in _ROUTE_STATUSES:
        _fail("task route status is invalid")
    if status == "exact":
        if set(selected) != set(allowed):
            _fail("exact task route must contain all five digests")
        result = {"status": status}
        for field in _ROUTE_DIGESTS:
            result[field] = _digest(selected.get(field), label=f"task route {field}")
        return result
    if any(field in selected and selected[field] is not None for field in _ROUTE_DIGESTS):
        _fail("wrong-state task route cannot carry exact binding digests")
    return {"status": status}


def _event_sequence(value: Mapping[str, Any] | int) -> dict[str, Any]:
    if isinstance(value, bool):
        _fail("event sequence index must be an integer")
    if isinstance(value, int):
        selected: Mapping[str, Any] = {"index": value}
    else:
        selected = _mapping(value, label="event sequence")
        _closed_fields(
            selected,
            frozenset({"index", "sequence_sha256"}),
            label="event sequence",
        )
    index = selected.get("index")
    if type(index) is not int or index < 0 or index > 1_000_000:
        _fail("event sequence index is invalid")
    if "sequence_sha256" in selected and selected.get("sequence_sha256") is not None:
        _fail("event sequence digest has no frozen derivation")
    # A null field is accepted only as an input normalization convenience. It
    # is deliberately omitted because no current derivation contract verifies it.
    return {"index": index}


def _session(value: Any, *, label: str = "session") -> str:
    return _digest(value, label=f"{label} identity")


def _validate_codex_hook(
    observation: Mapping[str, Any] | bytes | bytearray | str,
) -> tuple[str, list[str], str, str]:
    selected = _observation_mapping(observation, label="Codex hook observation")
    _closed_fields(selected, _CODEX_HOOK_FIELDS, label="Codex hook observation")
    if selected.get("method") != "hook/completed":
        _fail("Codex observation is not a completed hook event")
    if selected.get("hook_status") != "completed":
        _fail("Codex hook status is not completed")
    if selected.get("hook_source") != "plugin":
        _fail("Codex hook source is not the installed plugin")
    if selected.get("hook_handler_type") != "command":
        _fail("Codex hook handler is not the installed command")
    event_name = selected.get("hook_event_name")
    canonical_name = CODEX_HOOK_EVENTS.get(event_name)
    if canonical_name is None:
        _fail("Codex hook event name is unsupported")

    thread_id_sha256 = _digest(
        selected.get("thread_id_sha256"), label="Codex thread"
    )
    session_id_sha256 = _digest(
        selected.get("session_id_sha256"), label="Codex Host session"
    )
    if canonical_name in {"UserPromptSubmit", "PreCompact", "PostCompact"} and (
        "turn_id_sha256" not in selected
    ):
        _fail("Codex hook event requires an observed turn identity")
    if "turn_id_sha256" in selected:
        _digest(selected.get("turn_id_sha256"), label="Codex turn")
    hash_fields = (
        "hook_id_sha256",
        "hook_source_path_sha256",
        "continuity_context_sha256",
    )
    if not any(field in selected for field in hash_fields):
        _fail("Codex hook observation has no content-minimized digest")
    for field in hash_fields:
        if field in selected:
            _digest(selected[field], label=f"Codex {field}")
    if "continuity_context_bytes" in selected:
        value = selected["continuity_context_bytes"]
        if type(value) is not int or value < 1 or value > 64 * 1024:
            _fail("Codex continuity context byte count is invalid")
    if "continuity_status" in selected and selected["continuity_status"] not in {
        "admitted",
        "gap",
        "unreported",
    }:
        _fail("Codex continuity status is invalid")
    for field in ("continuity_statement_count", "continuity_conflict_count"):
        if field in selected:
            value = selected[field]
            if type(value) is not int or value < 0 or value > 1_000_000:
                _fail(f"Codex {field} is invalid")
    if "continuity_gap_codes" in selected:
        gaps = selected["continuity_gap_codes"]
        if not isinstance(gaps, list) or len(gaps) > 32 or len(set(gaps)) != len(gaps):
            _fail("Codex continuity gap codes are invalid")
        for gap in gaps:
            if (
                not isinstance(gap, str)
                or not gap
                or len(gap) > 100
                or any(
                    character not in "abcdefghijklmnopqrstuvwxyz0123456789_.:-"
                    for character in gap
                )
            ):
                _fail("Codex continuity gap code is invalid")
    return canonical_name, ["hook/completed"], thread_id_sha256, session_id_sha256


def validate_opencode_native_observation(
    observation: Mapping[str, Any] | bytes | bytearray | str,
) -> tuple[str, str, str]:
    """Validate one exact plugin data row without Host attestation."""
    selected = _observation_mapping(observation, label="OpenCode plugin observation")
    _closed_fields(selected, _OPENCODE_FIELDS, label="OpenCode plugin observation", exact=True)
    if selected.get("schema_version") != OPENCODE_OBSERVATION_SCHEMA_VERSION:
        _fail("OpenCode observation schema version is not the exact plugin observation")
    source_event = selected.get("event_type")
    event_type = OPENCODE_NATIVE_EVENTS.get(source_event)
    if event_type is None:
        _fail("OpenCode native event type is unsupported")
    if selected.get("status") != "observed" or selected.get("gap") is not None:
        _fail("OpenCode plugin observation is not an observed native event")
    session_sha256 = _session(selected.get("session_sha256"))
    if selected.get("parent_session_sha256") is not None:
        _fail("OpenCode plugin observation cannot carry a parent identity")
    if selected.get("parent_gap") != "parent_absent":
        _fail("OpenCode plugin observation must explicitly report parent absence")
    return str(source_event), event_type, session_sha256


def _nonnegative_integer(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        _fail(f"{label} must be a non-negative integer")
    return value


def _bounded_optional_text(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        _fail(f"{label} must be null or bounded text")
    if len(value.encode("utf-8")) > 4096:
        _fail(f"{label} exceeds its byte bound")
    return value


def _validate_opencode_model_observation(
    observation: Mapping[str, Any],
    *,
    host_identity: Mapping[str, Any],
) -> tuple[str, str]:
    """Validate the exact plugin model observation without retaining usage."""

    selected = _mapping(observation, label="OpenCode model observation")
    _closed_fields(
        selected,
        _OPENCODE_MODEL_FIELDS,
        label="OpenCode model observation",
        exact=True,
    )
    if selected.get("schema_version") != OPENCODE_MODEL_OBSERVATION_SCHEMA_VERSION:
        _fail("OpenCode model observation schema version is unsupported")
    if selected.get("event_type") != "message.updated":
        _fail("OpenCode model observation event type is unsupported")
    session_sha256 = _session(selected.get("session_sha256"))
    _digest(selected.get("message_sha256"), label="OpenCode message")
    if selected.get("role") != "assistant":
        _fail("OpenCode model observation role is not assistant")
    if selected.get("provider_id") != "deepseek":
        _fail("OpenCode model observation provider is not DeepSeek")
    identity = _identity_projection(host_identity, host="opencode")
    expected_model = identity.get("expected_response_model_id")
    if selected.get("model_id") != expected_model:
        _fail("OpenCode model observation does not match the frozen model")
    if selected.get("summary") is not False:
        _fail("OpenCode model observation summary must be false")
    _bounded_optional_text(selected.get("mode"), label="OpenCode mode")
    _bounded_optional_text(selected.get("finish"), label="OpenCode finish")

    tokens = _mapping(selected.get("tokens"), label="OpenCode model tokens")
    _closed_fields(tokens, _TOKEN_FIELDS, label="OpenCode model tokens", exact=True)
    input_tokens = _nonnegative_integer(tokens.get("input"), label="OpenCode input tokens")
    output_tokens = _nonnegative_integer(
        tokens.get("output"), label="OpenCode output tokens"
    )
    reasoning_tokens = _nonnegative_integer(
        tokens.get("reasoning"), label="OpenCode reasoning tokens"
    )
    total_tokens = _nonnegative_integer(tokens.get("total"), label="OpenCode total tokens")
    cache = _mapping(tokens.get("cache"), label="OpenCode token cache")
    _closed_fields(cache, _CACHE_FIELDS, label="OpenCode token cache", exact=True)
    cache_read = _nonnegative_integer(cache.get("read"), label="OpenCode cache read")
    cache_write = _nonnegative_integer(cache.get("write"), label="OpenCode cache write")
    if total_tokens != input_tokens + output_tokens + reasoning_tokens + cache_read + cache_write:
        _fail("OpenCode token total is inconsistent")
    return "chat.message", session_sha256


def _validate_fork_receipt(
    value: Mapping[str, Any],
    *,
    child_session_sha256: str,
) -> str:
    selected = _mapping(value, label="OpenCode fork receipt")
    _closed_fields(
        selected,
        _FORK_RECEIPT_FIELDS,
        label="OpenCode fork receipt",
        exact=True,
    )
    if selected.get("operation") != "fork" or selected.get("status") != "forked":
        _fail("OpenCode fork receipt operation or status is invalid")
    if (
        _session(selected.get("session_sha256"), label="fork child")
        != child_session_sha256
    ):
        _fail("OpenCode fork receipt child identity differs")
    parent_session_sha256 = _session(
        selected.get("parent_session_sha256"), label="fork parent"
    )
    if parent_session_sha256 == child_session_sha256:
        _fail("OpenCode fork sessions must be distinct")
    if selected.get("gap_codes") != []:
        _fail("OpenCode fork receipt contains gaps")
    expected_request_sha256 = sha256_bytes(
        canonical_json({"operation": "session.fork"}).encode("utf-8")
    )
    expected_response_sha256 = sha256_bytes(
        canonical_json(
            {
                "child_session_sha256": child_session_sha256,
                "forked_from_id_sha256": parent_session_sha256,
            }
        ).encode("utf-8")
    )
    if selected.get("request_sha256") != expected_request_sha256:
        _fail("OpenCode fork receipt request digest differs")
    if selected.get("response_sha256") != expected_response_sha256:
        _fail("OpenCode fork receipt response digest differs")
    return parent_session_sha256


def _public_fork_bytes(value: Any, *, label: str) -> bytes:
    """Require the original bounded bytes for a source-specific observation."""

    if isinstance(value, bytearray):
        value = bytes(value)
    if type(value) is not bytes or not 1 <= len(value) <= 64 * 1024:
        _fail(f"{label} must be bounded original bytes")
    return value


def _public_fork_json(value: Any, *, label: str) -> tuple[bytes, Mapping[str, Any]]:
    raw = _public_fork_bytes(value, label=label)
    try:
        decoded = strict_json_loads(raw)
    except (UnicodeError, TypeError, ValueError) as error:
        raise NativeEventAdapterError(f"{label} is not strict JSON") from error
    return raw, _mapping(decoded, label=label)


def _validate_public_fork_route(
    value: Any,
) -> tuple[str, str, str]:
    """Validate the actual public fork route and derive its parent identity.

    The route projection deliberately retains no route text in the returned
    proof.  Its parent session ID is used only in memory to bind the route to
    the response and plugin event.
    """

    if isinstance(value, Mapping):
        selected = _mapping(value, label="OpenCode public fork route observation")
        _closed_fields(
            selected,
            _PUBLIC_FORK_ROUTE_FIELDS,
            label="OpenCode public fork route observation",
            exact=True,
        )
        raw = canonical_json(dict(selected)).encode("utf-8")
    else:
        raw, selected = _public_fork_json(
            value, label="OpenCode public fork route observation"
        )
        _closed_fields(
            selected,
            _PUBLIC_FORK_ROUTE_FIELDS,
            label="OpenCode public fork route observation",
            exact=True,
        )
    if selected.get("method") != "POST":
        _fail("OpenCode public fork route method is not POST")
    if type(selected.get("status_code")) is not int or selected["status_code"] != 200:
        _fail("OpenCode public fork route status is not 200")
    path = selected.get("path")
    if not isinstance(path, str):
        _fail("OpenCode public fork route path is invalid")
    match = re.fullmatch(
        r"/session/(?P<parent>[A-Za-z0-9][A-Za-z0-9._:-]{0,199})/fork",
        path,
    )
    if match is None:
        _fail("OpenCode public fork route is not the actual session fork route")
    parent_session_id = match.group("parent")
    parent_session_sha256 = _session(
        sha256_bytes(parent_session_id.encode("utf-8")), label="fork route parent"
    )
    return sha256_bytes(raw), parent_session_id, parent_session_sha256


def _validate_public_fork_response(
    value: Any,
    *,
    parent_session_id: str,
) -> tuple[str, str, str]:
    """Extract only the child identity from the original fork response."""

    raw, selected = _public_fork_json(
        value, label="OpenCode public fork response"
    )
    identity_fields = [
        field for field in ("id", "sessionID", "sessionId") if field in selected
    ]
    if len(identity_fields) != 1 or not isinstance(selected[identity_fields[0]], str):
        _fail("OpenCode public fork response child identity is invalid")
    child_session_id = selected[identity_fields[0]]
    if _PUBLIC_FORK_SESSION_ID.fullmatch(child_session_id) is None:
        _fail("OpenCode public fork response child identity is unsafe")
    if child_session_id == parent_session_id:
        _fail("OpenCode public fork sessions must be distinct")
    parent_fields = [
        field for field in ("parentID", "parentId", "parent_id") if field in selected
    ]
    if len(parent_fields) > 1:
        _fail("OpenCode public fork response contains duplicate parent identity fields")
    if parent_fields and selected[parent_fields[0]] != parent_session_id:
        _fail("OpenCode public fork response parent identity differs from the route")
    child_session_sha256 = _session(
        sha256_bytes(child_session_id.encode("utf-8")), label="fork response child"
    )
    return sha256_bytes(raw), child_session_id, child_session_sha256


def _validate_public_fork_barrier(value: Any) -> dict[str, Any]:
    selected = _mapping(value, label="OpenCode public fork event barrier")
    _closed_fields(
        selected,
        _PUBLIC_FORK_BARRIER_FIELDS,
        label="OpenCode public fork event barrier",
        exact=True,
    )
    if (
        selected.get("status") != "satisfied"
        or selected.get("response_release") != "after_child_plugin_event"
        or selected.get("timed_out") is not False
        or selected.get("child_plugin_event_count") != 1
        or selected.get("event_type") != "session.created"
        or selected.get("timeout_seconds") != 30
        or type(selected.get("elapsed_ms")) is not int
        or not 0 <= selected["elapsed_ms"] <= 30_000
        or selected.get("parent_source") != "actual_ingress_route"
    ):
        _fail("OpenCode public fork event barrier was not satisfied")
    return dict(selected)


def _public_fork_process_binding(value: Any) -> dict[str, Any]:
    selected = _mapping(value, label="OpenCode public fork process binding")
    _closed_fields(
        selected,
        _PUBLIC_FORK_PROCESS_FIELDS,
        label="OpenCode public fork process binding",
        exact=True,
    )
    if selected.get("task_case") != "continuity":
        _fail("OpenCode public fork task case is unsupported")
    status = selected.get("status")
    exit_code = selected.get("exit_code")
    if (status == "running" and exit_code is None) or (status == "exited" and exit_code == 0):
        pass
    else:
        _fail("OpenCode public fork process binding has an invalid lifecycle state")
    return dict(_copy(selected, label="OpenCode public fork process binding"))


def _check_expected_public_fork_process_binding(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any] | None,
) -> None:
    if expected is None:
        return
    selected = _mapping(expected, label="expected OpenCode public fork process binding")
    allowed = _PUBLIC_FORK_PROCESS_FIELDS | frozenset({"host"})
    _closed_fields(
        selected,
        allowed,
        label="expected OpenCode public fork process binding",
    )
    if "host" in selected and selected["host"] != "opencode":
        _fail("expected OpenCode public fork Host differs")
    complete = _public_fork_process_binding(
        {field: value for field, value in selected.items() if field != "host"}
    )
    for field, value in complete.items():
        if actual.get(field) != value:
            _fail(f"OpenCode public fork process binding differs for {field}")


def _public_fork_native_binding(
    event: Mapping[str, Any],
    lifecycle_receipt: Mapping[str, Any],
) -> dict[str, str]:
    """Derive the v2 native binding from the provisional v3 event."""

    event_sequence = sha256_bytes(
        canonical_json(
            {
                "schema_version": SCHEMA_VERSION,
                "events": [
                    {
                        "event_type": event["event_type"],
                        "event_sequence": event["event_sequence"],
                        "session_sha256": event["session_sha256"],
                        "parent_session_sha256": event["parent_session_sha256"],
                    }
                ],
            }
        ).encode("utf-8")
    )
    session_identity = sha256_bytes(
        canonical_json(
            {
                "schema_version": "deeplaw.native-opencode-session-binding/v1",
                "host": event["host"],
                "session_sha256": event["session_sha256"],
                "parent_session_sha256": event["parent_session_sha256"],
            }
        ).encode("utf-8")
    )
    lifecycle_record = _digest(
        lifecycle_receipt.get("receipt_sha256"),
        label="OpenCode native lifecycle record",
    )
    return {
        "event_sequence_sha256": event_sequence,
        "session_identity_sha256": session_identity,
        "lifecycle_record_sha256": lifecycle_record,
    }


def _public_fork_process_proof(
    *,
    process: Mapping[str, Any],
    route_sha256: str,
    request_body_sha256: str,
    response_sha256: str,
    parent_session_sha256: str,
    child_session_sha256: str,
    child_plugin_event_sha256: str,
    native_binding: Mapping[str, str],
) -> dict[str, Any]:
    proof: dict[str, Any] = {
        "proof_kind": "opencode_public_fork_route_correlation",
        "process_identity_sha256": process["process_identity_sha256"],
        "request_method": "POST",
        "route_observation_sha256": route_sha256,
        "request_body_sha256": request_body_sha256,
        "response_sha256": response_sha256,
        "parent_session_sha256": parent_session_sha256,
        "child_session_sha256": child_session_sha256,
        "child_plugin_event_sha256": child_plugin_event_sha256,
        "child_plugin_session_sha256": child_session_sha256,
        "native_event_sequence_sha256": native_binding["event_sequence_sha256"],
        "native_session_identity_sha256": native_binding["session_identity_sha256"],
        "native_lifecycle_record_sha256": native_binding[
            "lifecycle_record_sha256"
        ],
        "same_process": True,
        "actual_route_observed": True,
    }
    proof["route_correlation_sha256"] = host_process_receipt_v2.correlation_sha256(
        {
            key: proof[key]
            for key in (
                "process_identity_sha256",
                "request_method",
                "route_observation_sha256",
                "request_body_sha256",
                "response_sha256",
                "parent_session_sha256",
                "child_session_sha256",
                "child_plugin_event_sha256",
                "child_plugin_session_sha256",
                "native_event_sequence_sha256",
                "native_session_identity_sha256",
                "native_lifecycle_record_sha256",
            )
        }
    )
    return proof


def adapt_opencode_public_fork_observation(
    fork_proof: Mapping[str, Any],
    *,
    host_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    route: Mapping[str, Any],
    event_sequence: Mapping[str, Any] | int,
    expected_process_binding: Mapping[str, Any] | None = None,
    seen_nonce_sha256s: MutableSet[str] | None = None,
) -> dict[str, Any]:
    """Adapt one actual OpenCode public fork and its optional v2 receipt.

    ``fork_proof`` is the source-specific boundary used by an owner-external
    producer.  It contains original route/body/response/plugin bytes and the
    already observed process metadata.  The original bytes are hashed only;
    the returned projection contains no route, session, or Host payload text.
    A provisional v3 fork event is built before an optional clean-exit v2
    receipt, which avoids a circular dependency between ``native_event_binding``
    and the event.  An active process returns only the native event, lifecycle
    receipt, and safe fork proof.
    """

    selected = _mapping(fork_proof, label="OpenCode public fork proof")
    if selected.get("schema_version") != OPENCODE_PUBLIC_FORK_PROOF_SCHEMA_VERSION:
        _fail("OpenCode public fork proof schema version is unsupported")
    _closed_fields(
        selected,
        _PUBLIC_FORK_PROOF_FIELDS,
        label="OpenCode public fork proof",
        exact=True,
    )
    route_sha256, parent_session_id, parent_session_sha256 = _validate_public_fork_route(
        selected["route_observation"]
    )
    request_body = _public_fork_bytes(
        selected["request_body"], label="OpenCode public fork request body"
    )
    if request_body != b"{}":
        _fail("OpenCode public fork request body must be the exact empty object")
    request_body_sha256 = sha256_bytes(request_body)
    response_sha256, _child_session_id, response_child_sha256 = _validate_public_fork_response(
        selected["response"], parent_session_id=parent_session_id
    )
    plugin_raw, _plugin_value = _public_fork_json(
        selected["child_plugin_observation"],
        label="OpenCode child plugin observation",
    )
    source_event, event_type, plugin_child_sha256 = validate_opencode_native_observation(
        plugin_raw
    )
    if source_event != "session.created" or event_type != "session":
        _fail("OpenCode public fork child plugin event must be session.created")
    if plugin_child_sha256 != response_child_sha256:
        _fail("OpenCode public fork response and child plugin identities differ")
    _validate_public_fork_barrier(selected["event_barrier"])
    process = _public_fork_process_binding(selected["process_binding"])
    _check_expected_public_fork_process_binding(process, expected_process_binding)

    event = _build_event(
        host="opencode",
        host_identity=host_identity,
        execution_identity=execution_identity,
        event_type="fork",
        event_sequence=event_sequence,
        session_sha256=plugin_child_sha256,
        parent_session_sha256=parent_session_sha256,
        methods_observed=["opencode.plugin.event"],
        route=route,
    )
    base = _result(event)
    native_binding = _public_fork_native_binding(event, base["receipt"])
    proof = _public_fork_process_proof(
        process=process,
        route_sha256=route_sha256,
        request_body_sha256=request_body_sha256,
        response_sha256=response_sha256,
        parent_session_sha256=parent_session_sha256,
        child_session_sha256=plugin_child_sha256,
        child_plugin_event_sha256=sha256_bytes(plugin_raw),
        native_binding=native_binding,
    )
    process_receipt: dict[str, Any] | None = None
    if process["status"] == "exited":
        try:
            process_receipt = host_process_receipt_v2.build_receipt(
                host="opencode",
                task_case=process["task_case"],
                run_id=process["run_id"],
                candidate_binding=process["candidate_binding"],
                run_binding=process["run_binding"],
                host_binary=process["host_binary"],
                broker_source=process["broker_source"],
                host_identity_sha256=process["host_identity_sha256"],
                host_identity_source_sha256=process["host_identity_source_sha256"],
                process_identity_sha256=process["process_identity_sha256"],
                broker_instance_sha256=process["broker_instance_sha256"],
                nonce_sha256=process["nonce_sha256"],
                issued_at=process["issued_at"],
                expires_at=process["expires_at"],
                validation_reference_time=process["validation_reference_time"],
                selector_source_symlink=process["selector_source_symlink"],
                execution_target_regular=process["execution_target_regular"],
                execution_target_single_link=process["execution_target_single_link"],
                status=process["status"],
                exit_code=process["exit_code"],
                native_event_binding=native_binding,
                proof=proof,
                isolation=process["isolation"],
            )
            expected = expected_process_binding
            process_receipt = host_process_receipt_v2.validate_receipt(
                process_receipt,
                expected_host="opencode",
                expected_task_case=(
                    expected.get("task_case") if expected is not None else None
                ),
                expected_run_id=(expected.get("run_id") if expected is not None else None),
                expected_candidate=(
                    expected.get("candidate_binding") if expected is not None else None
                ),
                expected_run_binding=(
                    expected.get("run_binding") if expected is not None else None
                ),
                expected_broker_sha256=(
                    expected.get("broker_source", {}).get("sha256")
                    if expected is not None
                    and isinstance(expected.get("broker_source"), Mapping)
                    else None
                ),
                expected_host_identity_sha256=(
                    expected.get("host_identity_sha256")
                    if expected is not None
                    else None
                ),
                expected_host_identity_source_sha256=(
                    expected.get("host_identity_source_sha256")
                    if expected is not None
                    else None
                ),
                expected_host_binary=(
                    expected.get("host_binary") if expected is not None else None
                ),
                seen_nonce_sha256s=(
                    seen_nonce_sha256s if seen_nonce_sha256s is not None else set()
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            host_process_receipt_v2.HostProcessReceiptV2Error,
        ) as error:
            raise NativeEventAdapterError(
                "OpenCode public fork v2 process receipt was rejected"
            ) from error
    elif seen_nonce_sha256s is not None:
        nonce = _digest(process["nonce_sha256"], label="public fork nonce")
        if nonce in seen_nonce_sha256s:
            _fail("OpenCode public fork one-time nonce was replayed")
        seen_nonce_sha256s.add(nonce)
    base["public_fork_proof"] = {
        "schema_version": OPENCODE_PUBLIC_FORK_PROOF_SCHEMA_VERSION,
        "route_observation_sha256": route_sha256,
        "request_body_sha256": request_body_sha256,
        "response_sha256": response_sha256,
        "parent_session_sha256": parent_session_sha256,
        "child_session_sha256": plugin_child_sha256,
        "child_plugin_event_sha256": sha256_bytes(plugin_raw),
        "event_barrier": dict(selected["event_barrier"]),
        "process_binding": _copy(
            process,
            label="OpenCode public fork process binding projection",
        ),
        "process_proof": _copy(
            proof,
            label="OpenCode public fork process proof projection",
        ),
    }
    if process_receipt is not None:
        base["process_receipt"] = process_receipt
    else:
        base.pop("process_receipt", None)
    return base


# The proof-oriented spelling is retained as a direct alias so the owner
# producer can name the source-specific operation without changing the return
# shape of the existing adapter functions.
adapt_opencode_public_fork_proof = adapt_opencode_public_fork_observation


def _build_event(
    *,
    host: str,
    host_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    event_type: str,
    event_sequence: Mapping[str, Any] | int,
    session_sha256: str,
    parent_session_sha256: str | None,
    methods_observed: Sequence[str],
    route: Mapping[str, Any],
) -> dict[str, Any]:
    if host not in {"codex", "opencode"}:
        _fail("Native Host is unsupported")
    identity = _identity_projection(host_identity, host=host)
    topology = _execution_identity(execution_identity, host=host)
    selected_session = _session(session_sha256)
    parent = _nullable_digest(parent_session_sha256, label="parent session")
    if event_type != "fork" and parent is not None:
        _fail("parent session identity is only valid for a fork event")
    selected_methods = list(methods_observed)
    if not selected_methods or len(set(selected_methods)) != len(selected_methods):
        _fail("Native Host methods are not a closed observed sequence")
    for method in selected_methods:
        if not isinstance(method, str) or not method:
            _fail("Native Host method is invalid")
    event = {
        "schema_version": SCHEMA_VERSION,
        "provenance_level": "native_plugin_hook",
        "host": host,
        "host_identity": identity,
        "execution_identity": topology,
        "event_type": event_type,
        "event_sequence": _event_sequence(event_sequence),
        "session_sha256": selected_session,
        "parent_session_sha256": parent,
        "observation": {"methods_observed": selected_methods, "status": "completed"},
        "route": _route(route),
    }
    try:
        parsed = parse_native_host_event(event)
    except (NativeHostObservationError, TypeError, ValueError) as error:
        raise NativeEventAdapterError("Native Host v3 event was rejected") from error
    return parsed


def _result(event: Mapping[str, Any]) -> dict[str, Any]:
    try:
        receipt = derive_native_host_receipt(event)
    except (NativeHostObservationError, TypeError, ValueError) as error:
        raise NativeEventAdapterError("Native Host v3 receipt derivation failed") from error
    if receipt.get("claim_eligible") is not False or receipt.get("write_performed") is not False:
        _fail("Native Host adapter produced an ineligible receipt violation")
    return {"event": _copy(dict(event), label="Native Host event"), "receipt": receipt}


def adapt_codex_hook_observation(
    observation: Mapping[str, Any] | bytes | bytearray | str,
    *,
    host_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    route: Mapping[str, Any],
    event_sequence: Mapping[str, Any] | int,
    session_sha256: str,
) -> dict[str, Any]:
    """Adapt one actual Codex ``hook/completed`` projection."""

    (
        event_type,
        methods,
        _actual_thread_id_sha256,
        actual_session_id_sha256,
    ) = _validate_codex_hook(observation)
    if _session(session_sha256) != actual_session_id_sha256:
        _fail("Codex caller session identity differs from the observed Host session")
    event = _build_event(
        host="codex",
        host_identity=host_identity,
        execution_identity=execution_identity,
        event_type=event_type,
        event_sequence=event_sequence,
        session_sha256=session_sha256,
        parent_session_sha256=None,
        methods_observed=methods,
        route=route,
    )
    return _result(event)


def adapt_opencode_plugin_observation(
    observation: Mapping[str, Any] | bytes | bytearray | str,
    *,
    host_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    route: Mapping[str, Any],
    event_sequence: Mapping[str, Any] | int,
    supervisor_parent_observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt one actual OpenCode native or model plugin observation."""

    selected = _observation_mapping(observation, label="OpenCode plugin observation")
    if selected.get("schema_version") == OPENCODE_MODEL_OBSERVATION_SCHEMA_VERSION:
        if supervisor_parent_observation is not None:
            _fail("OpenCode model observation cannot carry a parent observation")
        event_type, session_sha256 = _validate_opencode_model_observation(
            selected,
            host_identity=host_identity,
        )
        parent = None
        methods_observed = ["message.updated"]
    else:
        source_event, event_type, session_sha256 = validate_opencode_native_observation(
            selected
        )
        parent = None
        if supervisor_parent_observation is not None:
            if source_event != "session.created":
                _fail("OpenCode fork receipt requires session.created")
            parent = _validate_fork_receipt(
                supervisor_parent_observation,
                child_session_sha256=session_sha256,
            )
            event_type = "fork"
        methods_observed = ["opencode.plugin.event"]
    event = _build_event(
        host="opencode",
        host_identity=host_identity,
        execution_identity=execution_identity,
        event_type=event_type,
        event_sequence=event_sequence,
        session_sha256=session_sha256,
        parent_session_sha256=parent,
        methods_observed=methods_observed,
        route=route,
    )
    return _result(event)


def adapt_native_observation(
    host: str,
    observation: Mapping[str, Any] | bytes | bytearray | str,
    *,
    host_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    route: Mapping[str, Any],
    event_sequence: Mapping[str, Any] | int,
    session_sha256: str | None = None,
    supervisor_parent_observation: Mapping[str, Any] | None = None,
    public_fork_proof: Mapping[str, Any] | None = None,
    expected_process_binding: Mapping[str, Any] | None = None,
    seen_nonce_sha256s: MutableSet[str] | None = None,
) -> dict[str, Any]:
    """Dispatch to one exact Host observation or an explicit fork proof."""

    if host == "codex":
        if session_sha256 is None:
            _fail("Codex session identity is required outside the hook observation")
        if supervisor_parent_observation is not None:
            _fail("Codex does not accept an OpenCode supervisor parent observation")
        if public_fork_proof is not None:
            _fail("Codex does not accept an OpenCode public fork proof")
        return adapt_codex_hook_observation(
            observation,
            host_identity=host_identity,
            execution_identity=execution_identity,
            route=route,
            event_sequence=event_sequence,
            session_sha256=session_sha256,
        )
    if host == "opencode":
        if public_fork_proof is not None:
            if supervisor_parent_observation is not None:
                _fail(
                    "OpenCode public fork proof cannot carry a legacy parent observation"
                )
            proof = _mapping(public_fork_proof, label="OpenCode public fork proof")
            if proof.get("schema_version") != OPENCODE_PUBLIC_FORK_PROOF_SCHEMA_VERSION:
                _fail("OpenCode public fork proof schema version is unsupported")
            plugin_raw = proof.get("child_plugin_observation")
            if not isinstance(observation, (bytes, bytearray)) or not isinstance(
                plugin_raw, (bytes, bytearray)
            ):
                _fail(
                    "OpenCode public fork dispatch requires the exact child plugin bytes"
                )
            if bytes(observation) != plugin_raw:
                _fail("OpenCode public fork dispatch child plugin bytes differ")
            if expected_process_binding is None:
                _fail("OpenCode public fork dispatch requires expected process binding")
            if not isinstance(seen_nonce_sha256s, MutableSet):
                _fail("OpenCode public fork dispatch requires a shared mutable nonce set")
            return adapt_opencode_public_fork_observation(
                public_fork_proof,
                expected_process_binding=expected_process_binding,
                seen_nonce_sha256s=seen_nonce_sha256s,
                host_identity=host_identity,
                execution_identity=execution_identity,
                route=route,
                event_sequence=event_sequence,
            )
        return adapt_opencode_plugin_observation(
            observation,
            host_identity=host_identity,
            execution_identity=execution_identity,
            route=route,
            event_sequence=event_sequence,
            supervisor_parent_observation=supervisor_parent_observation,
        )
    _fail("Native Host is unsupported")


def _sequence_indices(
    values: Sequence[Mapping[str, Any]],
) -> list[int]:
    indices: list[int] = []
    for value in values:
        selected = _mapping(value, label="event sequence")
        index = selected.get("index")
        if type(index) is not int:
            _fail("event sequence index is invalid")
        indices.append(index)
    if indices != list(range(len(indices))):
        _fail("Native Host event sequence is not contiguous")
    return indices


def adapt_codex_hook_sequence(
    observations: Sequence[Mapping[str, Any]],
    *,
    host_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    route: Mapping[str, Any],
    session_sha256: str,
    event_sequences: Sequence[Mapping[str, Any] | int] | None = None,
) -> list[dict[str, Any]]:
    """Adapt a zero-based contiguous Codex hook sequence."""

    if event_sequences is None:
        sequences: list[Mapping[str, Any] | int] = list(range(len(observations)))
    else:
        if len(event_sequences) != len(observations):
            _fail("Codex event sequence length differs")
        sequences = list(event_sequences)
    results = [
        adapt_codex_hook_observation(
            observation,
            host_identity=host_identity,
            execution_identity=execution_identity,
            route=route,
            event_sequence=sequences[index],
            session_sha256=session_sha256,
        )
        for index, observation in enumerate(observations)
    ]
    _sequence_indices([result["event"]["event_sequence"] for result in results])
    return results


def adapt_opencode_plugin_sequence(
    observations: Sequence[Mapping[str, Any]],
    *,
    host_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    route: Mapping[str, Any],
    supervisor_parent_observations: Sequence[Mapping[str, Any] | None] | None = None,
    event_sequences: Sequence[Mapping[str, Any] | int] | None = None,
) -> list[dict[str, Any]]:
    """Adapt a zero-based contiguous OpenCode plugin observation sequence."""

    if supervisor_parent_observations is None:
        parents: list[Mapping[str, Any] | None] = [None] * len(observations)
    else:
        if len(supervisor_parent_observations) != len(observations):
            _fail("OpenCode supervisor parent sequence length differs")
        parents = list(supervisor_parent_observations)
    if event_sequences is None:
        sequences: list[Mapping[str, Any] | int] = list(range(len(observations)))
    else:
        if len(event_sequences) != len(observations):
            _fail("OpenCode event sequence length differs")
        sequences = list(event_sequences)
    results = [
        adapt_opencode_plugin_observation(
            observation,
            host_identity=host_identity,
            execution_identity=execution_identity,
            route=route,
            event_sequence=sequences[index],
            supervisor_parent_observation=parents[index],
        )
        for index, observation in enumerate(observations)
    ]
    _sequence_indices([result["event"]["event_sequence"] for result in results])
    return results


__all__ = [
    "CODEX_HOOK_EVENTS",
    "OPENCODE_MODEL_OBSERVATION_SCHEMA_VERSION",
    "OPENCODE_NATIVE_EVENTS",
    "OPENCODE_OBSERVATION_SCHEMA_VERSION",
    "OPENCODE_PUBLIC_FORK_PROOF_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "NativeEventAdapterError",
    "adapt_codex_hook_observation",
    "adapt_codex_hook_sequence",
    "adapt_native_observation",
    "adapt_opencode_plugin_observation",
    "adapt_opencode_plugin_sequence",
    "adapt_opencode_public_fork_observation",
    "adapt_opencode_public_fork_proof",
    "validate_opencode_native_observation",
]

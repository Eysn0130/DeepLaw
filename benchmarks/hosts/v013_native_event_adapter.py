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

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from deeplaw.native_host import (
    NativeHostObservationError,
    derive_native_host_receipt,
    parse_native_host_event,
)
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

SCHEMA_VERSION = "deeplaw.native-host-event/v3"
OPENCODE_OBSERVATION_SCHEMA_VERSION = "deeplaw.opencode-native-event-observation/v1"
OPENCODE_MODEL_OBSERVATION_SCHEMA_VERSION = "deeplaw.opencode-model-observation/v1"

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
) -> tuple[str, list[str], str]:
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
    return canonical_name, ["hook/completed"], thread_id_sha256


def _validate_opencode_observation(
    observation: Mapping[str, Any] | bytes | bytearray | str,
) -> tuple[str, str, str]:
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

    event_type, methods, actual_thread_id_sha256 = _validate_codex_hook(observation)
    if _session(session_sha256) != actual_thread_id_sha256:
        _fail("Codex caller session identity differs from the observed thread")
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
        source_event, event_type, session_sha256 = _validate_opencode_observation(
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
) -> dict[str, Any]:
    """Dispatch to one of the two exact current Host observation adapters."""

    if host == "codex":
        if session_sha256 is None:
            _fail("Codex session identity is required outside the hook observation")
        if supervisor_parent_observation is not None:
            _fail("Codex does not accept an OpenCode supervisor parent observation")
        return adapt_codex_hook_observation(
            observation,
            host_identity=host_identity,
            execution_identity=execution_identity,
            route=route,
            event_sequence=event_sequence,
            session_sha256=session_sha256,
        )
    if host == "opencode":
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
    "SCHEMA_VERSION",
    "NativeEventAdapterError",
    "adapt_codex_hook_observation",
    "adapt_codex_hook_sequence",
    "adapt_native_observation",
    "adapt_opencode_plugin_observation",
    "adapt_opencode_plugin_sequence",
]

"""Retain bounded, source-backed task-domain service observations.

This is a development evidence seam, not a Native Host or model collector.  It
invokes the existing task-domain driver only after validating a frozen seed and
associates its read-only output with one already observed native-v3 message
event.  The association is deliberately claim-ineligible and does not assert
that the Host consumed the driver output.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from deeplaw.native_host import derive_native_host_receipt, parse_native_host_event
from deeplaw.util import canonical_json

SERVICE_SCHEMA_VERSION = "deeplaw.v013-task-service-observation/v1"
TASK_RESULT_SCHEMA_VERSION = "deeplaw.v013-host-task-result/v2"
TASK_CASES = frozenset({"living_wiki", "professional_evidence"})
_MESSAGE_EVENTS = {"codex": "UserPromptSubmit", "opencode": "chat.message"}
_CALLER = "task_domain_driver"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_SERVICE_CALLS = {
    ("SourceReadService", "get"): "source_read",
    ("SourceReadService", "fragment"): "fragment_read",
    ("WikiReadService", "page"): "wiki_read",
    ("knowledge_support", "query"): "query_context",
    ("knowledge_support", "context"): "query_context",
}
_DROP_REQUEST_FIELDS = frozenset({"query", "task"})
_DROP_PROJECTION_FIELDS = frozenset(
    {
        "calls",
        "receipt_id",
        "provider_content_bytes",
        "provider_content_kind",
        "provider_content_sha256",
        "structured_response_byte_size",
        "structured_response_kind",
        "structured_response_sha256",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "prompt",
        "transcript",
        "reasoning_content",
        "secret",
        "credential",
        "password",
        "authorization",
        "api_key",
        "access_token",
        "query_trace",
        "ledger",
    }
)


class TaskServiceObservationError(ValueError):
    """A task-domain observation cannot be retained safely."""


def _fail(message: str) -> None:
    raise TaskServiceObservationError(message)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None or value == "0" * 64:
        _fail(f"{label} is not an observed SHA-256 digest")
    return value


def _git(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _GIT.fullmatch(value) is None or value == "0" * 40:
        _fail(f"{label} is not an observed Git identity")
    return value


def _identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _fail(f"{label} is not a safe identifier")
    return value


def _canonical_bytes(value: Any, *, label: str) -> bytes:
    try:
        return canonical_json(value).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise TaskServiceObservationError(f"{label} is not canonical JSON") from error


def _closed(value: Any, keys: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(f"{label} fields are not closed")
    return value


def _safe_relative_reference(value: Any) -> dict[str, Any]:
    reference = _closed(
        value,
        {"relative_path", "byte_size", "sha256", "media_type"},
        label="task service source",
    )
    relative = reference["relative_path"]
    windows = PureWindowsPath(relative) if isinstance(relative, str) else None
    raw_parts = relative.split("/") if isinstance(relative, str) else []
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or PurePosixPath(relative).is_absolute()
        or any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts)
        or (windows is not None and bool(windows.drive))
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        _fail("task service source path is unsafe")
    size = reference["byte_size"]
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 64 * 1024 * 1024:
        _fail("task service source byte size is invalid")
    _digest(reference["sha256"], label="task service source")
    if reference["media_type"] != "application/json":
        _fail("task service source media type is invalid")
    return dict(reference)


def task_result_service_source(value: Any) -> Mapping[str, Any] | None:
    """Return only the closed nested service ref for the current subartifact.

    Historical task-result-v1 and non-source task results intentionally return
    ``None``.  This helper does not follow nested refs or inspect their bytes;
    callers that admit a matching ref must reopen it through their own bounded,
    path-safe source reader.
    """

    if not isinstance(value, Mapping):
        return None
    if (
        value.get("artifact_kind") != "task_result"
        or value.get("schema_version") != TASK_RESULT_SCHEMA_VERSION
        or value.get("task_case") not in TASK_CASES
    ):
        return None
    if "service_source" not in value:
        _fail("current source-backed task result omits service_source")
    return _safe_relative_reference(value["service_source"])


def _copy_without_fields(value: Any, *, drop: frozenset[str], label: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(f"{label} field name is invalid")
            lowered = key.casefold()
            if lowered in _FORBIDDEN_KEYS:
                _fail(f"{label} contains a forbidden field")
            if key in drop:
                continue
            result[key] = _copy_without_fields(item, drop=drop, label=label)
        return result
    if isinstance(value, list):
        return [_copy_without_fields(item, drop=drop, label=label) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    _fail(f"{label} contains an unsupported value")


def _bounded_call(call: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "caller",
        "operation",
        "action",
        "request",
        "request_byte_size",
        "request_sha256",
        "response_kind",
        "projection",
        "projection_byte_size",
        "projection_sha256",
    }
    if not required.issubset(call):
        _fail("task service call is incomplete")
    if call["caller"] != _CALLER or call["response_kind"] != "bounded_projection":
        _fail("task service call caller or response kind is invalid")
    operation = call["operation"]
    action = call["action"]
    seam = _SERVICE_CALLS.get((operation, action))
    if seam is None:
        _fail("task service operation/action is not admitted")
    request = _copy_without_fields(
        call["request"], drop=_DROP_REQUEST_FIELDS, label="task service request"
    )
    projection = _copy_without_fields(
        call["projection"], drop=_DROP_PROJECTION_FIELDS, label="task service projection"
    )
    request_bytes = _canonical_bytes(request, label="task service request")
    projection_bytes = _canonical_bytes(projection, label="task service projection")
    observed_request = _canonical_bytes(call["request"], label="observed task service request")
    observed_projection = _canonical_bytes(
        call["projection"], label="observed task service projection"
    )
    if (
        call["request_byte_size"] != len(observed_request)
        or call["request_sha256"] != _sha256(observed_request)
        or call["projection_byte_size"] != len(observed_projection)
        or call["projection_sha256"] != _sha256(observed_projection)
    ):
        _fail("task service call does not bind the driver's observed bytes")
    for part, raw in (("request", observed_request), ("projection", observed_projection)):
        size = call.get(f"observed_{part}_byte_size", len(raw))
        digest = call.get(f"observed_{part}_sha256", _sha256(raw))
        if type(size) is not int or size != len(raw) or digest != _sha256(raw):
            _fail("task service call overrides the driver's observed bytes")
    if any(
        len(raw) > 24_576
        for raw in (request_bytes, projection_bytes, observed_request, observed_projection)
    ):
        _fail("task service call projection exceeds its bound")
    return {
        "operation": operation,
        "action": action,
        "seam": seam,
        "caller": _CALLER,
        "request": request,
        "request_byte_size": len(request_bytes),
        "request_sha256": _sha256(request_bytes),
        "projection": projection,
        "projection_byte_size": len(projection_bytes),
        "projection_sha256": _sha256(projection_bytes),
        "observed_request_byte_size": len(observed_request),
        "observed_request_sha256": _sha256(observed_request),
        "observed_projection_byte_size": len(observed_projection),
        "observed_projection_sha256": _sha256(observed_projection),
    }


def _candidate(value: Any) -> dict[str, str]:
    selected = _closed(
        value,
        {"commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"},
        label="candidate binding",
    )
    return {
        "commit": _git(selected["commit"], label="candidate commit"),
        "tree": _git(selected["tree"], label="candidate tree"),
        "lock_sha256": _digest(selected["lock_sha256"], label="candidate lock"),
        "wheel_sha256": _digest(selected["wheel_sha256"], label="candidate wheel"),
        "sdist_sha256": _digest(selected["sdist_sha256"], label="candidate sdist"),
    }


def _native_binding(
    event: Mapping[str, Any],
    *,
    run_id: str,
    workflow_run_id: int,
    candidate_binding: Mapping[str, Any],
    host: str,
    task_case: str,
    event_index: int,
) -> dict[str, Any]:
    parsed = parse_native_host_event(event)
    if parsed["schema_version"] != "deeplaw.native-host-event/v3":
        _fail("task service association requires native-v3 event")
    if parsed["host"] != host or parsed["event_type"] != _MESSAGE_EVENTS[host]:
        _fail("task service association requires the bound native message event")
    sequence = parsed["event_sequence"]
    if sequence.get("index") != event_index:
        _fail("task service event index differs from the native event")
    route = parsed.get("route")
    if not isinstance(route, Mapping) or route.get("status") != "exact":
        _fail("task service association requires an exact native route")
    route_fields = {
        "status",
        "binding_sha256",
        "task_handle_sha256",
        "project_sha256",
        "repository_sha256",
        "worktree_sha256",
    }
    if set(route) != route_fields:
        _fail("native route binding is incomplete")
    for field in route_fields - {"status"}:
        _digest(route[field], label=f"native route {field}")
    derive_native_host_receipt(parsed)
    identity = parsed.get("host_identity")
    if not isinstance(identity, Mapping):
        _fail("native Host identity is unavailable")
    identity_sha256 = _sha256(_canonical_bytes(identity, label="native Host identity"))
    return {
        "run_id": _identifier(run_id, label="service run id"),
        "workflow_run_id": (
            workflow_run_id
            if type(workflow_run_id) is int and workflow_run_id >= 1
            else _fail("service workflow run id is invalid")
        ),
        "candidate_binding": _candidate(candidate_binding),
        "host": host,
        "task_case": task_case,
        "event_index": event_index,
        "event_sha256": _sha256(_canonical_bytes(parsed, label="native event")),
        "session_sha256": _digest(parsed["session_sha256"], label="native session"),
        "route": dict(route),
        "host_identity_sha256": identity_sha256,
        "host_consumption_proven": False,
    }


def _seed_projection(seed: Mapping[str, Any], *, task_case: str) -> dict[str, Any]:
    if seed.get("task_case") != task_case:
        _fail("task service task case differs from frozen seed")
    if seed.get("formal_admission") is not False or seed.get("claim_eligible") is not False:
        _fail("task service seed is not development-only")
    expected = seed.get("expected")
    if not isinstance(expected, Mapping):
        _fail("task service seed expectations are unavailable")
    include = expected.get("include")
    duties = expected.get("duties")
    if not isinstance(include, Mapping) or not isinstance(duties, list) or not duties:
        _fail("task service seed identity is unavailable")
    identity_keys = {
        "knowledge_id",
        "knowledge_revision_id",
        "source_id",
        "source_revision_id",
        "fragment_id",
        "locator",
        "quote_sha256",
        "content_sha256",
        "authority",
        "legal_authority",
        "verification",
    }
    if set(include) != identity_keys:
        _fail("task service seed identity is not closed")
    return {
        "task_case": task_case,
        "scope": seed.get("scope"),
        "max_sensitivity": seed.get("max_sensitivity"),
        "include": dict(include),
        "duties": list(duties),
    }


def collect_task_service_observation(
    seed: Mapping[str, Any],
    *,
    vault: str | Path,
    native_event: Mapping[str, Any] | bytes | bytearray | str,
    run_id: str,
    workflow_run_id: int,
    candidate_binding: Mapping[str, Any],
    host: str,
    task_case: str,
    event_index: int,
    deeplaw_executable: str = "deeplaw",
    deeplaw_prefix: Sequence[str] = (),
) -> dict[str, Any]:
    """Run the existing S075 driver and retain only bounded service facts."""

    if host not in _MESSAGE_EVENTS or task_case not in TASK_CASES:
        _fail("task service Host or task case is unsupported")
    if type(event_index) is not int or event_index < 0:
        _fail("task service event index is invalid")
    binding = _native_binding(
        native_event,
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        candidate_binding=candidate_binding,
        host=host,
        task_case=task_case,
        event_index=event_index,
    )
    seed_projection = _seed_projection(seed, task_case=task_case)
    # Keep this import lazy: the task driver imports the Host qualification
    # catalog, whose parser may inspect this module's reference helper.
    from benchmarks.hosts.v013_task_domain_driver import collect_task_domain

    try:
        report = collect_task_domain(
            seed,
            vault=vault,
            deeplaw_executable=deeplaw_executable,
            deeplaw_prefix=deeplaw_prefix,
        )
    except Exception as error:
        if isinstance(error, TaskServiceObservationError):
            raise
        raise TaskServiceObservationError("task-domain driver execution failed") from error
    if (
        not isinstance(report, Mapping)
        or report.get("status") != "executed"
        or report.get("formal_admission") is not False
        or report.get("claim_eligible") is not False
        or report.get("caller") != _CALLER
        or report.get("task_case") != task_case
    ):
        _fail("task-domain driver report is not an executed development read")
    observations = report.get("observations")
    if not isinstance(observations, Mapping):
        _fail("task-domain driver observations are unavailable")
    records: list[dict[str, Any]] = []
    for observation_name in ("source_read", "wiki_read", "query", "context"):
        observation = observations.get(observation_name)
        if not isinstance(observation, Mapping) or not isinstance(observation.get("calls"), list):
            _fail(f"task-domain driver {observation_name} calls are unavailable")
        for call in observation["calls"]:
            if not isinstance(call, Mapping):
                _fail("task-domain driver call is not an object")
            records.append(_bounded_call(call))
    if not records:
        _fail("task-domain driver retained no service calls")
    keys = [(record["operation"], record["action"]) for record in records]
    if len(keys) != len(set(keys)):
        _fail("task-domain driver service calls contain duplicates")
    seams = sorted({record["seam"] for record in records})
    required = {"source_read", "wiki_read", "query_context"}
    if task_case == "professional_evidence":
        required.add("fragment_read")
    if not required.issubset(seams):
        _fail("task-domain driver did not execute the required source-backed reads")
    executed_duties = (
        {"wiki_exact_source_drill_down"}
        if task_case == "living_wiki"
        else {
            "original_bytes",
            "original_hash",
            "fragment",
            "locator",
            "wiki_exact_source_drill_down",
        }
    )
    all_duties = seed_projection["duties"]
    if any(duty not in all_duties for duty in executed_duties):
        _fail("task-domain driver duty is not in the frozen seed")
    audit = observations.get("ledger")
    if not isinstance(audit, Mapping):
        _fail("task-domain driver audit-head facts are unavailable")
    audit_keys = {
        "before_audit_head",
        "after_audit_head",
        "before_legacy_audit_head",
        "after_legacy_audit_head",
        "unchanged",
    }
    if set(audit) != audit_keys or not isinstance(audit["unchanged"], bool):
        _fail("task-domain driver audit-head facts are not closed")
    for field in audit_keys - {"unchanged"}:
        _digest(audit[field], label=f"audit-head fact {field}")
    if (
        audit["unchanged"] is not True
        or audit["before_audit_head"] != audit["after_audit_head"]
        or audit["before_legacy_audit_head"] != audit["after_legacy_audit_head"]
    ):
        _fail("task-domain driver changed the audit identity")
    return {
        "schema_version": SERVICE_SCHEMA_VERSION,
        "status": "executed",
        "formal_admission": False,
        "claim_eligible": False,
        "caller": _CALLER,
        "driver_kind": _CALLER,
        "task_case": task_case,
        "seed": seed_projection,
        "executed_operations": seams,
        "executed_duties": sorted(executed_duties),
        "not_executed_duties": sorted(set(all_duties) - executed_duties),
        "observed_public_seams": seams,
        "service_calls": records,
        "audit_head_facts": dict(audit),
        "native_event_binding": binding,
    }


__all__ = [
    "SERVICE_SCHEMA_VERSION",
    "TASK_RESULT_SCHEMA_VERSION",
    "TaskServiceObservationError",
    "collect_task_service_observation",
    "task_result_service_source",
]

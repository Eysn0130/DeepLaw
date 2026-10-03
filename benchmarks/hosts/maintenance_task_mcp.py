"""A bounded stdio MCP adapter for the synthetic maintenance task cases.

This module owns only the transport, owner binding, and durable trace seam.
The action rules remain in :mod:`maintenance_task_cases`; no action is chosen
or executed on behalf of a Host.  The only advertised tool is
``maintenance_task``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

import anyio
import jsonschema
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import maintenance_task_cases as cases

BINDING_SCHEMA_VERSION = "deeplaw.host-maintenance-binding/v2"
TRACE_SCHEMA_VERSION = "deeplaw.host-maintenance-trace/v3"
INPUT_SCHEMA_VERSION = "deeplaw.host-maintenance-mcp-input/v2"
OUTPUT_SCHEMA_VERSION = "deeplaw.host-maintenance-mcp-output/v2"
MAX_INPUT_BYTES = 16_384
MAX_OUTPUT_BYTES = 131_072
MAX_TRACE_BYTES = 262_144
MAX_PROVIDER_CAPSULE_BYTES = 65_536
MAX_TOOL_ERROR_ATTEMPTS = 16
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ZERO_SHA256 = "0" * 64


class MaintenanceMCPError(ValueError):
    """Raised when the owner binding, MCP envelope, or trace is invalid."""


class MaintenanceRunClaimError(MaintenanceMCPError):
    """Raised when a trace path already claims a run/candidate binding."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _contains_evaluator_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str) and any(
                marker in key.casefold()
                for marker in ("_private_cases", "expected", "oracle", "gold", "score")
            ):
                return True
            if _contains_evaluator_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_evaluator_key(item) for item in value)
    return False


def _public_capsule(value: Any) -> Any:
    """Validate a supplied public capsule without inspecting DeepLaw state."""

    try:
        encoded = _canonical(value).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise MaintenanceMCPError("provider capsule is not canonicalizable") from exc
    if len(encoded) > MAX_PROVIDER_CAPSULE_BYTES:
        raise MaintenanceMCPError("provider capsule exceeds byte budget")
    if not isinstance(value, Mapping):
        raise MaintenanceMCPError("provider capsule must be an object")
    if _contains_evaluator_key(value):
        raise MaintenanceMCPError("provider capsule contains evaluator metadata")
    return _copy(dict(value))


def provider_capsule_digest(provider_capsule: Mapping[str, Any]) -> str:
    capsule = _public_capsule(provider_capsule)
    return _sha256_json(capsule)


capsule_digest = provider_capsule_digest


def _read_provider_capsule_file(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise MaintenanceMCPError("provider capsule file cannot be read") from exc
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode) or file_stat.st_mode & 0o077:
        raise MaintenanceMCPError("provider capsule file must be owner-only regular file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MaintenanceMCPError("provider capsule file cannot be read") from exc
    if len(raw) > MAX_PROVIDER_CAPSULE_BYTES:
        raise MaintenanceMCPError("provider capsule file exceeds byte budget")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaintenanceMCPError("provider capsule file JSON is invalid") from exc
    checked = _public_capsule(value)
    if not isinstance(checked, dict):
        raise MaintenanceMCPError("provider capsule must be an object")
    return checked


def _validate_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise MaintenanceMCPError(f"invalid {field}")
    return value


def _binding_core(binding: Mapping[str, Any]) -> dict[str, str]:
    return {
        field: _validate_id(binding.get(field), field)
        for field in (
            "configuration_id",
            "scenario_id",
            "run_id",
            "candidate_id",
            "context_id",
            "capsule_digest",
        )
    }


def make_owner_binding(
    configuration_id: str,
    scenario_id: str,
    *,
    run_id: str,
    candidate_id: str,
    context_id: str,
    capsule_digest: str,
) -> dict[str, str]:
    """Create the explicit owner binding required before the server starts."""

    core = {
        "configuration_id": configuration_id,
        "scenario_id": scenario_id,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "context_id": context_id,
        "capsule_digest": capsule_digest,
    }
    try:
        cases.open_task(configuration_id, scenario_id)
    except cases.MaintenanceCaseError as exc:
        raise MaintenanceMCPError(str(exc)) from exc
    core = {
        field: _validate_id(value, field)
        for field, value in core.items()
        if field != "capsule_digest"
    }
    if not isinstance(capsule_digest, str) or not _SHA256.fullmatch(capsule_digest):
        raise MaintenanceMCPError("invalid capsule_digest")
    core["capsule_digest"] = capsule_digest
    return {
        **core,
        "binding_sha256": _sha256_json(core),
    }


def _validate_binding(binding: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(binding, Mapping):
        raise MaintenanceMCPError("owner binding must be an object")
    required = {
        "configuration_id",
        "scenario_id",
        "run_id",
        "candidate_id",
        "context_id",
        "capsule_digest",
        "binding_sha256",
    }
    if set(binding) != required:
        raise MaintenanceMCPError("owner binding fields are invalid")
    core = _binding_core(binding)
    try:
        cases.open_task(core["configuration_id"], core["scenario_id"])
    except cases.MaintenanceCaseError as exc:
        raise MaintenanceMCPError(str(exc)) from exc
    digest = binding.get("binding_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise MaintenanceMCPError("invalid binding_sha256")
    if digest != _sha256_json(core):
        raise MaintenanceMCPError("owner binding digest does not match fields")
    return {**core, "binding_sha256": digest}


def _trace_action(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _copy(event[key])
        for key in (
            "action_id",
            "observed_state_sha256",
            "kind",
            "parameters",
        )
    }


def _trace_payload(
    binding: Mapping[str, str], session: cases.MaintenanceTaskSession,
    *, tool_errors: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    events = session.trace
    head = events[-1]["event_sha256"] if events else _ZERO_SHA256
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "binding_schema_version": BINDING_SCHEMA_VERSION,
        "binding": _copy(dict(binding)),
        "task": session.public_task,
        "actions": [_trace_action(event) for event in events],
        "events": events,
        "state": session.snapshot(),
        "state_sha256": session.state_sha256,
        "event_count": len(events),
        "trace_head_sha256": head,
        "tool_errors": [_copy(dict(error)) for error in tool_errors],
    }


def _lifecycle_by_id(state: Mapping[str, Any]) -> dict[str, str]:
    experience = state.get("experience")
    if not isinstance(experience, Mapping):
        return {}
    active = experience.get("available_ids", [])
    forgotten = experience.get("forgotten_ids", [])
    return {
        **{str(experience_id): "active" for experience_id in active},
        **{str(experience_id): "forgotten" for experience_id in forgotten},
    }


def _provider_task_projection(task: Mapping[str, Any], *, state_sha256: str) -> dict[str, Any]:
    projection = _copy(dict(task))
    environment = projection.get("environment")
    if not isinstance(environment, dict):
        raise MaintenanceMCPError("public task environment is invalid")
    experience = environment.get("experience")
    if not isinstance(experience, dict):
        raise MaintenanceMCPError("public task experience state is invalid")
    records = experience.pop("records", None)
    if records is not None and not isinstance(records, list):
        raise MaintenanceMCPError("public task experience records are invalid")
    lifecycle_by_id = {
        **{str(experience_id): "active" for experience_id in experience.get("available_ids", [])},
        **{
            str(experience_id): "forgotten" for experience_id in experience.get("forgotten_ids", [])
        },
    }
    experience["records"] = {"omitted_from_environment": True}
    experience["lifecycle_by_id"] = lifecycle_by_id
    experience["state_sha256"] = state_sha256
    experience["state_hash_scope"] = "owner_trace_full_state_reference"
    projection["projection"] = "provider_task_projection"
    projection["input_hash_scope"] = "owner_trace_full_task_reference"
    projection["input_hash_verified"] = False
    return projection


def _provider_state_projection(full_state: Mapping[str, Any]) -> dict[str, Any]:
    experience = full_state.get("experience")
    if not isinstance(experience, Mapping):
        raise MaintenanceMCPError("session experience state is invalid")
    return {
        "projection": "provider_state_projection",
        "state_hash_verified": False,
        "state_revision": full_state.get("state_revision"),
        "resource": _copy(full_state.get("resource")),
        "report": _copy(full_state.get("report")),
        "independent_support": _copy(full_state.get("independent_support")),
        "experience": {
            "mode": experience.get("mode"),
            "available_ids": _copy(experience.get("available_ids", [])),
            "forgotten_ids": _copy(experience.get("forgotten_ids", [])),
            "lifecycle_by_id": _lifecycle_by_id(full_state),
            "records": {"omitted_from_environment": True},
            "state_sha256": full_state.get("state_sha256"),
            "state_hash_scope": "owner_trace_full_state_reference",
            "state_hash_verified": False,
        },
        "state_sha256": full_state.get("state_sha256"),
        "state_hash_scope": "owner_trace_full_state_reference",
    }


def _provider_knowledge_context(
    provider_capsule: Mapping[str, Any], capsule_digest_value: str
) -> dict[str, Any]:
    capsule = _public_capsule(provider_capsule)
    encoded = _canonical(capsule).encode("utf-8")
    return {
        "projection": "provided_provider_capsule",
        "capsule": capsule,
        "capsule_digest": capsule_digest_value,
        "digest_verified_against_owner_binding": True,
        "capsule_bytes": len(encoded),
        "digest_scope": "canonical_provider_capsule_bytes",
    }


def _write_atomic(path: Path, payload: Mapping[str, Any], *, create: bool) -> None:
    encoded = _canonical(payload).encode("utf-8")
    if len(encoded) > MAX_TRACE_BYTES:
        raise MaintenanceMCPError("trace exceeds byte budget")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise MaintenanceRunClaimError("trace path already claims a run binding") from exc
            os.chmod(path, 0o600)
        else:
            os.replace(temporary, path)
            os.chmod(path, 0o600)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


class _TraceStore:
    def __init__(
        self,
        path: str | Path,
        binding: Mapping[str, str],
        session: cases.MaintenanceTaskSession,
    ) -> None:
        self.path = Path(path).expanduser()
        if self.path.name in {"", ".", ".."}:
            raise MaintenanceMCPError("trace path is invalid")
        self.binding = _copy(dict(binding))
        self.tool_errors: list[dict[str, str]] = []
        payload = _trace_payload(self.binding, session, tool_errors=self.tool_errors)
        _write_atomic(self.path, payload, create=True)

    def persist(self, session: cases.MaintenanceTaskSession) -> None:
        chain = cases.verify_event_chain(session.trace)
        if chain["valid"] is not True:
            raise MaintenanceMCPError("session event chain is invalid")
        _write_atomic(
            self.path,
            _trace_payload(self.binding, session, tool_errors=self.tool_errors),
            create=False,
        )

    def record_tool_error(
        self,
        session: cases.MaintenanceTaskSession,
        *,
        code: str,
        request_sha256: str,
    ) -> None:
        if len(self.tool_errors) >= MAX_TOOL_ERROR_ATTEMPTS:
            raise MaintenanceMCPError("tool error attempt budget exceeded")
        if not isinstance(code, str) or not code:
            raise MaintenanceMCPError("tool error code is invalid")
        if not isinstance(request_sha256, str) or not _SHA256.fullmatch(request_sha256):
            raise MaintenanceMCPError("tool error request digest is invalid")
        self.tool_errors.append({
            "attempt_ordinal": len(self.tool_errors) + 1,
            "code": code,
            "request_sha256": request_sha256,
        })
        self.persist(session)


def _request_sha256(value: Any) -> str:
    try:
        encoded = _canonical(value).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise MaintenanceMCPError("tool request is not canonicalizable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _validate_persisted_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MaintenanceMCPError("persisted trace must be an object")
    required = {
        "schema_version",
        "binding_schema_version",
        "binding",
        "task",
        "actions",
        "events",
        "state",
        "state_sha256",
        "event_count",
        "trace_head_sha256",
        "tool_errors",
    }
    if set(payload) != required:
        raise MaintenanceMCPError("persisted trace fields are invalid")
    if payload.get("schema_version") != TRACE_SCHEMA_VERSION:
        raise MaintenanceMCPError("persisted trace schema is invalid")
    if payload.get("binding_schema_version") != BINDING_SCHEMA_VERSION:
        raise MaintenanceMCPError("persisted binding schema is invalid")
    binding = _validate_binding(payload["binding"])
    events = payload["events"]
    if not isinstance(events, list):
        raise MaintenanceMCPError("persisted events must be a list")
    chain = cases.verify_event_chain(events)
    if chain["valid"] is not True:
        raise MaintenanceMCPError("persisted event chain is invalid")
    if payload.get("event_count") != len(events):
        raise MaintenanceMCPError("persisted event count is invalid")
    head = events[-1]["event_sha256"] if events else _ZERO_SHA256
    if payload.get("trace_head_sha256") != head:
        raise MaintenanceMCPError("persisted trace head is invalid")
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise MaintenanceMCPError("persisted actions must be a list")
    expected_actions = [_trace_action(event) for event in events]
    if actions != expected_actions:
        raise MaintenanceMCPError("persisted actions do not match events")
    tool_errors = payload.get("tool_errors")
    if not isinstance(tool_errors, list) or len(tool_errors) > MAX_TOOL_ERROR_ATTEMPTS:
        raise MaintenanceMCPError("persisted tool error attempts are invalid")
    for ordinal, error in enumerate(tool_errors, start=1):
        if not isinstance(error, Mapping) or set(error) != {
            "attempt_ordinal", "code", "request_sha256",
        }:
            raise MaintenanceMCPError("persisted tool error entry is invalid")
        if error.get("attempt_ordinal") != ordinal:
            raise MaintenanceMCPError("persisted tool error ordinal is invalid")
        if not isinstance(error.get("code"), str) or not error["code"]:
            raise MaintenanceMCPError("persisted tool error code is invalid")
        if not isinstance(error.get("request_sha256"), str) \
                or not _SHA256.fullmatch(error["request_sha256"]):
            raise MaintenanceMCPError("persisted tool error digest is invalid")
    if tool_errors:
        raise MaintenanceMCPError("persisted trace contains tool errors")
    state = payload.get("state")
    if not isinstance(state, Mapping):
        raise MaintenanceMCPError("persisted state is invalid")
    state_body = _copy(dict(state))
    claimed_state = state_body.pop("state_sha256", None)
    if claimed_state != payload.get("state_sha256"):
        raise MaintenanceMCPError("persisted state digest is inconsistent")
    if _sha256_json(state_body) != claimed_state:
        raise MaintenanceMCPError("persisted state digest does not match state")
    if events and events[-1].get("state_sha256") != claimed_state:
        raise MaintenanceMCPError("persisted state is not the event state")
    return {
        **_copy(dict(payload)),
        "binding": binding,
    }


def load_persisted_trace(
    trace_path: str | Path, *, binding: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Load a closed trace and independently verify its binding and hashes."""

    path = Path(trace_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaintenanceMCPError("persisted trace cannot be read") from exc
    checked = _validate_persisted_payload(payload)
    if binding is not None and checked["binding"] != _validate_binding(binding):
        raise MaintenanceMCPError("persisted trace binding does not match owner binding")
    return checked


def verify_persisted_trace(
    trace_path: str | Path, *, binding: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    try:
        payload = load_persisted_trace(trace_path, binding=binding)
    except MaintenanceMCPError as exc:
        return {"valid": False, "errors": [str(exc)]}
    return {
        "valid": True,
        "errors": [],
        "event_count": payload["event_count"],
        "trace_head_sha256": payload["trace_head_sha256"],
        "state_sha256": payload["state_sha256"],
    }


def _action_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "action_id",
            "observed_state_sha256",
            "kind",
            "parameters",
        ],
        "properties": {
            "action_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "observed_state_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "kind": {"type": "string", "minLength": 1, "maxLength": 80},
            "parameters": {
                "type": "object",
                "maxProperties": 8,
                "additionalProperties": True,
            },
        },
    }


def maintenance_task_input_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": INPUT_SCHEMA_VERSION,
        "title": "DeepLaw Maintenance Task Host Input v2",
        "type": "object",
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"operation": {"const": "describe"}},
                "required": ["operation"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "operation": {"const": "submit"},
                    "action": _action_schema(),
                },
                "required": ["operation", "action"],
            },
        ],
    }


def maintenance_task_output_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": OUTPUT_SCHEMA_VERSION,
        "title": "DeepLaw Maintenance Task Host Output v2",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "operation",
            "task",
            "state",
            "state_sha256",
            "result",
            "knowledge_context",
            "trace",
        ],
        "properties": {
            "schema_version": {"const": OUTPUT_SCHEMA_VERSION},
            "operation": {"enum": ["describe", "submit"]},
            "task": {"type": "object"},
            "state": {"type": "object"},
            "state_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "result": {"type": "object"},
            "knowledge_context": {"type": "object"},
            "trace": {
                "type": "object",
                "additionalProperties": False,
                "required": ["event_count", "head_sha256"],
                "properties": {
                    "event_count": {"type": "integer", "minimum": 0, "maximum": 3},
                    "head_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                },
            },
        },
    }


def maintenance_task_tool_definition() -> types.Tool:
    return types.Tool(
        name="maintenance_task",
        description=(
            "Submit one bounded action for the owner-selected synthetic maintenance task. "
            "Use describe to inspect the public task and state before submitting an action. "
            "This tool has no knowledge query or provider operation."
        ),
        inputSchema=maintenance_task_input_schema(),
        outputSchema=maintenance_task_output_schema(),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )


@dataclass
class _Runtime:
    binding: dict[str, str]
    session: cases.MaintenanceTaskSession
    trace_store: _TraceStore
    provider_capsule: dict[str, Any]
    lock: RLock


def _response(
    runtime: _Runtime,
    *,
    operation: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    events = runtime.session.trace
    full_state = runtime.session.snapshot()
    response = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "operation": operation,
        "task": _provider_task_projection(
            runtime.session.public_task,
            state_sha256=full_state["state_sha256"],
        ),
        "state": _provider_state_projection(full_state),
        "state_sha256": full_state["state_sha256"],
        "result": _copy(dict(result)),
        "knowledge_context": _provider_knowledge_context(
            runtime.provider_capsule,
            runtime.binding["capsule_digest"],
        ),
        "trace": {
            "event_count": len(events),
            "head_sha256": events[-1]["event_sha256"] if events else _ZERO_SHA256,
        },
    }
    encoded = _canonical(response).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise MaintenanceMCPError("MCP output exceeds byte budget")
    return response


def create_mcp_server(
    binding: Mapping[str, Any],
    *,
    trace_path: str | Path,
    provider_capsule: Mapping[str, Any],
    session: cases.MaintenanceTaskSession | None = None,
) -> Server[_Runtime]:
    """Create the bound server; creation claims the trace path exactly once."""

    checked_binding = _validate_binding(binding)
    checked_capsule = _public_capsule(provider_capsule)
    if checked_binding["capsule_digest"] != _sha256_json(checked_capsule):
        raise MaintenanceMCPError("provider capsule digest does not match owner binding")
    selected_session = session or cases.open_task(
        checked_binding["configuration_id"], checked_binding["scenario_id"]
    )
    if (
        selected_session.configuration_id != checked_binding["configuration_id"]
        or selected_session.scenario_id != checked_binding["scenario_id"]
    ):
        raise MaintenanceMCPError("session does not match owner binding")
    runtime = _Runtime(
        binding=checked_binding,
        session=selected_session,
        trace_store=_TraceStore(trace_path, checked_binding, selected_session),
        provider_capsule=checked_capsule,
        lock=RLock(),
    )

    @asynccontextmanager
    async def lifespan(_: Server[_Runtime]) -> AsyncIterator[_Runtime]:
        yield runtime

    server: Server[_Runtime] = Server(
        "DeepLaw Maintenance Task",
        instructions=(
            "This bounded local task server exposes one synthetic maintenance_task tool. "
            "Inspect the public task/state and submit only an explicit Host action."
        ),
        lifespan=lifespan,
    )
    definition = maintenance_task_tool_definition()

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [definition]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with runtime.lock:
            request_sha256 = _request_sha256(arguments)

            def reject(code: str, message: str) -> None:
                runtime.trace_store.record_tool_error(
                    runtime.session, code=code, request_sha256=request_sha256,
                )
                raise MaintenanceMCPError(message)

            if name != "maintenance_task":
                reject("unknown_tool", "unknown maintenance task tool")
            if not isinstance(arguments, Mapping):
                reject("input_not_object", "maintenance_task input must be an object")
            try:
                input_size = len(_canonical(dict(arguments)).encode("utf-8"))
            except (TypeError, ValueError, OverflowError):
                reject("input_not_canonicalizable", "maintenance_task input is invalid")
            if input_size > MAX_INPUT_BYTES:
                reject("input_exceeds_byte_budget", "MCP input exceeds byte budget")
            try:
                jsonschema.validate(
                    instance=dict(arguments), schema=definition.inputSchema,
                )
            except jsonschema.ValidationError:
                reject("input_schema_invalid", "maintenance_task input does not match schema")
            operation = arguments.get("operation")
            if operation == "describe":
                return _response(
                    runtime,
                    operation="describe",
                    result={
                        "status": "ready",
                        "result_code": "task_available",
                        "retry_allowed": False,
                        "state_mutated": False,
                    },
                )
            if operation != "submit":
                reject("unknown_operation", "unknown maintenance task operation")
            action = arguments.get("action")
            if not isinstance(action, Mapping):
                reject("submit_action_invalid", "submit action must be an object")
            try:
                event = runtime.session.submit(action)
            except cases.MaintenanceActionError:
                runtime.trace_store.record_tool_error(
                    runtime.session, code="action_rejected", request_sha256=request_sha256,
                )
                raise MaintenanceMCPError("maintenance action rejected") from None
            runtime.trace_store.persist(runtime.session)
            return _response(
                runtime,
                operation="submit",
                result={
                    **_copy(event["typed_result"]),
                    "event_ordinal": event["event_ordinal"],
                },
            )

    return server


def run_mcp(
    binding: Mapping[str, Any],
    *,
    trace_path: str | Path,
    provider_capsule: Mapping[str, Any],
    transport: str = "stdio",
) -> None:
    if transport != "stdio":
        raise ValueError("maintenance task MCP supports only local stdio")

    async def serve() -> None:
        server = create_mcp_server(
            binding,
            trace_path=trace_path,
            provider_capsule=provider_capsule,
        )
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(serve)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the bounded maintenance task MCP server")
    parser.add_argument("--configuration-id", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--capsule-digest", required=True)
    parser.add_argument("--provider-capsule-file", required=True)
    parser.add_argument("--binding-sha256", required=True)
    parser.add_argument("--trace-path", required=True)
    parser.add_argument("--stdio", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    provider_capsule = _read_provider_capsule_file(args.provider_capsule_file)
    binding = make_owner_binding(
        args.configuration_id,
        args.scenario_id,
        run_id=args.run_id,
        candidate_id=args.candidate_id,
        context_id=args.context_id,
        capsule_digest=args.capsule_digest,
    )
    if args.binding_sha256 != binding["binding_sha256"]:
        raise MaintenanceMCPError("binding digest does not match owner arguments")
    if not args.stdio:
        raise ValueError("--stdio is required")
    run_mcp(
        binding,
        trace_path=args.trace_path,
        provider_capsule=provider_capsule,
    )


if __name__ == "__main__":
    main()


__all__ = [
    "BINDING_SCHEMA_VERSION",
    "INPUT_SCHEMA_VERSION",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_PROVIDER_CAPSULE_BYTES",
    "MAX_TOOL_ERROR_ATTEMPTS",
    "MAX_TRACE_BYTES",
    "OUTPUT_SCHEMA_VERSION",
    "TRACE_SCHEMA_VERSION",
    "MaintenanceMCPError",
    "MaintenanceRunClaimError",
    "capsule_digest",
    "create_mcp_server",
    "load_persisted_trace",
    "main",
    "maintenance_task_input_schema",
    "maintenance_task_output_schema",
    "maintenance_task_tool_definition",
    "make_owner_binding",
    "provider_capsule_digest",
    "run_mcp",
    "verify_persisted_trace",
]

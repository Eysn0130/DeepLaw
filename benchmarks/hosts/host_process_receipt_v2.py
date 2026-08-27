"""Validate the closed structure and cross-bindings of Host process receipts v2.

The receipt is a sibling of the frozen v1 control record.  It accepts only
content-minimized digests and closed control metadata.  Native Host v3 events
remain non-authoritative inputs whose aggregate digests are cross-bound by the
Kernel bundle validator; this module does not promote them into Host identity.
Neither serializer nor validator can establish formal observation authority.
That authority requires a future exact external per-Host broker plus formal
workflow provenance at the target observation seam.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, MutableSet
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError, ValidationError

SCHEMA_VERSION = "deeplaw.host-process-receipt/v2"
SCHEMA_FILENAME = "host-process-receipt.v2.schema.json"
AUTHORITY_CLASS = "owner_external_host_process_observation"
PROOF_SOURCE = "owner_external_broker_direct_observation"
MAX_RECEIPT_LIFETIME_SECONDS = 300

HOSTS = frozenset({"codex", "opencode"})
TASK_CASES = frozenset({"continuity", "living_wiki", "professional_evidence"})
CANDIDATE_FIELDS = (
    "commit",
    "tree",
    "lock_sha256",
    "wheel_sha256",
    "sdist_sha256",
)
RUN_BINDING_FIELDS = ("evidence_run_id", "qualification_run_id")
NATIVE_EVENT_FIELDS = (
    "event_sequence_sha256",
    "session_identity_sha256",
    "lifecycle_record_sha256",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s\"'=:(])/(?!/)[^\s\"'=;:)]+|"
    r"(?:^|[\s\"'=:(])[A-Za-z]:[\\/]"
)
_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:path|command|argv|env|stdout|stderr|output|prompt|transcript|"
    r"reasoning|secret|auth|credential|token|pid)(?:_|$)",
    re.IGNORECASE,
)
_SAFE_FORBIDDEN_KEYS = frozenset(
    {
        "runner_received_secret",
        "mcp_received_secret",
        "ambient_auth_forwarded_to_mcp",
        "raw_output_retained",
    }
)
_FIXED_SYNTHETIC_FORK_REQUEST_SHA256 = hashlib.sha256(
    json.dumps(
        {"operation": "session.fork"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()


class HostProcessReceiptV2Error(ValueError):
    """A v2 receipt is unsafe, incomplete, replayed, or cross-bound incorrectly."""


def _fail(message: str) -> None:
    raise HostProcessReceiptV2Error(message)


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts" / SCHEMA_FILENAME


def canonical_json(value: Any) -> bytes:
    """Return canonical UTF-8 JSON bytes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise HostProcessReceiptV2Error("receipt is not canonical JSON") from error


def record_sha256(value: Mapping[str, Any]) -> str:
    """Hash the canonical receipt body with its self-digest excluded."""

    if not isinstance(value, Mapping):
        _fail("receipt must be an object")
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def correlation_sha256(value: Mapping[str, Any]) -> str:
    """Hash one closed, value-minimized correlation projection."""

    if not isinstance(value, Mapping):
        _fail("correlation projection must be an object")
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _scan_safe(value: Any, *, key: str | None = None) -> None:
    if key is not None:
        if not isinstance(key, str):
            _fail("receipt field name is invalid")
        if (
            key not in _SAFE_FORBIDDEN_KEYS
            and not key.endswith("_sha256")
            and _FORBIDDEN_KEY.search(key)
        ):
            _fail("receipt contains a forbidden field")
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            _scan_safe(nested_value, key=nested_key)
    elif isinstance(value, list):
        for item in value:
            _scan_safe(item)
    elif isinstance(value, str) and _ABSOLUTE_PATH.search(value):
        _fail("receipt contains an absolute path")


def _schema_validate(value: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(value))
    except (OSError, UnicodeError, TypeError, ValueError, SchemaError, ValidationError) as error:
        raise HostProcessReceiptV2Error("receipt schema validation failed") from error


def _sha(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or _SHA256.fullmatch(value) is None
        or value == "0" * 64
    ):
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise HostProcessReceiptV2Error(f"{label} must be a UTC timestamp") from error
    return parsed


def _expected_projection(
    value: Mapping[str, Any] | None,
    fields: tuple[str, ...],
    *,
    label: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or any(field not in value for field in fields):
        _fail(f"expected {label} is incomplete")
    return {field: value[field] for field in fields}


def _validate_time_window(value: Mapping[str, Any]) -> None:
    issued = _timestamp(value.get("issued_at"), label="receipt issued_at")
    expires = _timestamp(value.get("expires_at"), label="receipt expires_at")
    reference = _timestamp(
        value.get("validation_reference_time"),
        label="receipt validation_reference_time",
    )
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > MAX_RECEIPT_LIFETIME_SECONDS:
        _fail("receipt lifetime is outside the closed bound")
    if reference < issued:
        _fail("receipt was issued in the future at its bound validation time")
    if reference > expires:
        _fail("receipt was expired at its bound validation time")


def _validate_authority_digests(value: Mapping[str, Any]) -> None:
    authority_digests = {
        _sha(value.get("host_identity_sha256"), label="Host identity"),
        _sha(value.get("host_identity_source_sha256"), label="Host identity source"),
        _sha(value.get("process_identity_sha256"), label="process identity"),
        _sha(value.get("broker_instance_sha256"), label="broker instance"),
        _sha(value.get("nonce_sha256"), label="one-time nonce"),
        _sha(value.get("broker_source", {}).get("sha256"), label="broker source"),
        _sha(value.get("host_binary", {}).get("sha256"), label="Host binary"),
    }
    native_digests = {
        _sha(
            value.get("native_event_binding", {}).get(field),
            label=f"native {field}",
        )
        for field in NATIVE_EVENT_FIELDS
    }
    if len(authority_digests) != 7 or len(native_digests) != 3 or (
        authority_digests & native_digests
    ):
        _fail("Host, process, broker, and nonce identities must remain distinct")


def _validate_codex_proof(value: Mapping[str, Any], proof: Mapping[str, Any]) -> None:
    if proof.get("proof_kind") != "codex_stdio_hook_correlation":
        _fail("Codex proof kind is unsupported")
    if proof.get("process_identity_sha256") != value["process_identity_sha256"]:
        _fail("Codex proof process identity differs")
    native = value["native_event_binding"]
    if any(
        proof.get(proof_field) != native[native_field]
        for proof_field, native_field in (
            ("native_event_sequence_sha256", "event_sequence_sha256"),
            ("native_session_identity_sha256", "session_identity_sha256"),
            ("native_lifecycle_record_sha256", "lifecycle_record_sha256"),
        )
    ):
        _fail("Codex proof native event binding differs")
    if proof.get("initialized_connection_count") != 1:
        _fail("Codex proof did not observe exactly one initialized connection")
    if proof.get("same_process") is not True or proof.get("same_connection") is not True:
        _fail("Codex proof lacks same-process and same-connection correlation")
    correlation = {
        _sha(proof.get("connection_sha256"), label="Codex connection"),
        _sha(proof.get("initialize_request_sha256"), label="Codex initialize request"),
        _sha(
            proof.get("initialized_notification_sha256"),
            label="Codex initialized notification",
        ),
        _sha(proof.get("hook_session_sha256"), label="Codex Hook session"),
        _sha(proof.get("hook_event_sha256"), label="Codex Hook event"),
    }
    if len(correlation) != 5 or correlation & {
        value["host_identity_sha256"],
        value["host_identity_source_sha256"],
        value["process_identity_sha256"],
        value["broker_instance_sha256"],
        value["broker_source"]["sha256"],
        value["nonce_sha256"],
    }:
        _fail("Codex connection and Hook correlation digests are ambiguous")
    expected_correlation = correlation_sha256(
        {
            "process_identity_sha256": value["process_identity_sha256"],
            "connection_sha256": proof["connection_sha256"],
            "initialize_request_sha256": proof["initialize_request_sha256"],
            "initialized_notification_sha256": proof[
                "initialized_notification_sha256"
            ],
            "initialized_connection_count": proof["initialized_connection_count"],
            "hook_session_sha256": proof["hook_session_sha256"],
            "hook_event_sha256": proof["hook_event_sha256"],
            "native_event_sequence_sha256": proof[
                "native_event_sequence_sha256"
            ],
            "native_session_identity_sha256": proof[
                "native_session_identity_sha256"
            ],
            "native_lifecycle_record_sha256": proof[
                "native_lifecycle_record_sha256"
            ],
        }
    )
    if proof.get("connection_correlation_sha256") != expected_correlation:
        _fail("Codex same-connection Hook correlation digest differs")


def _validate_opencode_proof(value: Mapping[str, Any], proof: Mapping[str, Any]) -> None:
    if proof.get("proof_kind") != "opencode_public_fork_route_correlation":
        _fail("OpenCode proof kind is unsupported")
    if proof.get("process_identity_sha256") != value["process_identity_sha256"]:
        _fail("OpenCode proof process identity differs")
    native = value["native_event_binding"]
    if any(
        proof.get(proof_field) != native[native_field]
        for proof_field, native_field in (
            ("native_event_sequence_sha256", "event_sequence_sha256"),
            ("native_session_identity_sha256", "session_identity_sha256"),
            ("native_lifecycle_record_sha256", "lifecycle_record_sha256"),
        )
    ):
        _fail("OpenCode proof native event binding differs")
    if proof.get("request_method") != "POST" or proof.get("actual_route_observed") is not True:
        _fail("OpenCode proof lacks an observed public POST fork route")
    if proof.get("same_process") is not True:
        _fail("OpenCode proof lacks same-process correlation")
    parent = _sha(proof.get("parent_session_sha256"), label="OpenCode parent session")
    child = _sha(proof.get("child_session_sha256"), label="OpenCode child session")
    child_event_session = _sha(
        proof.get("child_plugin_session_sha256"),
        label="OpenCode child plugin session",
    )
    if parent == child or child_event_session != child:
        _fail("OpenCode parent, child, and plugin event correlation differs")
    if _FIXED_SYNTHETIC_FORK_REQUEST_SHA256 in {
        proof.get("route_observation_sha256"),
        proof.get("request_body_sha256"),
    }:
        _fail("OpenCode observation uses the runner synthetic request digest")
    observation_digests = {
        _sha(proof.get("route_observation_sha256"), label="OpenCode route observation"),
        _sha(proof.get("request_body_sha256"), label="OpenCode request body"),
        _sha(proof.get("response_sha256"), label="OpenCode response"),
        _sha(proof.get("child_plugin_event_sha256"), label="OpenCode child plugin event"),
    }
    if len(observation_digests) != 4 or observation_digests & {
        value["host_identity_sha256"],
        value["host_identity_source_sha256"],
        value["process_identity_sha256"],
        value["broker_instance_sha256"],
        value["broker_source"]["sha256"],
        value["nonce_sha256"],
        parent,
        child,
    }:
        _fail("OpenCode route and child event digests are ambiguous")
    expected_correlation = correlation_sha256(
        {
            "process_identity_sha256": value["process_identity_sha256"],
            "request_method": proof["request_method"],
            "route_observation_sha256": proof["route_observation_sha256"],
            "request_body_sha256": proof["request_body_sha256"],
            "response_sha256": proof["response_sha256"],
            "parent_session_sha256": proof["parent_session_sha256"],
            "child_session_sha256": proof["child_session_sha256"],
            "child_plugin_event_sha256": proof["child_plugin_event_sha256"],
            "child_plugin_session_sha256": proof["child_plugin_session_sha256"],
            "native_event_sequence_sha256": proof[
                "native_event_sequence_sha256"
            ],
            "native_session_identity_sha256": proof[
                "native_session_identity_sha256"
            ],
            "native_lifecycle_record_sha256": proof[
                "native_lifecycle_record_sha256"
            ],
        }
    )
    if proof.get("route_correlation_sha256") != expected_correlation:
        _fail("OpenCode actual-route child-event correlation digest differs")


def validate_receipt(
    value: Mapping[str, Any],
    *,
    expected_host: str | None = None,
    expected_task_case: str | None = None,
    expected_run_id: str | None = None,
    expected_candidate: Mapping[str, Any] | None = None,
    expected_run_binding: Mapping[str, Any] | None = None,
    expected_broker_sha256: str | None = None,
    expected_host_identity_sha256: str | None = None,
    expected_host_identity_source_sha256: str | None = None,
    expected_host_binary: Mapping[str, Any] | None = None,
    seen_nonce_sha256s: MutableSet[str],
) -> dict[str, Any]:
    """Validate one v2 receipt's closed structure and expected cross-bindings."""

    if not isinstance(value, Mapping):
        _fail("receipt must be an object")
    _scan_safe(value)
    _schema_validate(value)
    if value.get("record_sha256") != record_sha256(value):
        _fail("receipt record digest differs")
    if value.get("authority_class") != AUTHORITY_CLASS or value.get(
        "proof_source"
    ) != PROOF_SOURCE:
        _fail("receipt declared observation provenance differs")
    if value.get("status") != "exited" or value.get("exit_code") != 0:
        _fail("receipt does not prove a clean Host exit")
    if any(item is not False for item in value["isolation"].values()):
        _fail("receipt isolation boundary is not closed")
    if (
        value.get("execution_target_regular") is not True
        or value.get("execution_target_single_link") is not True
        or (value.get("host") == "codex" and value.get("selector_source_symlink") is not False)
    ):
        _fail("receipt execution topology is invalid")

    _validate_time_window(value)
    _validate_authority_digests(value)
    host = value["host"]
    proof = value["proof"]
    if host == "codex":
        _validate_codex_proof(value, proof)
    elif host == "opencode":
        _validate_opencode_proof(value, proof)
    else:
        _fail("receipt Host is unsupported")

    if expected_host is not None and host != expected_host:
        _fail("receipt Host does not match the expected Host")
    if expected_task_case is not None and value["task_case"] != expected_task_case:
        _fail("receipt task case does not match the expected task")
    if expected_run_id is not None and value["run_id"] != expected_run_id:
        _fail("receipt run identifier does not match the expected run")
    candidate = _expected_projection(
        expected_candidate,
        CANDIDATE_FIELDS,
        label="candidate binding",
    )
    if candidate is not None and value["candidate_binding"] != candidate:
        _fail("receipt candidate binding differs")
    run_binding = _expected_projection(
        expected_run_binding,
        RUN_BINDING_FIELDS,
        label="run binding",
    )
    if run_binding is not None and value["run_binding"] != run_binding:
        _fail("receipt run binding differs")
    for actual, expected, label in (
        (value["broker_source"]["sha256"], expected_broker_sha256, "broker source"),
        (value["host_identity_sha256"], expected_host_identity_sha256, "Host identity"),
        (
            value["host_identity_source_sha256"],
            expected_host_identity_source_sha256,
            "Host identity source",
        ),
    ):
        if expected is not None:
            _sha(expected, label=f"expected {label}")
            if actual != expected:
                _fail(f"receipt {label} differs")
    if expected_host_binary is not None and value["host_binary"] != dict(
        expected_host_binary
    ):
        _fail("receipt Host binary differs")

    nonce = value["nonce_sha256"]
    if nonce in seen_nonce_sha256s:
        _fail("receipt one-time nonce was replayed")
    seen_nonce_sha256s.add(nonce)
    return dict(value)


def build_receipt(
    *,
    host: str,
    task_case: str,
    run_id: str,
    candidate_binding: Mapping[str, Any],
    run_binding: Mapping[str, Any],
    host_binary: Mapping[str, Any],
    broker_source: Mapping[str, Any],
    host_identity_sha256: str,
    host_identity_source_sha256: str,
    process_identity_sha256: str,
    broker_instance_sha256: str,
    nonce_sha256: str,
    issued_at: str,
    expires_at: str,
    validation_reference_time: str,
    selector_source_symlink: bool,
    execution_target_regular: bool,
    execution_target_single_link: bool,
    status: str,
    exit_code: int,
    native_event_binding: Mapping[str, Any],
    proof: Mapping[str, Any],
    isolation: Mapping[str, Any],
) -> dict[str, Any]:
    """Serialize an already sanitized observation without granting authority."""
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "authority_class": AUTHORITY_CLASS,
        "proof_source": PROOF_SOURCE,
        "host": host,
        "task_case": task_case,
        "run_id": run_id,
        "status": status,
        "exit_code": exit_code,
        "candidate_binding": {
            field: candidate_binding[field] for field in CANDIDATE_FIELDS
        },
        "run_binding": {field: run_binding[field] for field in RUN_BINDING_FIELDS},
        "host_binary": dict(host_binary),
        "broker_source": dict(broker_source),
        "isolation": dict(isolation),
        "host_identity_sha256": host_identity_sha256,
        "host_identity_source_sha256": host_identity_source_sha256,
        "process_identity_sha256": process_identity_sha256,
        "broker_instance_sha256": broker_instance_sha256,
        "nonce_sha256": nonce_sha256,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "validation_reference_time": validation_reference_time,
        "selector_source_symlink": selector_source_symlink,
        "execution_target_regular": execution_target_regular,
        "execution_target_single_link": execution_target_single_link,
        "native_event_binding": {
            field: native_event_binding[field] for field in NATIVE_EVENT_FIELDS
        },
        "proof": dict(proof),
    }
    value["record_sha256"] = record_sha256(value)
    return validate_receipt(value, seen_nonce_sha256s=set())


build_process_receipt = build_receipt
validate_process_receipt = validate_receipt

__all__ = [
    "AUTHORITY_CLASS",
    "CANDIDATE_FIELDS",
    "HOSTS",
    "MAX_RECEIPT_LIFETIME_SECONDS",
    "NATIVE_EVENT_FIELDS",
    "PROOF_SOURCE",
    "RUN_BINDING_FIELDS",
    "SCHEMA_FILENAME",
    "SCHEMA_VERSION",
    "TASK_CASES",
    "HostProcessReceiptV2Error",
    "build_process_receipt",
    "build_receipt",
    "canonical_json",
    "correlation_sha256",
    "record_sha256",
    "validate_process_receipt",
    "validate_receipt",
]

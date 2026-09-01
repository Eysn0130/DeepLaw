"""Validate a closed task-level set of Host process receipt v2 records.

The set is a bounded, path-free wrapper for one Host x task control slot.  It
does not grant Authority: external broker/collector provenance remains
necessary for any observation claim.  Every member is admitted through the
existing v2 validator and is bound to the wrapper's exact task identity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableSet, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError, ValidationError

from benchmarks.hosts import host_process_receipt_v2 as receipt_v2

SCHEMA_VERSION = "deeplaw.host-process-receipt-set/v1"
SCHEMA_FILENAME = "host-process-receipt-set.v1.schema.json"
MAX_PROCESS_COUNT = 32
MAX_PROCESSES = MAX_PROCESS_COUNT
HOSTS = receipt_v2.HOSTS
TASK_CASES = receipt_v2.TASK_CASES
CANDIDATE_FIELDS = receipt_v2.CANDIDATE_FIELDS
RUN_BINDING_FIELDS = receipt_v2.RUN_BINDING_FIELDS
NATIVE_EVENT_FIELDS = receipt_v2.NATIVE_EVENT_FIELDS
PROCESS_ROLES = {"codex": "codex_app_server", "opencode": "opencode_run"}


class HostProcessReceiptSetV1Error(ValueError):
    """A receipt set is unsafe, incomplete, replayed, or cross-bound."""


HostProcessReceiptSetError = HostProcessReceiptSetV1Error


def _fail(message: str) -> None:
    raise HostProcessReceiptSetV1Error(message)


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts" / SCHEMA_FILENAME


def canonical_json(value: Any) -> bytes:
    try:
        return receipt_v2.canonical_json(value)
    except (TypeError, UnicodeError, ValueError, receipt_v2.HostProcessReceiptV2Error) as error:
        raise HostProcessReceiptSetV1Error("receipt set is not canonical JSON") from error


def record_sha256(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        _fail("receipt set must be an object")
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _schema_validate(value: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(value))
    except (OSError, UnicodeError, TypeError, ValueError, SchemaError, ValidationError) as error:
        raise HostProcessReceiptSetV1Error("receipt set schema validation failed") from error


def _sha(value: Any, *, label: str) -> str:
    try:
        return receipt_v2._sha(value, label=label)
    except receipt_v2.HostProcessReceiptV2Error as error:
        raise HostProcessReceiptSetV1Error(str(error)) from error


def _projection(
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


def _validate_task_native_binding(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        _fail("task native event binding must be an object")
    for field in NATIVE_EVENT_FIELDS:
        _sha(value.get(field), label=f"task native {field}")


def _validate_member_row(
    row: Mapping[str, Any], *, host: str, expected_index: int
) -> Mapping[str, Any]:
    if not isinstance(row, Mapping):
        _fail("receipt set process row must be an object")
    if row.get("sequence_index") != expected_index:
        _fail("receipt set process sequence is not consecutive")
    if row.get("process_role") != PROCESS_ROLES.get(host):
        _fail("receipt set process role does not match the Host")
    receipt = row.get("receipt")
    if not isinstance(receipt, Mapping):
        _fail("receipt set process row lacks a v2 receipt")
    return receipt


def validate_receipt_set(
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
    expected_process_count: int | None = None,
    expected_task_native_event_binding: Mapping[str, Any] | None = None,
    seen_nonce_sha256s: MutableSet[str] | None = None,
) -> dict[str, Any]:
    """Validate one closed set and every complete member v2 receipt.

    Structural validation and serialization do not establish formal authority;
    the external per-Host broker/collector and its workflow provenance remain
    required.  ``seen_nonce_sha256s`` is shared with callers so one-time
    member nonces cannot be replayed across sets.
    """

    if not isinstance(value, Mapping):
        _fail("receipt set must be an object")
    try:
        receipt_v2._scan_safe(value)
    except receipt_v2.HostProcessReceiptV2Error as error:
        raise HostProcessReceiptSetV1Error(str(error)) from error
    _schema_validate(value)
    if value.get("record_sha256") != record_sha256(value):
        _fail("receipt set record digest differs")

    host = value["host"]
    task_case = value["task_case"]
    run_id = value["run_id"]
    candidate = value["candidate_binding"]
    run_binding = value["run_binding"]
    broker_source = value["broker_source"]
    host_binary = value["host_binary"]
    identity_sha256 = value["host_identity_sha256"]
    identity_source_sha256 = value["host_identity_source_sha256"]
    for actual, expected, label in (
        (host, expected_host, "Host"),
        (task_case, expected_task_case, "task case"),
        (run_id, expected_run_id, "run identifier"),
    ):
        if expected is not None and actual != expected:
            _fail(f"receipt set {label} differs")
    for actual, expected, fields, label in (
        (candidate, expected_candidate, CANDIDATE_FIELDS, "candidate binding"),
        (run_binding, expected_run_binding, RUN_BINDING_FIELDS, "run binding"),
    ):
        projected = _projection(expected, fields, label=label)
        if projected is not None and actual != projected:
            _fail(f"receipt set {label} differs")
    for actual, expected, label in (
        (broker_source["sha256"], expected_broker_sha256, "broker source"),
        (identity_sha256, expected_host_identity_sha256, "Host identity"),
        (identity_source_sha256, expected_host_identity_source_sha256, "Host identity source"),
    ):
        if expected is not None:
            _sha(expected, label=f"expected {label}")
            if actual != expected:
                _fail(f"receipt set {label} differs")
    if expected_host_binary is not None and host_binary != dict(expected_host_binary):
        _fail("receipt set Host binary differs")
    if (
        expected_process_count is not None
        and value["expected_process_count"] != expected_process_count
    ):
        _fail("receipt set expected process count differs")
    task_native = value["task_native_event_binding"]
    _validate_task_native_binding(task_native)
    expected_task_native = _projection(
        expected_task_native_event_binding,
        NATIVE_EVENT_FIELDS,
        label="task native event binding",
    )
    if expected_task_native is not None and task_native != expected_task_native:
        _fail("receipt set task native event binding differs")

    processes = value["processes"]
    expected_count = value["expected_process_count"]
    observed_count = value["observed_process_count"]
    if (
        value["coverage_complete"] is not True
        or expected_count != observed_count
        or observed_count != len(processes)
        or not 1 <= len(processes) <= MAX_PROCESS_COUNT
    ):
        _fail("receipt set process coverage/count is invalid")
    shared_seen = seen_nonce_sha256s if seen_nonce_sha256s is not None else set()
    seen_records: set[str] = set()
    seen_processes: set[str] = set()
    seen_brokers: set[str] = set()
    for sequence_index, row in enumerate(processes, start=1):
        member = _validate_member_row(row, host=host, expected_index=sequence_index)
        member_record = member.get("record_sha256")
        member_process = member.get("process_identity_sha256")
        member_broker = member.get("broker_instance_sha256")
        if member_record in seen_records:
            _fail("receipt set member record digest was duplicated")
        if member_process in seen_processes:
            _fail("receipt set process identity was duplicated")
        if member_broker in seen_brokers:
            _fail("receipt set broker instance was duplicated")
        try:
            admitted = receipt_v2.validate_receipt(
                member,
                expected_host=host,
                expected_task_case=task_case,
                expected_run_id=run_id,
                expected_candidate=candidate,
                expected_run_binding=run_binding,
                expected_broker_sha256=broker_source["sha256"],
                expected_host_identity_sha256=identity_sha256,
                expected_host_identity_source_sha256=identity_source_sha256,
                expected_host_binary=host_binary,
                seen_nonce_sha256s=shared_seen,
            )
        except (TypeError, ValueError, receipt_v2.HostProcessReceiptV2Error) as error:
            raise HostProcessReceiptSetV1Error(
                "receipt set member v2 validation failed"
            ) from error
        seen_records.add(admitted["record_sha256"])
        seen_processes.add(admitted["process_identity_sha256"])
        seen_brokers.add(admitted["broker_instance_sha256"])
    return dict(value)


def _normalise_processes(
    processes: Sequence[Mapping[str, Any]],
    *,
    host: str,
) -> list[dict[str, Any]]:
    if isinstance(processes, (str, bytes, bytearray)) or not isinstance(processes, Sequence):
        _fail("receipt set processes must be a sequence")
    rows: list[dict[str, Any]] = []
    role = PROCESS_ROLES.get(host)
    if role is None:
        _fail("receipt set Host is unsupported")
    for index, item in enumerate(processes, start=1):
        if not isinstance(item, Mapping):
            _fail("receipt set process must be an object")
        if "receipt" in item:
            if set(item) != {"sequence_index", "process_role", "receipt"}:
                _fail("receipt set process row contains an unknown field")
            sequence_index = item.get("sequence_index")
            process_role = item.get("process_role")
            member = item.get("receipt")
        else:
            sequence_index = index
            process_role = role
            member = item
        if sequence_index != index or process_role != role or not isinstance(member, Mapping):
            _fail("receipt set process row binding is invalid")
        rows.append(
            {
                "sequence_index": index,
                "process_role": role,
                "receipt": dict(member),
            }
        )
    return rows


def build_receipt_set(
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
    task_native_event_binding: Mapping[str, Any],
    processes: Sequence[Mapping[str, Any]],
    expected_process_count: int | None = None,
    observed_process_count: int | None = None,
) -> dict[str, Any]:
    """Serialize a sanitized process set without granting observation authority."""

    rows = _normalise_processes(processes, host=host)
    count = len(rows)
    if expected_process_count is None:
        expected_process_count = count
    if observed_process_count is None:
        observed_process_count = count
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "task_case": task_case,
        "run_id": run_id,
        "candidate_binding": {
            field: candidate_binding[field] for field in CANDIDATE_FIELDS
        },
        "run_binding": {field: run_binding[field] for field in RUN_BINDING_FIELDS},
        "host_binary": dict(host_binary),
        "broker_source": dict(broker_source),
        "host_identity_sha256": host_identity_sha256,
        "host_identity_source_sha256": host_identity_source_sha256,
        "expected_process_count": expected_process_count,
        "observed_process_count": observed_process_count,
        "coverage_complete": True,
        "processes": rows,
        "task_native_event_binding": {
            field: task_native_event_binding[field] for field in NATIVE_EVENT_FIELDS
        },
    }
    value["record_sha256"] = record_sha256(value)
    return validate_receipt_set(value, seen_nonce_sha256s=set())


build_process_receipt_set = build_receipt_set
validate_process_receipt_set = validate_receipt_set

__all__ = [
    "CANDIDATE_FIELDS",
    "HOSTS",
    "MAX_PROCESSES",
    "MAX_PROCESS_COUNT",
    "NATIVE_EVENT_FIELDS",
    "PROCESS_ROLES",
    "RUN_BINDING_FIELDS",
    "SCHEMA_FILENAME",
    "SCHEMA_VERSION",
    "TASK_CASES",
    "HostProcessReceiptSetError",
    "HostProcessReceiptSetV1Error",
    "build_process_receipt_set",
    "build_receipt_set",
    "canonical_json",
    "record_sha256",
    "validate_process_receipt_set",
    "validate_receipt_set",
]

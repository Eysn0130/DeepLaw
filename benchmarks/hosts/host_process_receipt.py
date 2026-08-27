"""Sanitized receipts for completed external Host processes.

The external owner-only broker is the process boundary for credentials.  This
module accepts only typed process metadata, reuses the exact Host/broker
inspection rules from :mod:`host_preflight_receipt`, and retains the strict
path-free ``host-process-receipt.v1`` schema shape.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError, ValidationError

from benchmarks.hosts import host_preflight_receipt as preflight

SCHEMA_VERSION = "deeplaw.host-process-receipt/v1"
SCHEMA_FILENAME = "host-process-receipt.v1.schema.json"
HOSTS = frozenset({"codex", "opencode"})
TASK_CASES = frozenset({"continuity", "living_wiki", "professional_evidence"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s\"'=:(])/(?!/)[^\s\"'=;:)]+|"
    r"(?:^|[\s\"'=:(])[A-Za-z]:[\\/]"
)
_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:path|command|argv|env|stdout|stderr|output|prompt|transcript|"
    r"reasoning|secret|auth|credential|token)(?:_|$)",
    re.IGNORECASE,
)
_SUPERVISOR_KEYS = frozenset({"observed", "exit_code"})
_ISOLATION_KEYS = frozenset(
    {
        "runner_received_secret",
        "mcp_received_secret",
        "ambient_auth_forwarded_to_mcp",
        "raw_output_retained",
    }
)
_TOPOLOGY_KEYS = frozenset(
    {
        "selector_source_symlink",
        "execution_target_regular",
        "execution_target_single_link",
    }
)


class HostProcessReceiptError(ValueError):
    """A Host process receipt is unsafe or does not match the v1 contract."""


def _fail(message: str) -> None:
    raise HostProcessReceiptError(message)


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts" / SCHEMA_FILENAME


def canonical_json(value: Any) -> bytes:
    """Return canonical JSON bytes for a receipt body."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise HostProcessReceiptError("receipt is not canonical JSON") from error


def record_sha256(value: Mapping[str, Any]) -> str:
    """Hash the canonical receipt body with its self-digest excluded."""

    if not isinstance(value, Mapping):
        _fail("receipt must be an object")
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _scan_safe(value: Any, *, key: str | None = None) -> None:
    """Reject forbidden field names and path-bearing strings before schema use."""

    if key is not None:
        if not isinstance(key, str):
            _fail("receipt field name is invalid")
        if key not in _ISOLATION_KEYS and _FORBIDDEN_KEY.search(key):
            _fail("receipt contains a forbidden field")
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            _scan_safe(nested_value, key=nested_key)
    elif isinstance(value, list):
        for item in value:
            _scan_safe(item)
    elif isinstance(value, str) and _ABSOLUTE_PATH.search(value):
        _fail("receipt contains an absolute path")


def _strict_mapping(value: Any, keys: frozenset[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(f"{label} fields are not closed")
    if any(not isinstance(key, str) for key in value):
        _fail(f"{label} field name is invalid")
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        _fail(f"{label} must be boolean")
    return value


def _strict_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return value


def _strict_identity(identity: Mapping[str, Any], *, host: str) -> tuple[dict[str, str], str, str]:
    if not isinstance(identity, Mapping) or set(identity) != {
        "schema_version",
        "hosts",
        "source_sha256",
        "source_bytes",
    }:
        _fail("Host identity fields are not closed")
    if identity.get("schema_version") != preflight.HOST_IDENTITY_SCHEMA_VERSION:
        _fail("Host identity schema version is unsupported")
    source_sha256 = _strict_sha(identity.get("source_sha256"), label="Host identity source")
    source_bytes = identity.get("source_bytes")
    if (
        type(source_bytes) is not int
        or source_bytes < 1
        or source_bytes > preflight.HOST_IDENTITY_MAX_BYTES
    ):
        _fail("Host identity source byte bound is invalid")
    if host not in HOSTS:
        _fail("Host identity Host is unsupported")
    try:
        binary = preflight.host_binary_identity(identity, host)
        host_item = identity["hosts"][host]
        host_identity_sha256 = preflight.host_identity_sha256(host_item)
    except (KeyError, TypeError, ValueError, preflight.HostIdentityValidationError) as error:
        raise HostProcessReceiptError("Host identity projection is invalid") from error
    _strict_sha(binary.get("sha256"), label="Host binary identity")
    if not isinstance(binary.get("version"), str) or not binary["version"]:
        _fail("Host binary identity version is invalid")
    _strict_sha(host_identity_sha256, label="Host identity")
    return binary, host_identity_sha256, source_sha256


def _strict_supervisor(supervisor: Mapping[str, Any]) -> None:
    selected = _strict_mapping(supervisor, _SUPERVISOR_KEYS, label="Supervisor")
    if selected.get("observed") is not True or selected.get("exit_code") != 0:
        _fail("Supervisor did not observe a clean Host exit")


def _strict_isolation(isolation: Mapping[str, Any]) -> dict[str, bool]:
    selected = _strict_mapping(isolation, _ISOLATION_KEYS, label="Isolation")
    result: dict[str, bool] = {}
    for key in sorted(_ISOLATION_KEYS):
        result[key] = _strict_bool(selected[key], label=f"Isolation {key}")
        if result[key] is not False:
            _fail("Isolation boundary is not closed")
    return result


def _strict_topology(topology: Mapping[str, Any]) -> dict[str, bool]:
    selected = _strict_mapping(topology, _TOPOLOGY_KEYS, label="Execution topology")
    result = {
        key: _strict_bool(selected[key], label=f"Execution topology {key}")
        for key in sorted(_TOPOLOGY_KEYS)
    }
    if (
        result["execution_target_regular"] is not True
        or result["execution_target_single_link"] is not True
    ):
        _fail("Execution target topology is not closed")
    return result


def _schema_validate(value: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(value))
    except (OSError, UnicodeError, TypeError, ValueError, SchemaError, ValidationError) as error:
        raise HostProcessReceiptError("receipt schema validation failed") from error


def validate_receipt(
    value: Mapping[str, Any],
    *,
    identity: Mapping[str, Any] | None = None,
    expected_host: str | None = None,
    expected_task_case: str | None = None,
    expected_run_id: str | None = None,
    expected_broker_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate one path-free receipt and optionally bind its frozen identity."""

    if not isinstance(value, Mapping):
        _fail("receipt must be an object")
    _scan_safe(value)
    _schema_validate(value)
    if value.get("record_sha256") != record_sha256(value):
        _fail("receipt record digest differs")

    host = value["host"]
    if expected_host is not None and host != expected_host:
        _fail("receipt Host does not match the expected Host")
    if expected_task_case is not None and value["task_case"] != expected_task_case:
        _fail("receipt task case does not match the expected case")
    if expected_run_id is not None and value["run_id"] != expected_run_id:
        _fail("receipt run identifier does not match the expected run")
    if expected_broker_sha256 is not None:
        _strict_sha(expected_broker_sha256, label="Expected broker source")
        if value["broker_source"]["sha256"] != expected_broker_sha256:
            _fail("receipt broker source does not match the expected source")

    if identity is not None:
        expected_binary, expected_identity_sha256, expected_source_sha256 = _strict_identity(
            identity, host=host
        )
        if (
            value["host_binary"] != expected_binary
            or value["host_identity_sha256"] != expected_identity_sha256
            or value["host_identity_source_sha256"] != expected_source_sha256
        ):
            _fail("receipt does not bind the frozen Host identity")
        if host == "codex" and value["selector_source_symlink"] is not False:
            _fail("Codex selector topology is invalid")

    broker = value["broker_source"]
    if broker["repository_external"] is not True or broker["owner_only_mode"] is not True:
        _fail("broker source boundary is not closed")
    isolation = value["isolation"]
    if any(item is not False for item in isolation.values()):
        _fail("receipt isolation is not closed")
    if value["status"] != "exited" or value["exit_code"] != 0:
        _fail("receipt does not prove a clean Host exit")
    if (
        value["execution_target_regular"] is not True
        or value["execution_target_single_link"] is not True
    ):
        _fail("receipt execution target topology is invalid")
    return dict(value)


def build_receipt(
    *,
    host: str,
    task_case: str,
    run_id: str,
    identity: Mapping[str, Any],
    repository: Path | str,
    host_binary: Path | str,
    broker_path: Path | str,
    supervisor: Mapping[str, Any],
    isolation: Mapping[str, Any],
    execution_identity: Mapping[str, Any] | None = None,
    selector_source_symlink: bool | None = None,
    execution_target_regular: bool | None = None,
    execution_target_single_link: bool | None = None,
    expected_broker_sha256: str,
) -> dict[str, Any]:
    """Inspect exact external artifacts and produce a sanitized process receipt."""

    if host not in HOSTS:
        _fail("Host is unsupported")
    if task_case not in TASK_CASES:
        _fail("task case is unsupported")
    if not isinstance(run_id, str) or _IDENTIFIER.fullmatch(run_id) is None:
        _fail("run identifier is invalid")
    _strict_supervisor(supervisor)
    selected_isolation = _strict_isolation(isolation)
    _strict_sha(expected_broker_sha256, label="Expected broker source")
    expected_binary, expected_identity_sha256, expected_source_sha256 = _strict_identity(
        identity, host=host
    )

    repository_path = Path(repository)
    binary_path = Path(host_binary)
    try:
        binary_observation = preflight.inspect_host_binary(
            binary_path,
            host=host,
            identity=identity,
            repository=repository_path,
        )
        broker_observation = preflight.inspect_broker_source(
            Path(broker_path),
            repository=repository_path,
            host_binary=binary_path,
            expected_sha256=expected_broker_sha256,
        )
    except (OSError, TypeError, ValueError) as error:
        raise HostProcessReceiptError("Host artifact inspection failed") from error
    if broker_observation.get("failure_reason_code") is not None:
        _fail("broker source inspection failed")
    broker_sha256 = broker_observation.get("sha256")
    _strict_sha(broker_sha256, label="Broker source")
    if broker_observation.get("repository_external") is not True or broker_observation.get(
        "owner_only_mode"
    ) is not True:
        _fail("broker source boundary is not closed")

    observed_topology = {
        "selector_source_symlink": binary_observation.get("selector_source_symlink"),
        "execution_target_regular": binary_observation.get("execution_target_regular"),
        "execution_target_single_link": binary_observation.get("execution_target_single_link"),
    }
    normalized_topology = _strict_topology(observed_topology)
    if host == "codex" and normalized_topology["selector_source_symlink"] is not False:
        _fail("Codex selector topology is invalid")

    if execution_identity is not None:
        supplied_topology = _strict_topology(execution_identity)
        if supplied_topology != normalized_topology:
            _fail("execution topology differs from the inspected Host target")
    supplied_fields = {
        "selector_source_symlink": selector_source_symlink,
        "execution_target_regular": execution_target_regular,
        "execution_target_single_link": execution_target_single_link,
    }
    for key, supplied in supplied_fields.items():
        if supplied is not None:
            _strict_bool(supplied, label=f"Execution topology {key}")
            if supplied is not normalized_topology[key]:
                _fail("execution topology differs from the inspected Host target")

    if binary_observation.get("version") != expected_binary["version"] or binary_observation.get(
        "sha256"
    ) != expected_binary["sha256"]:
        _fail("Host binary inspection does not bind the frozen identity")
    if (
        binary_observation.get("host_identity_sha256") != expected_identity_sha256
        or binary_observation.get("host_identity_source_sha256")
        != expected_source_sha256
    ):
        _fail("Host identity inspection does not bind the supplied identity")

    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "task_case": task_case,
        "run_id": run_id,
        "status": "exited",
        "exit_code": 0,
        "host_binary": {
            "version": expected_binary["version"],
            "sha256": expected_binary["sha256"],
        },
        "broker_source": {
            "repository_external": True,
            "owner_only_mode": True,
            "sha256": broker_sha256,
        },
        "isolation": selected_isolation,
        "host_identity_sha256": expected_identity_sha256,
        "host_identity_source_sha256": expected_source_sha256,
        "selector_source_symlink": normalized_topology["selector_source_symlink"],
        "execution_target_regular": True,
        "execution_target_single_link": True,
    }
    value["record_sha256"] = record_sha256(value)
    return validate_receipt(
        value,
        identity=identity,
        expected_host=host,
        expected_task_case=task_case,
        expected_run_id=run_id,
        expected_broker_sha256=expected_broker_sha256,
    )


build_process_receipt = build_receipt
produce_receipt = build_receipt
validate_process_receipt = validate_receipt


__all__ = [
    "HOSTS",
    "SCHEMA_FILENAME",
    "SCHEMA_VERSION",
    "TASK_CASES",
    "HostProcessReceiptError",
    "build_process_receipt",
    "build_receipt",
    "canonical_json",
    "produce_receipt",
    "record_sha256",
    "validate_process_receipt",
    "validate_receipt",
]

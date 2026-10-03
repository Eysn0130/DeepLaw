"""Validate a candidate owner-external guard isolation source, without admission.

Every boolean and actor digest is an external producer declaration that must be
reopened against native observations. This validator checks structure, policy,
and supplied cross-bindings only; it does not observe processes, read a key, or
prove source authenticity, create observations, or authorize credential access.
The qualification consumer can parse this candidate to report an observation
gap, but it cannot admit it as formal isolation evidence.
Public requests and native responses may be inspected in memory, but their raw
contents must not be retained. Store-read declarations
refer to on-disk private Host stores, not the authority's authorized access to an
existing broker credential. A later native collector must establish actual
process and execution bindings before considering formal qualification.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

SCHEMA_VERSION = "deeplaw.host-owner-guard-isolation/v1"
SCHEMA_FILENAME = "host-owner-guard-isolation.v1.schema.json"
ACTOR_ROLES = ("credential_authority", "host", "mcp", "observer", "runner", "scorer")
BINDING_FIELDS = (
    "observation_binding_sha256",
    "challenge_nonce_sha256",
    "native_process_receipt_sha256",
    "execution_binding_sha256",
)
_ENVELOPE_FIELDS = (
    "candidate_binding",
    "run_binding",
    "corpus",
    "runner",
    "scorer",
    "host",
    "task_case",
)


class OwnerGuardIsolationError(ValueError):
    """A fixed diagnostic code with no source value or schema path."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OwnerGuardIsolationError(code) from None


def _plain(value: Any, *, code: str, depth: int = 0) -> Any:
    # The closed source has only small objects and scalar metadata. These bounds
    # reject cyclic or oversized untyped inputs before JSON/schema processing.
    _require(depth <= 8, code)
    if isinstance(value, Mapping):
        _require(len(value) <= 32, code)
        result = {}
        for key, item in value.items():
            _require(type(key) is str and len(key) <= 200, code)
            result[key] = _plain(item, code=code, depth=depth + 1)
        return result
    _require(value is None or type(value) in (str, bool, int), code)
    if isinstance(value, str):
        _require(len(value) <= 200, code)
    return value


def _schema() -> dict[str, Any]:
    try:
        path = Path(__file__).resolve().parents[2] / "contracts" / SCHEMA_FILENAME
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, TypeError, ValueError, SchemaError):
        raise OwnerGuardIsolationError("owner_guard_schema_unavailable") from None
    return schema


def _projection_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(_ENVELOPE_FIELDS),
        "properties": {field: schema["properties"][field] for field in _ENVELOPE_FIELDS},
        "$defs": schema["$defs"],
    }


def _validate_roles(source: Mapping[str, Any]) -> None:
    actors = source["actors"]
    # Only the external observation/scoring roles may share a process. The
    # credential authority, Host, and MCP each need a separate actual identity.
    for role in ACTOR_ROLES[:3]:
        identity = actors[role]["process_identity_sha256"]
        _require(
            all(
                identity != actors[other]["process_identity_sha256"]
                for other in ACTOR_ROLES
                if other != role
            ),
            "owner_guard_role_overlap",
        )
    for index, role in enumerate(ACTOR_ROLES[3:]):
        for other in ACTOR_ROLES[3 + index + 1 :]:
            if actors[role]["process_identity_sha256"] == actors[other]["process_identity_sha256"]:
                _require(
                    actors[role]["instance_sha256"] == actors[other]["instance_sha256"],
                    "owner_guard_instance_mismatch",
                )
    for role in ("runner", "scorer"):
        _require(
            actors[role]["source_sha256"] == source[role]["sha256"],
            "owner_guard_actor_source_mismatch",
        )


def _validate_write(source: Mapping[str, Any]) -> None:
    write = source["write_observation"]
    mutation = write["authorized_mutation"]
    observed = mutation["observed"]
    if observed:
        _require(
            source["task_case"] == "continuity"
            and mutation["operation"] == "owner_forget"
            and mutation["owner_authorized"] is True
            and mutation["receipt_sha256"] is not None,
            "owner_guard_write_invalid",
        )
    else:
        _require(
            mutation["operation"] is None
            and mutation["owner_authorized"] is False
            and mutation["receipt_sha256"] is None,
            "owner_guard_write_invalid",
        )
    _require(write["write_performed"] is observed, "owner_guard_write_invalid")
    if write["audit_head_before_sha256"] != write["audit_head_after_sha256"]:
        _require(source["task_case"] == "continuity" and observed, "owner_guard_write_invalid")


def validate_owner_guard_isolation(
    value: Mapping[str, Any],
    *,
    envelope: Mapping[str, Any],
    expected_bindings: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a closed source and return its canonical digest with formal=False.

    ``envelope`` supplies the existing seven Host-task cross-binding fields; its
    other typed-envelope fields are neither changed nor returned. The four
    native observation bindings come from a separate closed ``expected_bindings``
    mapping. Matching caller-supplied digests does not authenticate those callers
    or turn producer declarations into actual native observations.
    """

    _require(isinstance(value, Mapping), "owner_guard_shape_invalid")
    _require(isinstance(envelope, Mapping), "owner_guard_envelope_invalid")
    _require(isinstance(expected_bindings, Mapping), "owner_guard_expected_bindings_invalid")
    source = _plain(value, code="owner_guard_shape_invalid")
    _require(all(field in envelope for field in _ENVELOPE_FIELDS), "owner_guard_envelope_invalid")
    bound_envelope = _plain(
        {field: envelope[field] for field in _ENVELOPE_FIELDS}, code="owner_guard_envelope_invalid"
    )
    bindings = _plain(expected_bindings, code="owner_guard_expected_bindings_invalid")
    schema = _schema()
    _require(Draft202012Validator(schema).is_valid(source), "owner_guard_shape_invalid")
    _require(
        Draft202012Validator(_projection_schema(schema)).is_valid(bound_envelope),
        "owner_guard_envelope_invalid",
    )
    _require(
        Draft202012Validator(
            {"$ref": "#/$defs/expectedBindings", "$defs": schema["$defs"]}
        ).is_valid(bindings),
        "owner_guard_expected_bindings_invalid",
    )
    _require(
        all(source[field] == bound_envelope[field] for field in _ENVELOPE_FIELDS),
        "owner_guard_binding_mismatch",
    )
    _require(
        all(source[field] == bindings[field] for field in BINDING_FIELDS),
        "owner_guard_binding_mismatch",
    )
    _validate_roles(source)
    _validate_write(source)
    try:
        raw = json.dumps(
            source, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        raise OwnerGuardIsolationError("owner_guard_shape_invalid") from None
    return {
        "formal_admission": False,
        "validated_source": source,
        "record_sha256": hashlib.sha256(raw).hexdigest(),
    }

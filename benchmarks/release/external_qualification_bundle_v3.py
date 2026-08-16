"""Fail-closed validation for the current-only external qualification bundle v3.

This module validates a bundle boundary.  It does not run a Gate, interpret a
receipt, attest a human decision, or derive ``release_ready``/``claim_eligible``.
Only path-free counts and digests are returned.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import stat
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from defusedxml import ElementTree as DefusedET
from jsonschema import Draft202012Validator

MAX_FILES = 10_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_ACTIVE_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
BUNDLE_MANIFEST_NAME = "bundle-manifest.json"
SCHEMA_VERSION = "deeplaw.external-qualification-bundle-manifest/v3"
VALIDATION_SCHEMA_VERSION = "deeplaw.external-qualification-bundle-validation/v3"
REPOSITORY = Path(__file__).resolve().parents[2]
BUNDLE_MANIFEST_SCHEMA = (
    REPOSITORY / "contracts/external-qualification-bundle-manifest.v3.schema.json"
)
ACTIVE_QUALIFICATION_SCHEMA = (
    REPOSITORY / "contracts/v013-active-qualification.v1.schema.json"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,512}$")
_DRIVE_PATH = re.compile(r"(?:^|[\s\"'])[A-Za-z]:[\\/]")
_ABSOLUTE_PATH = re.compile(
    r"(?:(?<![A-Za-z0-9])/(?:Users|home|private|var|tmp|root|etc|opt|Volumes|workspace)(?:/|$))",
    re.IGNORECASE,
)
_SECRET_KEY = re.compile(
    r"(?:^|[._-])(?:auth|authorization|credential|credentials|secret|secrets|"
    r"password|passwd|api[_-]?key|private[_-]?key|access[_-]?token|token|bearer)"
    r"(?:$|[._-])",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?ix)(?:"
    r"(?:api[_-]?key|access[_-]?token|authorization|bearer|password|passwd|"
    r"private[_-]?key|secret|token)\s*[:=]\s*\S+"
    r"|-----begin[^\n]*(?:private|rsa|openssh)[^\n]*-----"
    r"|(?:ghp_|github_pat_|glpat-|xox[baprs]-|sk-[A-Za-z0-9]|eyJ[A-Za-z0-9_-]+\.)"
    r")",
)
_SAFE_FALSE_FIELDS = frozenset(
    {
        "auth_file_read",
        "auth_store_read",
        "authentication_material_read",
        "credential_value_recorded",
    }
)
_SAFE_DIGEST_FIELDS = frozenset(
    {
        "auth_file_sha256",
        "auth_store_sha256",
        "authentication_material_sha256",
        "credential_path_sha256",
        "credential_value_sha256",
    }
)
_FORBIDDEN_NAME = re.compile(
    r"(?:^|[._-])(?:auth|authorization|credential|credentials|secret|secrets|"
    r"password|passwd|api[_-]?key|private[_-]?key|access[_-]?token|token|"
    r"transcript|chain[_-]?of[_-]?thought|hidden[_-]?reasoning|"
    r"raw[_-]?(?:events|reasoning|log)|reasoning|log)(?:$|[._-])",
    re.IGNORECASE,
)

_TYPED_JSON_KINDS = frozenset(
    {
        "typed_manifest",
        "typed_json",
        "candidate_full_raw_inventory",
        "candidate_full_junit",
        "candidate_platform_receipt",
        "host_event_sequence",
        "exact_wheel_execution",
        "human_gold_scorer",
        "legal_rows",
        "wiki_journey_rows",
        "context_capsule_selection_usage",
        "scale_report",
        "sbom",
        "openvex",
        "licenses",
        "provenance",
        "gate_result",
        "gate_collection",
        "commercial_release_template",
        "post_build_gold_binding",
        "sanitized_supporting_receipt",
    }
)
_TYPED_XML_KINDS = frozenset({"typed_xml"})
_TEXT_KINDS = frozenset(
    {
        "sanitized_text",
        "original_legal_html",
        "original_legal_markdown",
    }
)
_BINARY_KINDS = frozenset(
    {
        "original_legal_pdf",
        "original_legal_docx",
        "retained_wheel",
        "retained_sdist",
    }
)
_CANDIDATE_RUN_KINDS = frozenset(
    {
        "candidate_full_raw_inventory",
        "candidate_full_junit",
        "candidate_platform_receipt",
    }
)
_INVENTORY_KINDS = frozenset({"candidate_full_raw_inventory"})
_WHEEL_KINDS = frozenset({"retained_wheel"})
_SDIST_KINDS = frozenset({"retained_sdist"})


class ExternalQualificationBundleV3Error(ValueError):
    """Raised when a v3 bundle is absent, unsafe, open, or mis-bound."""


# Keep the familiar name available to callers while making the version explicit.
ExternalQualificationBundleError = ExternalQualificationBundleV3Error


def _error(message: str) -> None:
    # Callers only receive fixed policy messages.  In particular, never include
    # raw JSON strings, credential values, absolute paths, or descriptor bytes.
    raise ExternalQualificationBundleV3Error(message)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError) as error:
        raise ExternalQualificationBundleV3Error("value is not canonical JSON") from error


def _record_sha256(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return _sha256_bytes(_canonical_json(body).encode("utf-8"))


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            _error("strict JSON contains duplicate keys")
        result[key] = item
    return result


def _reject_constant(value: str) -> Any:
    _error("strict JSON contains a non-finite number")


def _check_json_projection(value: Any, *, depth: int = 0) -> None:
    """Reject secrets, private paths, non-finite values, and unsafe strings."""

    if depth > MAX_JSON_DEPTH:
        _error("JSON exceeds its depth bound")
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ExternalQualificationBundleV3Error(
                "JSON contains an invalid Unicode string"
            ) from error
        if _ABSOLUTE_PATH.search(value) or _DRIVE_PATH.search(value):
            _error("evidence contains a private absolute path")
        if _SECRET_VALUE.search(value):
            _error("evidence contains Secret material")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _error("JSON object key is not a string")
            if key in _SAFE_FALSE_FIELDS:
                if item is not False:
                    _error("authentication receipt field must be false")
            elif key in _SAFE_DIGEST_FIELDS:
                if not isinstance(item, str) or not _SHA256.fullmatch(item):
                    _error("authentication receipt digest is invalid")
            elif _SECRET_KEY.search(key):
                _error("evidence contains a Secret-shaped field")
            _check_json_projection(item, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _check_json_projection(item, depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _error("JSON contains a non-finite number")
        return
    _error("JSON contains an unsupported value")


def _strict_json_bytes(raw: bytes, *, label: str = "JSON") -> Any:
    if not isinstance(raw, bytes) or not raw:
        _error(f"{label} is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except ExternalQualificationBundleV3Error:
        raise
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ExternalQualificationBundleV3Error(
            f"{label} must be strict UTF-8 JSON"
        ) from error
    _check_json_projection(value)
    return value


def _strict_json(path: Path, *, label: str = "JSON") -> Any:
    """Read one regular file as strict JSON without exposing its contents."""

    raw = _read_regular_file(path, max_bytes=MAX_FILE_BYTES, label=label)
    return _strict_json_bytes(raw, label=label)


def _has_symlink_component(path: Path) -> bool:
    current = path
    parts = current.parts
    if not parts:
        return False
    # Absolute paths retain their anchor as a non-checkable component.
    start = 1 if current.is_absolute() else 0
    for index in range(start, len(parts) + 1):
        selected = Path(*parts[:index])
        try:
            if selected.is_symlink():
                return True
        except OSError:
            return True
    return False


def _regular_path(path: Path, *, label: str, max_bytes: int) -> tuple[Path, bytes]:
    selected = path.expanduser()
    if _has_symlink_component(selected) or selected.is_symlink():
        _error(f"{label} must be a regular non-symlink file")
    try:
        resolved = selected.resolve(strict=True)
        mode = os.lstat(resolved).st_mode
        if not stat.S_ISREG(mode):
            _error(f"{label} must be a regular non-symlink file")
        size = os.stat(resolved).st_size
        if not 1 <= size <= max_bytes:
            _error(f"{label} exceeds its byte bound")
        raw = resolved.read_bytes()
    except ExternalQualificationBundleV3Error:
        raise
    except OSError as error:
        raise ExternalQualificationBundleV3Error(f"{label} is unavailable") from error
    if len(raw) != size:
        _error(f"{label} changed while it was read")
    return resolved, raw


def _read_regular_file(path: Path, *, max_bytes: int, label: str) -> bytes:
    _resolved, raw = _regular_path(path, label=label, max_bytes=max_bytes)
    return raw


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_PATH.fullmatch(value):
        _error("file reference path is not a safe relative POSIX path")
    if "\\" in value or "//" in value or "\x00" in value:
        _error("file reference path is not a safe relative POSIX path")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        _error("file reference path is not a safe relative POSIX path")
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _error("file reference path is not a safe relative POSIX path")
    # Exercise the POSIX parser as an additional guard against platform paths.
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.parts != tuple(parts):
        _error("file reference path is not a safe relative POSIX path")
    return value


def _forbidden_filename(relative_path: str) -> None:
    for component in relative_path.split("/"):
        if component == ".env" or _FORBIDDEN_NAME.search(component):
            _error("bundle contains a forbidden filename")


def _safe_root(root: Path) -> Path:
    selected = root.expanduser()
    if _has_symlink_component(selected) or selected.is_symlink():
        _error("external bundle root must be a regular non-symlink directory")
    try:
        resolved = selected.resolve(strict=True)
        mode = os.lstat(resolved).st_mode
    except OSError as error:
        raise ExternalQualificationBundleV3Error(
            "external bundle root is unavailable"
        ) from error
    if not stat.S_ISDIR(mode):
        _error("external bundle root must be a regular non-symlink directory")
    return resolved


def _scan_root(root: Path) -> tuple[Path, dict[str, bytes], int]:
    selected = _safe_root(root)
    actual: dict[str, bytes] = {}
    total = 0
    try:
        for current, directories, filenames in os.walk(selected, topdown=True, followlinks=False):
            current_path = Path(current)
            for name in (*directories, *filenames):
                path = current_path / name
                if path.is_symlink() or _has_symlink_component(path):
                    _error("external bundle contains a symbolic link")
                mode = os.lstat(path).st_mode
                if stat.S_ISDIR(mode):
                    continue
                if not stat.S_ISREG(mode):
                    _error("external bundle contains a non-regular file")
                relative = _safe_relative_path(path.relative_to(selected).as_posix())
                _forbidden_filename(relative)
                size = os.stat(path).st_size
                if not 1 <= size <= MAX_FILE_BYTES:
                    _error("external bundle file exceeds its byte bound")
                raw = path.read_bytes()
                if len(raw) != size:
                    _error("external bundle file changed while it was read")
                total += size
                if total > MAX_TOTAL_BYTES:
                    _error("external bundle exceeds its aggregate byte bound")
                actual[relative] = raw
                if len(actual) > MAX_FILES:
                    _error("external bundle exceeds its file-count bound")
    except ExternalQualificationBundleV3Error:
        raise
    except OSError as error:
        raise ExternalQualificationBundleV3Error(
            "external bundle inventory is unavailable"
        ) from error
    if BUNDLE_MANIFEST_NAME not in actual:
        _error("external bundle manifest is missing")
    return selected, actual, total


def _schema(path: Path, *, label: str) -> dict[str, Any]:
    raw = _read_regular_file(path, max_bytes=MAX_FILE_BYTES, label=label)
    value = _strict_json_bytes(raw, label=label)
    if not isinstance(value, dict):
        _error(f"{label} must be an object")
    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:
        raise ExternalQualificationBundleV3Error(f"{label} is not a valid JSON Schema") from error
    return value


def _validate_schema(value: Any, schema: Mapping[str, Any], *, label: str) -> None:
    try:
        errors = sorted(
            Draft202012Validator(schema).iter_errors(value),
            key=lambda item: list(item.path),
        )
    except Exception as error:
        raise ExternalQualificationBundleV3Error(
            f"{label} schema validation failed"
        ) from error
    if errors:
        _error(f"{label} schema validation failed")


def _positive_run(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _error(f"{label} is invalid")
    return value


def _load_active(path: Path) -> dict[str, Any]:
    raw = _read_regular_file(path, max_bytes=MAX_ACTIVE_BYTES, label="active qualification")
    value = _strict_json_bytes(raw, label="active qualification")
    if not isinstance(value, dict):
        _error("active qualification must be an object")
    schema = _schema(ACTIVE_QUALIFICATION_SCHEMA, label="active qualification contract")
    _validate_schema(value, schema, label="active qualification")
    if (
        value.get("status") != "frozen_exact_candidate"
        or value.get("candidate_version") != "0.13.0"
        or value.get("release_ready") is not False
        or value.get("claim_eligible") is not False
    ):
        _error("active qualification is not a frozen exact current candidate")
    candidate = value.get("candidate_binding")
    external = value.get("external_inputs")
    if not isinstance(candidate, Mapping) or not isinstance(external, Mapping):
        _error("active qualification binding is incomplete")
    required_candidate = (
        "source_commit",
        "source_tree",
        "lock_sha256",
        "wheel_sha256",
        "sdist_sha256",
    )
    required_external = (
        "human_gold_manifest_sha256",
        "qualification_holdout_sha256",
        "final_blind_holdout_sha256",
        "compiler_scorer_isolation_sha256",
    )
    if any(candidate.get(key) in (None, "") for key in required_candidate):
        _error("active qualification candidate binding is incomplete")
    if any(external.get(key) in (None, "") for key in required_external):
        _error("active qualification external binding is incomplete")
    return value


def _load_trusted_descriptor(path: Path, *, bundle_root: Path) -> tuple[dict[str, Any], bytes, str]:
    selected_input = path.expanduser()
    if _has_symlink_component(selected_input) or selected_input.is_symlink():
        _error("trusted human approver descriptor must be a regular non-symlink file")
    _forbidden_filename(selected_input.name)
    try:
        selected = selected_input.resolve(strict=True)
    except OSError as error:
        raise ExternalQualificationBundleV3Error(
            "trusted human approver descriptor is unavailable"
        ) from error
    try:
        selected.relative_to(bundle_root)
    except ValueError:
        pass
    else:
        _error("trusted human approver descriptor must be outside the bundle")
    raw = _read_regular_file(
        selected,
        max_bytes=MAX_DESCRIPTOR_BYTES,
        label="trusted human approver descriptor",
    )
    value = _strict_json_bytes(raw, label="trusted human approver descriptor")
    if not isinstance(value, dict) or set(value) != {"identity", "key_id", "public_key_b64"}:
        _error("trusted human approver descriptor is not closed")
    identity = value["identity"]
    key_id = value["key_id"]
    encoded = value["public_key_b64"]
    if (
        not isinstance(identity, str)
        or not identity
        or len(identity) > 200
        or _ABSOLUTE_PATH.search(identity)
        or _SECRET_VALUE.search(identity)
        or not isinstance(key_id, str)
        or not _SHA256.fullmatch(key_id)
        or not isinstance(encoded, str)
        or not encoded
    ):
        _error("trusted human approver descriptor fields are invalid")
    try:
        public_key = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ExternalQualificationBundleV3Error(
            "trusted human approver public key encoding is invalid"
        ) from error
    if len(public_key) != 32 or base64.b64encode(public_key).decode("ascii") != encoded:
        _error("trusted human approver public key is not a canonical 32-byte Ed25519 key")
    if key_id != _sha256_bytes(public_key):
        _error("trusted human approver key id is not bound to public key bytes")
    return value, raw, _sha256_bytes(raw)


def _file_policy(kind: str, media_type: str) -> str:
    if kind in _TYPED_JSON_KINDS:
        if media_type != "application/json":
            _error("evidence kind and media type differ")
        return "json"
    if kind in _TYPED_XML_KINDS:
        if media_type != "application/xml":
            _error("evidence kind and media type differ")
        return "xml"
    if kind in _TEXT_KINDS:
        expected = {
            "original_legal_html": {"text/html"},
            "original_legal_markdown": {"text/markdown"},
            "sanitized_text": {"text/plain", "text/csv", "text/html", "text/markdown"},
        }[kind]
        if media_type not in expected:
            _error("evidence kind and media type differ")
        return "text"
    if kind == "original_legal_pdf":
        if media_type != "application/pdf":
            _error("evidence kind and media type differ")
        return "binary"
    if kind == "original_legal_docx":
        if media_type != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            _error("evidence kind and media type differ")
        return "binary"
    if kind == "retained_wheel":
        if media_type not in {"application/zip", "application/octet-stream"}:
            _error("evidence kind and media type differ")
        return "binary"
    if kind == "retained_sdist":
        if media_type not in {"application/gzip", "application/x-gzip", "application/octet-stream"}:
            _error("evidence kind and media type differ")
        return "binary"
    _error("unsupported evidence kind")


def _check_text(raw: bytes, *, mode: str) -> str:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ExternalQualificationBundleV3Error(
            "sanitized evidence must be strict UTF-8"
        ) from error
    if _ABSOLUTE_PATH.search(text) or _DRIVE_PATH.search(text) or _SECRET_VALUE.search(text):
        _error("evidence contains a Secret or private absolute path")
    if mode == "xml":
        try:
            DefusedET.fromstring(text)
        except Exception as error:
            raise ExternalQualificationBundleV3Error(
                "typed XML evidence is not well formed"
            ) from error
    return text


def _candidate_from_manifest(value: Mapping[str, Any]) -> dict[str, str]:
    candidate = value.get("candidate_binding")
    if not isinstance(candidate, Mapping):
        _error("bundle candidate binding is missing")
    return {
        key: str(candidate[key])
        for key in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256")
    }


def _check_typed_binding(
    value: Any,
    *,
    reference_kind: str,
    candidate: Mapping[str, str],
    candidate_run_id: int,
    evidence_run_id: int,
) -> None:
    if not isinstance(value, Mapping):
        return
    typed_schema = value.get("schema_version")
    declared_kind = value.get("kind")
    if typed_schema == "deeplaw.typed-qualification-evidence/v1":
        if isinstance(declared_kind, str) and declared_kind != reference_kind:
            _error("typed evidence kind differs from its bundle reference")
        bound_candidate = value.get("candidate_binding")
        if isinstance(bound_candidate, Mapping):
            for key in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"):
                if bound_candidate.get(key) != candidate[key]:
                    _error("typed evidence candidate binding differs")
        run_binding = value.get("run_binding")
        if isinstance(run_binding, Mapping) and "workflow_run_id" in run_binding:
            expected = (
                candidate_run_id
                if reference_kind in _CANDIDATE_RUN_KINDS
                else evidence_run_id
            )
            if run_binding.get("workflow_run_id") != expected:
                _error("typed evidence workflow run binding differs")
        record = value.get("record_sha256")
        if (
            isinstance(record, str)
            and _SHA256.fullmatch(record)
            and record != _record_sha256(value)
        ):
            _error("typed evidence record digest differs")


def _validate_reference(
    reference: Mapping[str, Any],
    raw: bytes,
    *,
    candidate: Mapping[str, str],
    candidate_run_id: int,
    evidence_run_id: int,
) -> None:
    kind = reference.get("evidence_kind")
    media_type = reference.get("media_type")
    if not isinstance(kind, str) or not isinstance(media_type, str):
        _error("bundle file reference is invalid")
    mode = _file_policy(kind, media_type)
    if mode == "binary":
        # Opaque legal originals and retained distributions are deliberately
        # not decoded or scanned as UTF-8.
        return
    if mode == "json":
        value = _strict_json_bytes(raw, label="typed JSON evidence")
        _check_typed_binding(
            value,
            reference_kind=kind,
            candidate=candidate,
            candidate_run_id=candidate_run_id,
            evidence_run_id=evidence_run_id,
        )
        return
    _check_text(raw, mode=mode)


def _reference_index(
    manifest: Mapping[str, Any],
    *,
    root: Path,
    actual: Mapping[str, bytes],
    candidate: Mapping[str, str],
    candidate_run_id: int,
    evidence_run_id: int,
) -> tuple[dict[str, Mapping[str, Any]], Counter[str]]:
    references = manifest.get("files")
    if not isinstance(references, list) or not references:
        _error("bundle file inventory is missing")
    by_path: dict[str, Mapping[str, Any]] = {}
    kinds: Counter[str] = Counter()
    for reference in references:
        if not isinstance(reference, Mapping):
            _error("bundle file reference is invalid")
        relative = _safe_relative_path(reference.get("relative_path"))
        _forbidden_filename(relative)
        if relative == BUNDLE_MANIFEST_NAME or relative in by_path:
            _error("bundle file inventory contains a duplicate path")
        if relative not in actual:
            _error("bundle manifest references a missing file")
        if not isinstance(reference.get("byte_size"), int) or isinstance(
            reference.get("byte_size"), bool
        ):
            _error("bundle file reference byte size is invalid")
        if reference["byte_size"] != len(actual[relative]):
            _error("bundle file size differs from retained bytes")
        digest = reference.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _error("bundle file reference digest is invalid")
        if digest != _sha256_bytes(actual[relative]):
            _error("bundle file hash differs from retained bytes")
        _validate_reference(
            reference,
            actual[relative],
            candidate=candidate,
            candidate_run_id=candidate_run_id,
            evidence_run_id=evidence_run_id,
        )
        by_path[relative] = reference
        kinds[str(reference["evidence_kind"])] += 1

    expected = set(by_path) | {BUNDLE_MANIFEST_NAME}
    if set(actual) != expected:
        _error("external bundle contains an orphan or unreferenced file")
    return by_path, kinds


def _require_bound_file(
    references: Mapping[str, Mapping[str, Any]],
    digest: str,
    *,
    allowed_kinds: frozenset[str],
    label: str,
) -> None:
    if not any(
        reference.get("sha256") == digest
        and reference.get("evidence_kind") in allowed_kinds
        for reference in references.values()
    ):
        _error(f"{label} has no retained evidence binding")


def _cross_bind(
    manifest: Mapping[str, Any],
    *,
    active: Mapping[str, Any],
    references: Mapping[str, Mapping[str, Any]],
    descriptor_sha256: str,
) -> None:
    active_candidate = active["candidate_binding"]
    expected_candidate = {
        "commit": active_candidate["source_commit"],
        "tree": active_candidate["source_tree"],
        "lock_sha256": active_candidate["lock_sha256"],
        "wheel_sha256": active_candidate["wheel_sha256"],
        "sdist_sha256": active_candidate["sdist_sha256"],
    }
    if manifest["candidate_binding"] != expected_candidate:
        _error("bundle candidate binding differs from active qualification")
    active_external = active["external_inputs"]
    external = manifest["external_inputs"]
    if external["semantic_gold_sha256"] != active_external["human_gold_manifest_sha256"]:
        _error("bundle semantic Gold binding differs from active qualification")
    if external["qualification_holdout_sha256"] != active_external["qualification_holdout_sha256"]:
        _error("bundle qualification holdout binding differs from active qualification")
    if external["final_blind_holdout_sha256"] != active_external["final_blind_holdout_sha256"]:
        _error("bundle final blind binding differs from active qualification")
    if (
        external["compiler_scorer_isolation_sha256"]
        != active_external["compiler_scorer_isolation_sha256"]
    ):
        _error("bundle scorer isolation binding differs from active qualification")
    if manifest["trusted_human_approver_descriptor_sha256"] != descriptor_sha256:
        _error("trusted human approver descriptor hash differs")

    _require_bound_file(
        references,
        expected_candidate["wheel_sha256"],
        allowed_kinds=_WHEEL_KINDS,
        label="candidate wheel",
    )
    _require_bound_file(
        references,
        expected_candidate["sdist_sha256"],
        allowed_kinds=_SDIST_KINDS,
        label="candidate sdist",
    )
    external_kinds = frozenset(
        {
            "typed_manifest",
            "typed_json",
            "human_gold_scorer",
            "post_build_gold_binding",
            "sanitized_supporting_receipt",
            "sanitized_text",
            "gate_result",
            "gate_collection",
            "sbom",
            "openvex",
            "licenses",
            "provenance",
            "candidate_platform_receipt",
            "host_event_sequence",
            "exact_wheel_execution",
            "legal_rows",
            "wiki_journey_rows",
            "context_capsule_selection_usage",
            "scale_report",
        }
    )
    for field in (
        "semantic_gold_sha256",
        "candidate_gold_binding_sha256",
        "qualification_holdout_sha256",
        "final_blind_holdout_sha256",
        "runner_sha256",
        "scorer_sha256",
        "compiler_scorer_isolation_sha256",
    ):
        _require_bound_file(
            references,
            external[field],
            allowed_kinds=external_kinds,
            label=field,
        )
    _require_bound_file(
        references,
        manifest["candidate_full_raw_inventory_sha256"],
        allowed_kinds=_INVENTORY_KINDS,
        label="Candidate Full raw inventory",
    )


def validate_external_bundle(
    root: Path,
    *,
    active_qualification: Path,
    trusted_human_approver: Path,
    expected_candidate_run_id: int,
    expected_evidence_run_id: int,
) -> dict[str, Any]:
    """Validate one current-only v3 bundle and return path-free derived data."""

    candidate_run = _positive_run(expected_candidate_run_id, label="candidate run id")
    evidence_run = _positive_run(expected_evidence_run_id, label="evidence run id")
    if candidate_run == evidence_run:
        _error("candidate and evidence run IDs must be distinct")
    selected_root, actual, total_bytes = _scan_root(Path(root))
    manifest_raw = actual[BUNDLE_MANIFEST_NAME]
    manifest_value = _strict_json_bytes(manifest_raw, label="bundle manifest")
    if not isinstance(manifest_value, dict):
        _error("bundle manifest must be an object")
    schema = _schema(BUNDLE_MANIFEST_SCHEMA, label="bundle manifest contract")
    _validate_schema(manifest_value, schema, label="bundle manifest")
    if manifest_value["schema_version"] != SCHEMA_VERSION:
        _error("bundle manifest is not the current v3 contract")
    if (
        _positive_run(manifest_value["candidate_run_id"], label="bundle candidate run id")
        != candidate_run
        or _positive_run(manifest_value["evidence_run_id"], label="bundle evidence run id")
        != evidence_run
    ):
        _error("bundle workflow run identity differs")
    if manifest_value["candidate_run_id"] == manifest_value["evidence_run_id"]:
        _error("bundle candidate and evidence run IDs must be distinct")
    if manifest_value["record_sha256"] != _record_sha256(manifest_value):
        _error("bundle manifest record digest differs")

    # The descriptor is intentionally loaded only after the bundle root is
    # established, so its location can be checked without ever accepting an
    # in-bundle trust record.
    _descriptor, _descriptor_raw, descriptor_sha = _load_trusted_descriptor(
        Path(trusted_human_approver), bundle_root=selected_root
    )
    active = _load_active(Path(active_qualification))
    candidate = _candidate_from_manifest(manifest_value)
    references, kind_counts = _reference_index(
        manifest_value,
        root=selected_root,
        actual=actual,
        candidate=candidate,
        candidate_run_id=candidate_run,
        evidence_run_id=evidence_run,
    )
    _cross_bind(
        manifest_value,
        active=active,
        references=references,
        descriptor_sha256=descriptor_sha,
    )
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "candidate_run_id": candidate_run,
        "evidence_run_id": evidence_run,
        "file_count": len(actual),
        "referenced_file_count": len(references),
        "total_bytes": total_bytes,
        "bundle_manifest_sha256": _sha256_bytes(manifest_raw),
        "candidate_binding_sha256": _sha256_bytes(
            _canonical_json(candidate).encode("utf-8")
        ),
        "external_inputs_sha256": _sha256_bytes(
            _canonical_json(manifest_value["external_inputs"]).encode("utf-8")
        ),
        "trusted_human_approver_descriptor_sha256": descriptor_sha,
        "candidate_full_raw_inventory_sha256": manifest_value[
            "candidate_full_raw_inventory_sha256"
        ],
        "evidence_kind_counts": dict(sorted(kind_counts.items())),
    }


def validate_external_bundle_v3(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Explicit versioned alias for integrations that avoid unversioned names."""

    return validate_external_bundle(*args, **kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a current-only External Qualification Bundle v3."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--active-qualification", type=Path, required=True)
    parser.add_argument("--candidate-run-id", type=int, required=True)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    parser.add_argument("--trusted-human-approver", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate_external_bundle(
            args.root,
            active_qualification=args.active_qualification,
            trusted_human_approver=args.trusted_human_approver,
            expected_candidate_run_id=args.candidate_run_id,
            expected_evidence_run_id=args.evidence_run_id,
        )
    except (OSError, ExternalQualificationBundleV3Error, ValueError):
        print("external qualification bundle v3 validation failed", file=sys.stderr)
        return 1
    print(_canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BUNDLE_MANIFEST_NAME",
    "BUNDLE_MANIFEST_SCHEMA",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "ExternalQualificationBundleError",
    "ExternalQualificationBundleV3Error",
    "main",
    "validate_external_bundle",
    "validate_external_bundle_v3",
]

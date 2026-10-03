"""Fail-closed validation for a machine-only External Qualification Bundle v4.

The v4 boundary has no human approver input.  It accepts only evidence whose
bytes, typed envelope, candidate binding, run binding, machine reference, and
two-scorer/arbiter isolation can be reopened and checked locally.  A successful
return is a boundary-validation result; it is not a Gate decision and it never
sets ``release_ready`` or a product claim.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as DefusedET
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.qualification_artifact_safety import (
    FORBIDDEN_FILENAME_RE as _FORBIDDEN_NAME,
)
from benchmarks.release.qualification_evidence_core import (
    canonical_json_bytes as _core_canonical_json_bytes,
)
from benchmarks.release.qualification_evidence_core import (
    digest_without as _core_digest_without,
)
from benchmarks.release.qualification_evidence_core import (
    regular_file_bytes as _core_regular_file_bytes,
)
from benchmarks.release.qualification_evidence_core import (
    safe_relative_posix as _core_safe_relative_posix,
)
from benchmarks.release.qualification_evidence_core import (
    safe_root_directory as _core_safe_root_directory,
)
from benchmarks.release.qualification_evidence_core import (
    sha256_bytes as _core_sha256_bytes,
)
from benchmarks.release.qualification_evidence_core import (
    strict_json_bytes as _core_strict_json_bytes,
)
from benchmarks.release.security_domain_receipt import (
    ROLES as _SECURITY_DOMAIN_ROLES,
)
from benchmarks.release.security_domain_receipt import (
    SecurityDomainReceiptError as _SecurityDomainReceiptError,
)
from benchmarks.release.security_domain_receipt import (
    security_domain_set_sha256 as _security_domain_set_sha256,
)
from benchmarks.release.security_domain_receipt import (
    validate_security_domain_receipt as _validate_security_domain_receipt,
)

MAX_FILES = 10_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_ACTIVE_BYTES = 8 * 1024 * 1024
BUNDLE_MANIFEST_NAME = "bundle-manifest.json"
SCHEMA_VERSION = "deeplaw.external-qualification-bundle-manifest/v4"
VALIDATION_SCHEMA_VERSION = "deeplaw.external-qualification-bundle-validation/v4"
REPOSITORY = Path(__file__).resolve().parents[2]
BUNDLE_MANIFEST_SCHEMA = (
    REPOSITORY / "contracts/external-qualification-bundle-manifest.v4.schema.json"
)
TYPED_EVIDENCE_SCHEMA = REPOSITORY / "contracts/typed-qualification-evidence.v2.schema.json"
EXACT_WHEEL_RUNNER_SOURCE = REPOSITORY / "benchmarks/release/exact_wheel_runner.py"
CANDIDATE_BINDING_SCHEMA = (
    REPOSITORY / "contracts/candidate-gold-binding-receipt.v2.schema.json"
)
SEMANTIC_REFERENCE_SCHEMA = REPOSITORY / "contracts/semantic-machine-reference.v1.schema.json"
MACHINE_REVIEWER_OUTPUT_SCHEMA = (
    REPOSITORY / "contracts/machine-reviewer-output.v1.schema.json"
)
SECURITY_DOMAIN_SCHEMA = REPOSITORY / "contracts/security-domain-receipt.v1.schema.json"

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

# These fields are policy receipts, not secret values.  They are kept closed
# so a future producer cannot use an arbitrary ``auth_*`` field as an escape.
_SAFE_FALSE_FIELDS = frozenset(
    {
        "auth_file_read",
        "auth_store_read",
        "authentication_material_read",
        "credential_value_recorded",
    }
)
_SAFE_LITERAL_FIELDS = {
    "auth_material_access": {"forbidden", False},
    "secret_visibility": {"forbidden"},
    "secret_policy": {"forbidden", "broker_only_exact_host"},
    "human_authenticity": {"not_claimed"},
    "reference_provenance": {"agent_consensus"},
    "auth_status_command": {"codex login status"},
}
_SAFE_DIGEST_FIELDS = frozenset(
    {
        "auth_file_sha256",
        "auth_store_sha256",
        "authentication_material_sha256",
        "credential_path_sha256",
        "credential_value_sha256",
    }
)

_TYPED_KINDS = frozenset(
    {
        "candidate_full_junit",
        "candidate_platform_receipt",
        "host_event_sequence",
        "exact_wheel_execution",
        "legal_rows",
        "wiki_journey_rows",
        "context_capsule_selection_usage",
        "scale_report",
        "retained_supply_chain",
        "machine_reference_scorer",
    }
)
_JSON_KINDS = frozenset(
    {
        "typed_manifest",
        "typed_json",
        "candidate_full_raw_inventory",
        "candidate_full_junit",
        "candidate_platform_receipt",
        "host_event_sequence",
        "exact_wheel_execution",
        "legal_rows",
        "wiki_journey_rows",
        "context_capsule_selection_usage",
        "scale_report",
        "retained_supply_chain",
        "machine_reference_scorer",
        "machine_candidate_output",
        "machine_candidate_execution",
        "sbom",
        "openvex",
        "licenses",
        "provenance",
        "gate_result",
        "gate_collection",
        "commercial_release_template",
        "post_build_machine_reference_binding",
        "semantic_machine_reference",
        "agent_roster",
        "agent_consensus",
        "agent_isolation",
        "machine_reviewer_output",
        "sanitized_supporting_receipt",
        "security_domain_receipt",
    }
)
_TEXT_KINDS = frozenset({"sanitized_text", "original_legal_html", "original_legal_markdown"})
_BINARY_KINDS = frozenset(
    {"original_legal_pdf", "original_legal_docx", "retained_wheel", "retained_sdist"}
)
_CANDIDATE_RUN_KINDS = frozenset(
    {"candidate_full_junit", "candidate_platform_receipt", "retained_supply_chain"}
)

# These are the Core v8 typed receipt counts.  One host event sequence is
# reused by secret isolation, timeline, and the three Codex/OpenCode cases.
_REQUIRED_TYPED_COUNTS = {
    "candidate_full_junit": 1,
    "candidate_platform_receipt": 1,
    "host_event_sequence": 6,
    "exact_wheel_execution": 1,
    "machine_reference_scorer": 2,
    "legal_rows": 1,
    "wiki_journey_rows": 1,
    "context_capsule_selection_usage": 1,
    "scale_report": 1,
    "retained_supply_chain": 1,
}


class ExternalQualificationBundleV4Error(ValueError):
    """Raised when a v4 bundle is absent, unsafe, open, or mis-bound."""


ExternalQualificationBundleError = ExternalQualificationBundleV4Error


def _error(message: str) -> None:
    raise ExternalQualificationBundleV4Error(message)


def _sha256_bytes(raw: bytes) -> str:
    return _core_sha256_bytes(raw)


def _canonical_json(value: Any) -> str:
    return _core_canonical_json_bytes(
        value,
        error_type=ExternalQualificationBundleV4Error,
    ).decode("utf-8")


def _record_sha256(value: Mapping[str, Any]) -> str:
    return _core_digest_without(
        value,
        field="record_sha256",
        error_type=ExternalQualificationBundleV4Error,
    )


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _error("strict JSON contains duplicate keys")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    _error("strict JSON contains a non-finite number")


def _check_json_projection(value: Any, *, depth: int = 0) -> None:
    """Reject secrets, private paths, unsupported values, and non-finite numbers."""

    if depth > MAX_JSON_DEPTH:
        _error("JSON exceeds its depth bound")
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ExternalQualificationBundleV4Error("JSON contains invalid Unicode") from error
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
            elif key in _SAFE_LITERAL_FIELDS:
                if not any(item == allowed for allowed in _SAFE_LITERAL_FIELDS[key]):
                    _error("authentication receipt field is invalid")
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
    return _core_strict_json_bytes(
        raw,
        label=label,
        error_type=ExternalQualificationBundleV4Error,
        projection=_check_json_projection,
    )


def _has_symlink_component(path: Path) -> bool:
    parts = path.parts
    start = 1 if path.is_absolute() else 0
    for index in range(start, len(parts) + 1):
        try:
            if Path(*parts[:index]).is_symlink():
                return True
        except OSError:
            return True
    return False


def _regular_path(path: Path, *, label: str, max_bytes: int) -> tuple[Path, bytes]:
    return _core_regular_file_bytes(
        path,
        label=label,
        max_bytes=max_bytes,
        error_type=ExternalQualificationBundleV4Error,
    )


def _exact_wheel_runner_identity() -> dict[str, str]:
    _path, raw = _regular_path(
        EXACT_WHEEL_RUNNER_SOURCE,
        label="exact-wheel runner source",
        max_bytes=MAX_FILE_BYTES,
    )
    return {
        "identity": "exact-wheel-runner:v2",
        "sha256": _sha256_bytes(raw),
    }


def _safe_root(root: Path) -> Path:
    try:
        return _core_safe_root_directory(
            root,
            label="external bundle root",
            error_type=ExternalQualificationBundleV4Error,
        )
    except ExternalQualificationBundleV4Error as error:
        raise ExternalQualificationBundleV4Error("external bundle root is unavailable") from error


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_PATH.fullmatch(value):
        _error("file reference path is not a safe relative POSIX path")
    try:
        return _core_safe_relative_posix(
            value,
            label="file reference path",
            error_type=ExternalQualificationBundleV4Error,
        )
    except ExternalQualificationBundleV4Error as error:
        raise ExternalQualificationBundleV4Error(
            "file reference path is not a safe relative POSIX path"
        ) from error


def _forbidden_filename(relative_path: str) -> None:
    for component in relative_path.split("/"):
        if component == ".env" or _FORBIDDEN_NAME.search(component):
            _error("bundle contains a forbidden filename")


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
    except ExternalQualificationBundleV4Error:
        raise
    except OSError as error:
        raise ExternalQualificationBundleV4Error(
            "external bundle inventory is unavailable"
        ) from error
    if BUNDLE_MANIFEST_NAME not in actual:
        _error("external bundle manifest is missing")
    return selected, actual, total


def _load_schema(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ExternalQualificationBundleV4Error(f"{label} is unavailable") from error
    # Repository contracts are trusted code inputs.  Keep duplicate-key and
    # non-finite-number rejection, but do not apply evidence Secret/path
    # projection to schema keywords such as ``auth_material_access``.
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except ExternalQualificationBundleV4Error:
        raise
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ExternalQualificationBundleV4Error(f"{label} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        _error(f"{label} must be an object")
    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:
        raise ExternalQualificationBundleV4Error(f"{label} is not a valid JSON Schema") from error
    return value


def _validate_schema(value: Any, schema: Mapping[str, Any], *, label: str) -> None:
    try:
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
            key=lambda item: list(item.path),
        )
    except Exception as error:
        raise ExternalQualificationBundleV4Error(f"{label} schema validation failed") from error
    if errors:
        location = ".".join(str(item) for item in errors[0].path)
        _error(
            f"{label} schema validation failed at {location or 'root'}: "
            f"{errors[0].validator}"
        )


def _positive_run(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _error(f"{label} is invalid")
    return value


def _candidate_from_manifest(manifest: Mapping[str, Any]) -> dict[str, str]:
    candidate = manifest.get("candidate_binding")
    if not isinstance(candidate, Mapping):
        _error("bundle candidate binding is missing")
    keys = ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256")
    result = {key: candidate.get(key) for key in keys}
    if not isinstance(result["commit"], str) or not _GIT.fullmatch(result["commit"]):
        _error("bundle candidate commit is invalid")
    if not isinstance(result["tree"], str) or not _GIT.fullmatch(result["tree"]):
        _error("bundle candidate tree is invalid")
    for key in keys[2:]:
        if not isinstance(result[key], str) or not _SHA256.fullmatch(result[key]):
            _error("bundle candidate artifact binding is invalid")
    return {key: str(result[key]) for key in keys}


def _file_policy(kind: str, media_type: str) -> str:
    if kind in _JSON_KINDS:
        if media_type != "application/json":
            _error("evidence kind and media type differ")
        return "json"
    if kind == "typed_xml":
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
    _error(f"unsupported evidence kind: {kind}")


def _check_text(raw: bytes, *, xml: bool = False) -> None:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ExternalQualificationBundleV4Error(
            "sanitized evidence must be strict UTF-8"
        ) from error
    if _ABSOLUTE_PATH.search(text) or _DRIVE_PATH.search(text) or _SECRET_VALUE.search(text):
        _error("evidence contains a Secret or private absolute path")
    if xml:
        try:
            DefusedET.fromstring(text)
        except Exception as error:
            raise ExternalQualificationBundleV4Error(
                "typed XML evidence is not well formed"
            ) from error


def _iter_source_refs(value: Any) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        if {"relative_path", "byte_size", "sha256", "media_type"} <= set(value):
            result.append(value)
        for item in value.values():
            result.extend(_iter_source_refs(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_iter_source_refs(item))
    return result


def _source_key(source: Mapping[str, Any]) -> tuple[str, int, str, str]:
    path = _safe_relative_path(source.get("relative_path"))
    size = source.get("byte_size")
    digest = source.get("sha256")
    media = source.get("media_type")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_FILE_BYTES:
        _error("typed source byte size is invalid")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        _error("typed source digest is invalid")
    if not isinstance(media, str):
        _error("typed source media type is invalid")
    return path, size, digest, media


def _check_source_refs(
    value: Mapping[str, Any],
    *,
    references: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, bytes],
) -> None:
    seen: set[tuple[str, int, str, str]] = set()
    for source in _iter_source_refs(value):
        key = _source_key(source)
        if key in seen:
            continue
        seen.add(key)
        path, size, digest, media = key
        if path not in actual or path not in references:
            _error("typed evidence source escapes the retained bundle")
        if size != len(actual[path]) or digest != _sha256_bytes(actual[path]):
            _error("typed evidence source digest differs from retained bytes")
        ref = references[path]
        if (
            ref.get("byte_size") != size
            or ref.get("sha256") != digest
            or ref.get("media_type") != media
        ):
            _error("typed evidence source reference differs from manifest")


def _reject_caller_pass_facts(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {
                "pass",
                "passed",
                "qualification_passed",
                "release_ready",
                "claim_eligible",
                "machine_qualification_claim_eligible",
                "core_gates_passed",
            }:
                _error("caller-authored qualification pass fact is forbidden")
            _reject_caller_pass_facts(item)
    elif isinstance(value, list):
        for item in value:
            _reject_caller_pass_facts(item)


def _check_record(value: Mapping[str, Any], *, label: str) -> None:
    record = value.get("record_sha256")
    if not isinstance(record, str) or not _SHA256.fullmatch(record):
        _error(f"{label} record digest is invalid")
    if record != _record_sha256(value):
        _error(f"{label} record digest differs")


def _validate_candidate_binding(
    value: Any,
    *,
    candidate: Mapping[str, str],
    references: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    binding_path: str,
) -> dict[str, Any]:
    schema = _load_schema(CANDIDATE_BINDING_SCHEMA, label="candidate binding contract")
    _validate_schema(value, schema, label="candidate binding")
    if not isinstance(value, Mapping):
        _error("candidate binding must be an object")
    _check_record(value, label="candidate binding")
    if value.get("profile") != "machine_evaluated_no_human_attestation":
        _error("candidate binding profile is invalid")
    bound = value.get("candidate")
    if not isinstance(bound, Mapping):
        _error("candidate binding candidate is missing")
    if {
        "commit": bound.get("commit"),
        "tree": bound.get("tree"),
        "lock_sha256": bound.get("lock_sha256"),
    } != {
        "commit": candidate["commit"],
        "tree": candidate["tree"],
        "lock_sha256": candidate["lock_sha256"],
    }:
        _error("candidate binding source differs from bundle candidate")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, Mapping):
        _error("candidate binding artifacts are missing")
    for artifact_name, candidate_key, evidence_kind in (
        ("wheel", "wheel_sha256", "retained_wheel"),
        ("sdist", "sdist_sha256", "retained_sdist"),
    ):
        artifact = artifacts.get(artifact_name)
        if not isinstance(artifact, Mapping) or artifact.get("sha256") != candidate[candidate_key]:
            _error("candidate binding artifact differs from bundle candidate")
        if not isinstance(artifact.get("byte_size"), int) or artifact["byte_size"] < 1:
            _error("candidate binding artifact size is invalid")
        matching = [
            item
            for item in references.values()
            if item.get("evidence_kind") == evidence_kind
            and item.get("sha256") == candidate[candidate_key]
            and item.get("byte_size") == artifact["byte_size"]
        ]
        if len(matching) != 1:
            _error("candidate retained artifact is not uniquely bound")
    semantic = value.get("semantic_reference")
    external = manifest["external_inputs"]
    if (
        not isinstance(semantic, Mapping)
        or semantic.get("sha256") != external["semantic_reference_sha256"]
    ):
        _error("candidate semantic reference binding differs")
    if semantic.get("schema_version") != "deeplaw.semantic-machine-reference/v1":
        _error("candidate semantic reference schema differs")
    for field, label in (
        ("agent_roster", "agent roster"),
        ("agent_consensus", "agent consensus"),
        ("agent_isolation", "agent isolation"),
    ):
        item = value.get(field)
        if not isinstance(item, Mapping) or item.get("sha256") != external[f"{field}_sha256"]:
            _error(f"candidate {label} binding differs")
    holdout = value.get("holdout")
    blind = value.get("blind")
    if (
        not isinstance(holdout, Mapping)
        or holdout.get("sha256") != external["qualification_holdout_sha256"]
    ):
        _error("candidate qualification holdout binding differs")
    if (
        not isinstance(blind, Mapping)
        or blind.get("sha256") != external["final_blind_holdout_sha256"]
    ):
        _error("candidate final blind binding differs")
    panel = value.get("scorer_panel")
    arbiter = value.get("arbiter")
    runner = value.get("runner")
    if (
        not isinstance(panel, Mapping)
        or not isinstance(arbiter, Mapping)
        or not isinstance(runner, Mapping)
    ):
        _error("candidate scorer panel binding is incomplete")
    _validate_panel(panel, arbiter, external)
    if runner.get("sha256") != external["runner_sha256"]:
        _error("candidate runner binding differs")
    _require_bound_file(
        references,
        external["semantic_reference_sha256"],
        allowed_kinds=frozenset({"semantic_machine_reference"}),
        label="semantic reference",
    )
    _require_bound_file(
        references,
        external["agent_roster_sha256"],
        allowed_kinds=frozenset({"agent_roster"}),
        label="agent roster",
    )
    _require_bound_file(
        references,
        external["agent_consensus_sha256"],
        allowed_kinds=frozenset({"agent_consensus"}),
        label="agent consensus",
    )
    _require_bound_file(
        references,
        external["agent_isolation_sha256"],
        allowed_kinds=frozenset({"agent_isolation"}),
        label="agent isolation",
    )
    _require_bound_file(
        references,
        _sha256_bytes(actual[binding_path]),
        allowed_kinds=frozenset({"post_build_machine_reference_binding"}),
        label="candidate binding",
    )
    return dict(value)


def _panel_digest(panel: Mapping[str, Any]) -> str:
    body = {
        "scorer_a": panel.get("scorer_a"),
        "scorer_b": panel.get("scorer_b"),
    }
    return _sha256_bytes(_canonical_json(body).encode("utf-8"))


def _validate_panel(
    panel: Mapping[str, Any],
    arbiter: Mapping[str, Any],
    external: Mapping[str, Any],
) -> None:
    scorer_a = panel.get("scorer_a")
    scorer_b = panel.get("scorer_b")
    if not isinstance(scorer_a, Mapping) or not isinstance(scorer_b, Mapping):
        _error("scorer panel is incomplete")
    if (
        scorer_a.get("role") != "independent_scorer_a"
        or scorer_b.get("role") != "independent_scorer_b"
    ):
        _error("scorer panel roles are invalid")
    if (
        scorer_a.get("identity") == scorer_b.get("identity")
        or scorer_a.get("sha256") == scorer_b.get("sha256")
    ):
        _error("scorer panel identities are not distinct")
    if panel.get("distinct_scorers") is not True:
        _error("scorer panel does not assert distinct scorers")
    panel_sha = panel.get("panel_sha256")
    if not isinstance(panel_sha, str) or not _SHA256.fullmatch(panel_sha):
        _error("scorer panel digest is invalid")
    if panel_sha != _panel_digest(panel):
        _error("scorer panel digest is not recomputed from both scorers")
    if arbiter.get("role") != "deterministic_arbiter":
        _error("arbiter role is invalid")
    if not isinstance(arbiter.get("identity"), str) or not isinstance(arbiter.get("sha256"), str):
        _error("arbiter identity is invalid")
    if arbiter.get("sha256") in {scorer_a.get("sha256"), scorer_b.get("sha256")}:
        _error("arbiter identity is not distinct from scorer identities")
    scorer_fields = [
        key
        for key in ("scorer_sha256", "scorer_panel_sha256", "scorer_panel_digest_sha256")
        if key in external
    ]
    if not scorer_fields:
        _error("bundle scorer panel digest is missing")
    for key in scorer_fields:
        if external[key] != panel_sha:
            _error("bundle scorer panel digest differs")
    for key in ("arbiter_sha256", "arbitration_sha256"):
        if key in external and external[key] != arbiter["sha256"]:
            _error("bundle arbiter digest differs")
    for key, expected in (
        ("scorer_a_sha256", scorer_a.get("sha256")),
        ("scorer_b_sha256", scorer_b.get("sha256")),
    ):
        if key in external and external[key] != expected:
            _error("bundle scorer identity digest differs")


def _require_bound_file(
    references: Mapping[str, Mapping[str, Any]],
    digest: str,
    *,
    allowed_kinds: frozenset[str],
    label: str,
) -> None:
    if not any(
        reference.get("sha256") == digest and reference.get("evidence_kind") in allowed_kinds
        for reference in references.values()
    ):
        _error(f"{label} has no retained evidence binding")


def _validate_semantic_reference(
    value: Mapping[str, Any],
    *,
    raw_digest: str,
    external: Mapping[str, Any],
) -> None:
    schema = _load_schema(SEMANTIC_REFERENCE_SCHEMA, label="semantic machine reference contract")
    _validate_schema(value, schema, label="semantic machine reference")
    _check_record(value, label="semantic machine reference")
    review = value.get("agent_review")
    if not isinstance(review, Mapping):
        _error("semantic machine reference review is missing")
    reviewers = review.get("reviewers")
    if not isinstance(reviewers, list) or len(reviewers) < 3:
        _error("semantic machine reference needs at least three reviewers")
    ids: set[str] = set()
    process_ids: set[str] = set()
    output_ids: set[str] = set()
    for reviewer in reviewers:
        if not isinstance(reviewer, Mapping):
            _error("semantic machine reviewer is invalid")
        agent_id = reviewer.get("agent_id")
        if not isinstance(agent_id, str) or agent_id in ids:
            _error("semantic machine reviewers are not distinct")
        ids.add(agent_id)
        process_id = reviewer.get("process_identity_sha256")
        output_id = reviewer.get("output_sha256")
        if (
            not isinstance(process_id, str)
            or process_id in process_ids
            or not isinstance(output_id, str)
            or output_id in output_ids
        ):
            _error("semantic machine reviewer process/output identities are not distinct")
        process_ids.add(process_id)
        output_ids.add(output_id)
        # Legacy semantic-panel metadata only; OS-domain isolation is checked
        # independently by _validate_security_domains below.
        if (
            reviewer.get("conclusions_hidden_from_peers") is not True
            or reviewer.get("separate_process") is not True
        ):
            _error("semantic machine reviewer isolation is incomplete")
    if (
        review.get("minimum_distinct_agents") != len(ids)
        and review.get("minimum_distinct_agents", 0) > len(ids)
    ):
        _error("semantic machine reviewer count is incomplete")
    if review.get("unanimity_required") is not True:
        _error("semantic machine reference does not require unanimity")
    if (
        value.get("human_claim_eligible") is not False
        or value.get("competitive_claim_eligible") is not False
    ):
        _error("semantic machine reference claim flags are invalid")
    if raw_digest != external["semantic_reference_sha256"]:
        _error("semantic machine reference bytes differ from external binding")


def _validate_inventory(
    value: Any,
    *,
    raw_digest: str,
    candidate_run_id: int,
    candidate: Mapping[str, str],
    references: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> set[tuple[str, int]]:
    if not isinstance(value, Mapping):
        _error("Candidate Full raw inventory must be an object")
    required = {"schema_version", "record_kind", "run_id", "head_sha", "path_policy", "files"}
    if set(value) != required:
        _error("Candidate Full raw inventory receipt schema is not current")
    if (
        value.get("schema_version") != "deeplaw.candidate-full-inventory-receipt/v1"
        or value.get("record_kind") != "candidate_full_raw_inventory"
    ):
        _error("Candidate Full raw inventory kind is invalid")
    if value.get("run_id") != candidate_run_id or value.get("head_sha") != candidate["commit"]:
        _error("Candidate Full raw inventory identity differs")
    if value.get("path_policy") != "logical_relative_paths_only":
        _error("Candidate Full raw inventory path policy differs")
    rows = value.get("files")
    if not isinstance(rows, list) or not rows:
        _error("Candidate Full raw inventory file list is missing")
    declared: dict[str, tuple[str, int]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"logical_path", "sha256", "bytes"}:
            _error("Candidate Full raw inventory row is not closed")
        logical_path = _safe_relative_path(row.get("logical_path"))
        digest = row.get("sha256")
        size = row.get("bytes")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _error("Candidate raw inventory digest is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_FILE_BYTES:
            _error("Candidate raw inventory byte size is invalid")
        if logical_path in declared:
            _error("Candidate raw inventory contains a duplicate path")
        declared[logical_path] = (digest, size)
    if raw_digest != manifest["candidate_full_raw_inventory_sha256"]:
        _error("Candidate Full raw inventory digest differs")
    # If raw Candidate Full bytes are retained in this bundle, reopen them.  A
    # receipt may also reference an independently retained Candidate Full root;
    # in that case the digest/size set is still required for typed closure.
    observed_pairs = {
        (reference["sha256"], reference["byte_size"])
        for path, reference in references.items()
        if reference.get("evidence_kind") in _CANDIDATE_RUN_KINDS and path in actual
    }
    declared_pairs = set(declared.values())
    candidate_sources = []
    for path, reference in references.items():
        if reference.get("evidence_kind") in _CANDIDATE_RUN_KINDS:
            candidate_sources.append((path, reference["sha256"], reference["byte_size"]))
    for _path, digest, size in candidate_sources:
        if (digest, size) not in declared_pairs:
            _error("Candidate Full raw inventory does not close candidate evidence")
    if observed_pairs and not observed_pairs <= declared_pairs:
        _error("Candidate Full raw inventory does not match retained bytes")
    return declared_pairs


def _validate_machine_scorer(
    value: Mapping[str, Any],
    *,
    references: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, bytes],
    candidate: Mapping[str, str],
    external: Mapping[str, Any],
    candidate_binding: Mapping[str, Any],
) -> tuple[str, str, str]:
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        _error("machine reference scorer payload is missing")
    _check_source_refs(payload, references=references, actual=actual)
    process = payload.get("process_identity")
    if not isinstance(process, Mapping):
        _error("machine reference scorer process identity is missing")
    process_keys = (
        "scorer_a_process_id",
        "scorer_b_process_id",
        "runner_process_id",
        "arbiter_process_id",
    )
    ids = [process.get(key) for key in process_keys]
    if any(not isinstance(item, str) for item in ids) or len(set(ids)) != 4:
        _error("machine reference scorer processes are not distinct")
    domain_sources = payload.get("security_domain_receipt_sources")
    if not isinstance(domain_sources, list) or len(domain_sources) != len(_SECURITY_DOMAIN_ROLES):
        _error("machine reference scorer security-domain receipts are incomplete")
    _check_source_refs(
        {"security_domain_receipt_sources": domain_sources},
        references=references,
        actual=actual,
    )
    domain_digests = {
        source.get("sha256")
        for source in domain_sources
        if isinstance(source, Mapping)
    }
    retained_domain_digests = {
        reference.get("sha256")
        for reference in references.values()
        if reference.get("evidence_kind") == "security_domain_receipt"
    }
    if domain_digests != retained_domain_digests:
        _error("machine reference scorer security-domain receipt binding differs")
    panel = candidate_binding["scorer_panel"]
    arbiter = candidate_binding["arbiter"]
    runner = candidate_binding["runner"]
    expected_process_digests = {
        "scorer_a_identity_sha256": panel["scorer_a"]["sha256"],
        "scorer_b_identity_sha256": panel["scorer_b"]["sha256"],
        "runner_identity_sha256": runner["sha256"],
        "arbiter_identity_sha256": arbiter["sha256"],
    }
    for key, expected in expected_process_digests.items():
        if process.get(key) != expected:
            _error("machine reference scorer process identity differs")
    for key, expected in (
        ("candidate_binding", candidate),
        ("runner", runner),
        ("scorer_panel", panel),
        ("arbiter", arbiter),
    ):
        if value.get(key) != expected:
            _error("typed machine evidence binding differs")
    corpus = value.get("corpus")
    if (
        not isinstance(corpus, Mapping)
        or corpus.get("role") not in {"qualification_holdout", "final_blind"}
    ):
        _error("machine reference scorer corpus role is invalid")
    role = str(corpus["role"])
    expected_corpus = (
        external["qualification_holdout_sha256"]
        if role == "qualification_holdout"
        else external["final_blind_holdout_sha256"]
    )
    if corpus.get("sha256") != expected_corpus:
        _error("machine reference scorer corpus binding differs")
    source_fields = (
        "scorer_a_rows_source",
        "scorer_b_rows_source",
        "arbiter_consensus_rows_source",
    )
    row_hashes: list[str] = []
    for field in source_fields:
        source = payload.get(field)
        if not isinstance(source, Mapping):
            _error("machine reference scorer raw rows are incomplete")
        path, _size, digest, _media = _source_key(source)
        if path not in actual:
            _error("machine reference scorer rows are not retained")
        rows = _strict_json_bytes(actual[path], label="machine reference scorer rows")
        _reject_caller_pass_facts(rows)
        row_hashes.append(digest)
    if row_hashes[0] == row_hashes[1] or row_hashes[2] in {row_hashes[0], row_hashes[1]}:
        _error("machine reference scorer rows are not independently retained")
    scorer_a = value.get("scorer_panel", {}).get("scorer_a", {})
    scorer_b = value.get("scorer_panel", {}).get("scorer_b", {})
    return role, str(scorer_a.get("identity")), str(scorer_b.get("identity"))


def _validate_typed(
    value: Any,
    *,
    kind: str,
    candidate: Mapping[str, str],
    candidate_run_id: int,
    evidence_run_id: int,
    references: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, bytes],
    external: Mapping[str, Any],
    candidate_binding: Mapping[str, Any],
    candidate_inventory_sha256: str,
) -> tuple[str | None, str | None, str | None]:
    if not isinstance(value, Mapping):
        _error("typed evidence must be an object")
    if value.get("schema_version") != "deeplaw.typed-qualification-evidence/v2":
        _error("typed evidence is not the v2 contract")
    declared = value.get("kind")
    if declared != kind:
        _error("typed evidence kind differs from its bundle reference")
    schema = _load_schema(TYPED_EVIDENCE_SCHEMA, label="typed qualification evidence contract")
    _validate_schema(value, schema, label=f"typed qualification evidence {kind}")
    _check_record(value, label="typed qualification evidence")
    if (
        value.get("profile") != "machine_evaluated_no_human_attestation"
        or value.get("human_authenticity") != "not_claimed"
    ):
        _error("typed evidence human profile is invalid")
    if value.get("candidate_binding") != candidate:
        _error("typed evidence candidate binding differs")
    run = value.get("run_binding")
    if not isinstance(run, Mapping):
        _error("typed evidence run binding is missing")
    expected_workflow = candidate_run_id if kind in _CANDIDATE_RUN_KINDS else evidence_run_id
    if run.get("workflow_run_id") != expected_workflow:
        _error("typed evidence workflow run binding differs")
    corpus = value.get("corpus")
    if not isinstance(corpus, Mapping):
        _error("typed evidence corpus binding is missing")
    if kind in _CANDIDATE_RUN_KINDS and corpus.get("role") != "candidate_full":
        _error("candidate typed evidence corpus role differs")
    if kind == "exact_wheel_execution" and corpus.get("role") != "candidate_full":
        _error("exact-wheel typed evidence corpus role differs")
    if (
        kind == "exact_wheel_execution"
        and corpus.get("sha256") != candidate_inventory_sha256
    ):
        _error("exact-wheel typed evidence does not bind Candidate Full raw inventory")
    if (
        kind not in _CANDIDATE_RUN_KINDS
        and kind != "exact_wheel_execution"
        and corpus.get("role") not in {"qualification_holdout", "final_blind"}
    ):
        _error("qualification typed evidence corpus role differs")
    if (
        kind == "exact_wheel_execution"
        and value.get("runner") != _exact_wheel_runner_identity()
    ):
        _error("exact-wheel typed evidence runner identity differs")
    _check_source_refs(value, references=references, actual=actual)
    if kind == "machine_reference_scorer":
        arbiter = candidate_binding.get("arbiter")
        if (
            value.get("scorer_panel") != candidate_binding.get("scorer_panel")
            or value.get("arbiter") != arbiter
            or not isinstance(arbiter, Mapping)
            or value.get("scorer")
            != {"identity": arbiter.get("identity"), "sha256": arbiter.get("sha256")}
        ):
            _error("typed scorer panel, arbiter, or compatibility scorer binding differs")
        return _validate_machine_scorer(
            value,
            references=references,
            actual=actual,
            candidate=candidate,
            external=external,
            candidate_binding=candidate_binding,
        )
    return None, None, None


def _reference_index(
    manifest: Mapping[str, Any],
    *,
    actual: Mapping[str, bytes],
) -> tuple[dict[str, Mapping[str, Any]], Counter[str]]:
    references = manifest.get("files")
    if not isinstance(references, list) or not references:
        _error("bundle file inventory is missing")
    by_path: dict[str, Mapping[str, Any]] = {}
    kind_counts: Counter[str] = Counter()
    typed_values: list[tuple[str, Mapping[str, Any]]] = []
    for reference in references:
        if not isinstance(reference, Mapping):
            _error("bundle file reference is invalid")
        relative = _safe_relative_path(reference.get("relative_path"))
        _forbidden_filename(relative)
        if relative == BUNDLE_MANIFEST_NAME or relative in by_path:
            _error("bundle file inventory contains a duplicate path")
        if relative not in actual:
            if (
                reference.get("evidence_kind") == "machine_reviewer_output"
            ):
                _error("reviewer output is missing from the retained bundle")
            _error("bundle manifest references a missing file")
        size = reference.get("byte_size")
        if isinstance(size, bool) or not isinstance(size, int) or size != len(actual[relative]):
            _error("bundle file size differs from retained bytes")
        digest = reference.get("sha256")
        if (
            not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or digest != _sha256_bytes(actual[relative])
        ):
            _error("bundle file hash differs from retained bytes")
        kind = reference.get("evidence_kind")
        media = reference.get("media_type")
        if not isinstance(kind, str) or not isinstance(media, str):
            _error("bundle file reference is invalid")
        mode = _file_policy(kind, media)
        if mode == "json":
            label = (
                "reviewer output"
                if kind == "machine_reviewer_output"
                else "bundle JSON evidence"
            )
            value = _strict_json_bytes(actual[relative], label=label)
            typed_values.append((kind, value))
        elif mode == "xml":
            _check_text(actual[relative], xml=True)
        elif mode == "text":
            _check_text(actual[relative])
        by_path[relative] = reference
        kind_counts[kind] += 1
    if set(actual) != set(by_path) | {BUNDLE_MANIFEST_NAME}:
        _error("external bundle contains an orphan or unreferenced file")
    # Validation of candidate binding and semantic reference needs the complete
    # source index, so callers perform the typed pass after this function.
    return by_path, kind_counts


def _validate_reviewer_outputs(
    reviewers: list[Any],
    *,
    reference_id: str,
    review: Mapping[str, Any],
    references: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, bytes],
) -> tuple[list[str], bool, list[str]]:
    """Reopen every retained reviewer output and derive the consensus inputs.

    The semantic reference and its consensus receipt are caller-provided
    bindings.  They are therefore not sufficient evidence on their own: each
    ``output_sha256`` must resolve to one retained raw output, and that output
    must independently bind the reviewer identity and frozen review inputs.
    Only the raw bytes are used to derive the returned output digests,
    unanimity, and disagreement set.
    """

    schema = _load_schema(
        MACHINE_REVIEWER_OUTPUT_SCHEMA,
        label="machine reviewer output contract",
    )
    output_paths: set[str] = set()
    agent_ids: set[str] = set()
    process_ids: set[str] = set()
    output_ids: set[str] = set()
    decisions: list[str] = []
    disagreements: set[str] = set()
    output_sha256s: list[str] = []
    for reviewer in reviewers:
        if not isinstance(reviewer, Mapping):
            _error("reviewer output identity binding is invalid")
        expected_output_sha256 = reviewer.get("output_sha256")
        if not isinstance(expected_output_sha256, str) or not _SHA256.fullmatch(
            expected_output_sha256
        ):
            _error("reviewer output digest is invalid")
        paths = [
            path
            for path, reference in references.items()
            if reference.get("evidence_kind") == "machine_reviewer_output"
            and reference.get("sha256") == expected_output_sha256
        ]
        if len(paths) != 1:
            _error("reviewer output is not retained exactly once")
        path = paths[0]
        if path in output_paths:
            _error("reviewer output identities are not distinct")
        output_paths.add(path)
        raw = actual[path]
        try:
            output = _strict_json_bytes(raw, label="reviewer output")
        except ExternalQualificationBundleV4Error:
            raise
        except Exception as error:
            raise ExternalQualificationBundleV4Error("reviewer output is not parseable") from error
        if not isinstance(output, Mapping):
            _error("reviewer output must be an object")
        _validate_schema(output, schema, label="reviewer output")
        _check_record(output, label="reviewer output")
        raw_digest = _sha256_bytes(raw)
        if raw_digest != expected_output_sha256:
            _error("reviewer output digest differs from semantic reviewer")
        expected_identity = {
            "reference_id": reference_id,
            "agent_id": reviewer.get("agent_id"),
            "model_id": reviewer.get("model_id"),
            "rubric_sha256": review.get("rubric_sha256"),
            "source_corpus_sha256": review.get("source_corpus_sha256"),
            "process_identity_sha256": reviewer.get("process_identity_sha256"),
        }
        if any(output.get(field) != value for field, value in expected_identity.items()):
            _error("reviewer output identity or input hash binding differs")
        agent_id = output["agent_id"]
        process_id = output["process_identity_sha256"]
        if agent_id in agent_ids or process_id in process_ids:
            _error("reviewer output identities are not distinct")
        agent_ids.add(agent_id)
        process_ids.add(process_id)
        output_ids.add(raw_digest)
        decisions.append(output["decision"])
        disagreements.update(output["disagreements"])
        output_sha256s.append(raw_digest)
    if len(output_ids) != len(output_sha256s):
        _error("reviewer output identities are not distinct")
    derived_disagreements = sorted(disagreements)
    unanimous = bool(decisions) and all(decision == "approved" for decision in decisions)
    unanimous = unanimous and not derived_disagreements
    return output_sha256s, unanimous, derived_disagreements


def _validate_security_domains(
    references: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, bytes],
    *,
    manifest: Mapping[str, Any],
    candidate_binding: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """Require OS-observed, role-specific security domains for current v4.

    The old v1 ``agent_isolation`` booleans remain historical evidence.  They
    are not accepted as a substitute for these five self-bound receipts.
    """

    external = manifest.get("external_inputs")
    if not isinstance(external, Mapping):
        _error("bundle external inputs are missing")
    aggregate = external.get("security_domains_sha256")
    if not isinstance(aggregate, str) or not _SHA256.fullmatch(aggregate):
        _error("security domain receipt set digest is missing")
    security_schema = _load_schema(SECURITY_DOMAIN_SCHEMA, label="security domain receipt contract")
    paths = [
        (path, reference)
        for path, reference in references.items()
        if reference.get("evidence_kind") == "security_domain_receipt"
    ]
    if len(paths) != len(_SECURITY_DOMAIN_ROLES):
        _error("security domain receipt inventory must contain five roles")
    parsed: dict[str, Mapping[str, Any]] = {}
    for path, reference in paths:
        value = _strict_json_bytes(actual[path], label="security domain receipt")
        if not isinstance(value, Mapping):
            _error("security domain receipt must be an object")
        _validate_schema(value, security_schema, label="security domain receipt")
        try:
            receipt = _validate_security_domain_receipt(value)
        except _SecurityDomainReceiptError as error:
            raise ExternalQualificationBundleV4Error(str(error)) from error
        role = receipt["role"]
        if role in parsed:
            _error("security domain receipt roles are duplicated")
        if reference.get("sha256") != _sha256_bytes(actual[path]):
            _error("security domain receipt hash differs from retained bytes")
        parsed[role] = receipt
    if set(parsed) != set(_SECURITY_DOMAIN_ROLES):
        _error("security domain receipt roles are incomplete")
    for field, label in (
        ("domain_id", "domain"),
        ("ephemeral_runner_id", "runner"),
        ("namespace_id", "mount namespace"),
    ):
        values = [
            (
                receipt[field]
                if field == "domain_id"
                else receipt["runner"][field]
                if field == "ephemeral_runner_id"
                else receipt["mount"][field]
            )
            for receipt in parsed.values()
        ]
        if len(values) != len(set(values)):
            _error(f"security domain {label} identities are not distinct")
    ipc_namespaces = [receipt["ipc"]["namespace_id"] for receipt in parsed.values()]
    if len(ipc_namespaces) != len(set(ipc_namespaces)):
        _error("security domain IPC namespaces are shared")
    principal_ids = [receipt["principal"]["principal_id"] for receipt in parsed.values()]
    if len(principal_ids) != len(set(principal_ids)):
        _error("security domain principals are shared")
    for field, observed_key, label in (
        (
            "security_domain_executable_sha256",
            "executable_sha256",
            "executable",
        ),
        (
            "security_domain_process_tree_sha256",
            "process_tree_sha256",
            "process tree",
        ),
        (
            "security_domain_process_receipt_sha256",
            "process_receipt_sha256",
            "process receipt",
        ),
        (
            "security_domain_observed_roots_sha256",
            "observed_roots_sha256",
            "observed roots",
        ),
    ):
        expected = external.get(field)
        if not isinstance(expected, Mapping) or set(expected) != set(_SECURITY_DOMAIN_ROLES):
            _error(f"security domain {label} bindings are missing")
        for role, receipt in parsed.items():
            digest = expected.get(role)
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                _error(f"security domain {label} binding is invalid")
            observed = (
                receipt["executable"][observed_key]
                if observed_key in receipt["executable"]
                else receipt[observed_key]
            )
            if observed != digest:
                _error(f"security domain {label} hash drifted for {role}")
    retained_process_receipts = {
        reference.get("sha256")
        for reference in references.values()
        if reference.get("evidence_kind") == "sanitized_supporting_receipt"
    }
    for role, receipt in parsed.items():
        process_receipts = receipt["process_receipt_sha256s"]
        expected_count = 2 if role == "candidate_host" else 1
        if len(process_receipts) != expected_count:
            _error(f"security domain {role} process receipt inventory is incomplete")
        if not set(process_receipts) <= retained_process_receipts:
            _error(f"security domain {role} process receipt bytes are not retained")
    attester_digest = external.get("compiler_scorer_isolation_sha256")
    if not isinstance(attester_digest, str) or not _SHA256.fullmatch(attester_digest):
        _error("security domain attester executable binding is missing")
    for role, receipt in parsed.items():
        if receipt["attester_executable_sha256"] != attester_digest:
            _error(f"security domain attester executable hash drifted for {role}")

    expected_names = {
        "reference_freezer": ({"reference-cases", "reviewer-inputs"}, {"sealed-reference"}),
        "candidate_host": (
            {"verified-candidate-artifacts", "qualification-inputs", "final-blind-inputs"},
            {"candidate-sanitized-output"},
        ),
        "scorer_a": ({"candidate-sanitized-output", "sealed-reference"}, {"scorer-a-output"}),
        "scorer_b": ({"candidate-sanitized-output", "sealed-reference"}, {"scorer-b-output"}),
        "arbiter": ({"scorer-a-output", "scorer-b-output"}, {"arbiter-output"}),
    }
    producers: dict[str, tuple[str, str]] = {}
    for role, receipt in parsed.items():
        for artifact in receipt["egress"]:
            name = artifact["name"]
            identity = (role, artifact["sha256"])
            if name in producers:
                _error("security domain artifact has multiple producers")
            producers[name] = identity
    for _role, receipt in parsed.items():
        for artifact in receipt["ingress"]:
            produced = producers.get(artifact["name"])
            if produced is not None and produced[1] != artifact["sha256"]:
                _error("security domain producer/consumer artifact hash differs")
    forbidden_names = {
        "reference_freezer": {
            "candidate-sanitized-output",
            "scorer-a-output",
            "scorer-b-output",
            "arbiter-output",
        },
        "candidate_host": {
            "sealed-reference",
            "scorer-a-output",
            "scorer-b-output",
            "arbiter-output",
        },
        "scorer_a": {"scorer-b-output", "arbiter-output"},
        "scorer_b": {"scorer-a-output", "arbiter-output"},
        "arbiter": {"candidate-sanitized-output", "sealed-reference"},
    }
    for role, receipt in parsed.items():
        ingress = {item["name"] for item in receipt["ingress"]}
        egress = {item["name"] for item in receipt["egress"]}
        expected_ingress, expected_egress = expected_names[role]
        if ingress != expected_ingress or egress != expected_egress:
            _error(f"security domain {role} artifact visibility is not role-bounded")
        if role == "candidate_host":
            if receipt["secret_policy"] != "broker_only_exact_host":
                _error("candidate security domain Secret policy is not broker-only")
        elif receipt["secret_policy"] != "forbidden":
            _error("non-candidate security domain may receive Secret material")
        if role == "candidate_host":
            if receipt["network"]["policy"] != "host_provider_allowlist":
                _error("candidate security domain network policy is not provider allowlisted")
        elif receipt["network"]["policy"] != "deny_all":
            _error(f"security domain {role} network policy is not deny-all")
        canary_targets = {
            item["name"] for item in receipt["negative_canary"]["targets"]
        }
        if canary_targets != forbidden_names[role]:
            _error(f"security domain {role} prohibited artifact visibility is incomplete")
    scorer_panel = candidate_binding.get("scorer_panel")
    arbiter_binding = candidate_binding.get("arbiter")
    runner_binding = candidate_binding.get("runner")
    if (
        not isinstance(scorer_panel, Mapping)
        or not isinstance(arbiter_binding, Mapping)
        or not isinstance(runner_binding, Mapping)
    ):
        _error("security domain executable candidate bindings are incomplete")
    scorer_a_binding = scorer_panel.get("scorer_a")
    scorer_b_binding = scorer_panel.get("scorer_b")
    if not isinstance(scorer_a_binding, Mapping) or not isinstance(
        scorer_b_binding, Mapping
    ):
        _error("security domain scorer executable bindings are incomplete")
    executable_bindings = {
        "candidate_host": runner_binding.get("sha256"),
        "scorer_a": scorer_a_binding.get("sha256"),
        "scorer_b": scorer_b_binding.get("sha256"),
        "arbiter": arbiter_binding.get("sha256"),
    }
    for role, expected in executable_bindings.items():
        if parsed[role]["executable"]["executable_sha256"] != expected:
            _error(f"security domain executable is not the frozen {role} input")
    try:
        observed_aggregate = _security_domain_set_sha256(list(parsed.values()))
    except _SecurityDomainReceiptError as error:
        raise ExternalQualificationBundleV4Error(str(error)) from error
    if observed_aggregate != aggregate:
        _error("security domain receipt set digest differs")
    return parsed


def _validate_auxiliary_sources(
    references: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, bytes],
    *,
    manifest: Mapping[str, Any],
) -> None:
    semantic_paths = [
        path
        for path, ref in references.items()
        if ref.get("evidence_kind") == "semantic_machine_reference"
    ]
    if len(semantic_paths) != 1:
        _error("semantic machine reference must be retained exactly once")
    semantic = _strict_json_bytes(actual[semantic_paths[0]], label="semantic machine reference")
    if not isinstance(semantic, Mapping):
        _error("semantic machine reference must be an object")
    _validate_semantic_reference(
        semantic,
        raw_digest=_sha256_bytes(actual[semantic_paths[0]]),
        external=manifest["external_inputs"],
    )
    auxiliary: dict[str, tuple[Mapping[str, Any], str]] = {}
    for kind in ("agent_roster", "agent_consensus", "agent_isolation"):
        paths = [path for path, ref in references.items() if ref.get("evidence_kind") == kind]
        if len(paths) != 1:
            _error(f"{kind} must be retained exactly once")
        source = _strict_json_bytes(actual[paths[0]], label=kind)
        if not isinstance(source, Mapping):
            _error(f"{kind} must be a JSON object")
        _reject_caller_pass_facts(source)
        _check_record(source, label=kind)
        digest = _sha256_bytes(actual[paths[0]])
        if digest != manifest["external_inputs"][f"{kind}_sha256"]:
            _error(f"{kind} bytes differ from the external binding")
        auxiliary[kind] = (source, digest)
    review = semantic["agent_review"]
    reviewers = review["reviewers"]
    reviewer_output_sha256s, unanimous, disagreements = _validate_reviewer_outputs(
        reviewers,
        reference_id=semantic["reference_id"],
        review=review,
        references=references,
        actual=actual,
    )
    roster = auxiliary["agent_roster"][0]
    if (
        set(roster)
        != {"schema_version", "profile", "reference_id", "reviewers", "record_sha256"}
        or roster.get("schema_version") != "deeplaw.agent-review-roster/v1"
        or roster.get("profile") != "machine_evaluated_no_human_attestation"
        or roster.get("reference_id") != semantic["reference_id"]
        or roster.get("reviewers") != reviewers
        or auxiliary["agent_roster"][1] != review["roster_sha256"]
    ):
        _error("agent roster does not bind the semantic reference")
    consensus = auxiliary["agent_consensus"][0]
    if (
        set(consensus)
        != {
            "schema_version",
            "profile",
            "reference_id",
            "roster_sha256",
            "rubric_sha256",
            "source_corpus_sha256",
            "reviewer_output_sha256s",
            "unanimous",
            "disagreements",
            "record_sha256",
        }
        or consensus.get("schema_version") != "deeplaw.agent-review-consensus/v1"
        or consensus.get("profile") != "machine_evaluated_no_human_attestation"
        or consensus.get("reference_id") != semantic["reference_id"]
        or consensus.get("roster_sha256") != review["roster_sha256"]
        or consensus.get("rubric_sha256") != review["rubric_sha256"]
        or consensus.get("source_corpus_sha256") != review["source_corpus_sha256"]
        or consensus.get("reviewer_output_sha256s") != reviewer_output_sha256s
        or consensus.get("unanimous") is not unanimous
        or unanimous is not True
        or consensus.get("disagreements") != disagreements
        or disagreements != []
        or auxiliary["agent_consensus"][1] != review["consensus_sha256"]
    ):
        _error("agent consensus does not bind retained reviewer output decisions")
    isolation = auxiliary["agent_isolation"][0]
    isolation_true = {
        "reviewer_processes_distinct",
        "reviewer_outputs_hidden",
        "candidate_hidden",
        "runner_reference_labels_hidden",
        "scorers_mutually_hidden",
        "scorer_runner_isolated",
        "arbiter_deterministic",
    }
    if (
        set(isolation)
        != {
            "schema_version",
            "profile",
            "reference_id",
            *isolation_true,
            "compiler_reference_access",
            "evaluator_output_mutation",
            "blind_contamination",
            "violations",
            "record_sha256",
        }
        or isolation.get("schema_version") != "deeplaw.agent-review-isolation/v1"
        or isolation.get("profile") != "machine_evaluated_no_human_attestation"
        or isolation.get("reference_id") != semantic["reference_id"]
        or any(isolation.get(field) is not True for field in isolation_true)
        or any(
            isolation.get(field) is not False
            for field in (
                "compiler_reference_access",
                "evaluator_output_mutation",
                "blind_contamination",
            )
        )
        or isolation.get("violations") != []
        or auxiliary["agent_isolation"][1] != review["isolation_sha256"]
    ):
        _error("agent isolation does not bind the semantic reference")
    for reviewer in reviewers:
        for field, label in (
            ("process_identity_sha256", "reviewer process receipt"),
            ("output_sha256", "reviewer output"),
        ):
            evidence_kind = (
                "machine_reviewer_output"
                if field == "output_sha256"
                else "sanitized_supporting_receipt"
            )
            matches = [
                path
                for path, ref in references.items()
                if ref.get("evidence_kind") == evidence_kind
                and ref.get("sha256") == reviewer[field]
            ]
            if len(matches) != 1:
                _error(f"{label} is not retained exactly once")
    for field, label in (
        ("qualification_holdout_sha256", "qualification holdout"),
        ("final_blind_holdout_sha256", "final blind holdout"),
    ):
        matches = [
            path
            for path, ref in references.items()
            if ref.get("evidence_kind") == "sanitized_supporting_receipt"
            and ref.get("sha256") == manifest["external_inputs"][field]
        ]
        if len(matches) != 1:
            _error(f"{label} bytes are not retained exactly once")


def validate_external_bundle(
    root: Path,
    *,
    expected_candidate_run_id: int,
    expected_evidence_run_id: int,
    active_qualification: Path | None = None,
) -> dict[str, Any]:
    """Validate one v4 bundle without any human approver or secret input."""

    candidate_run = _positive_run(expected_candidate_run_id, label="candidate run id")
    evidence_run = _positive_run(expected_evidence_run_id, label="evidence run id")
    if candidate_run == evidence_run:
        _error("candidate and evidence run IDs must be distinct")
    _selected_root, actual, total_bytes = _scan_root(Path(root))
    manifest_raw = actual[BUNDLE_MANIFEST_NAME]
    manifest = _strict_json_bytes(manifest_raw, label="bundle manifest")
    if not isinstance(manifest, dict):
        _error("bundle manifest must be an object")
    schema = _load_schema(BUNDLE_MANIFEST_SCHEMA, label="bundle manifest contract")
    _validate_schema(manifest, schema, label="bundle manifest")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        _error("bundle manifest is not the current v4 contract")
    if (
        manifest.get("profile") != "machine_evaluated_no_human_attestation"
        or manifest.get("human_authenticity") != "not_claimed"
    ):
        _error("bundle manifest human profile is invalid")
    if (
        manifest.get("candidate_run_id") != candidate_run
        or manifest.get("evidence_run_id") != evidence_run
    ):
        _error("bundle workflow run identity differs")
    _check_record(manifest, label="bundle manifest")
    candidate = _candidate_from_manifest(manifest)
    references, kind_counts = _reference_index(
        manifest,
        actual=actual,
    )
    _validate_auxiliary_sources(references, actual, manifest=manifest)
    binding_paths = [
        path
        for path, ref in references.items()
        if ref.get("evidence_kind") == "post_build_machine_reference_binding"
    ]
    if len(binding_paths) != 1:
        _error("post-build machine reference binding must be retained exactly once")
    binding_path = binding_paths[0]
    binding_value = _strict_json_bytes(actual[binding_path], label="candidate binding")
    if not isinstance(binding_value, Mapping):
        _error("candidate binding must be an object")
    candidate_binding = _validate_candidate_binding(
        binding_value,
        candidate=candidate,
        references=references,
        actual=actual,
        manifest=manifest,
        binding_path=binding_path,
    )
    _validate_security_domains(
        references,
        actual,
        manifest=manifest,
        candidate_binding=candidate_binding,
    )
    inventory_paths = [
        path
        for path, ref in references.items()
        if ref.get("evidence_kind") == "candidate_full_raw_inventory"
    ]
    if len(inventory_paths) != 1:
        _error("Candidate Full raw inventory must be retained exactly once")
    inventory_value = _strict_json_bytes(
        actual[inventory_paths[0]], label="Candidate Full raw inventory"
    )
    _validate_inventory(
        inventory_value,
        raw_digest=_sha256_bytes(actual[inventory_paths[0]]),
        candidate_run_id=candidate_run,
        candidate=candidate,
        references=references,
        actual=actual,
        manifest=manifest,
    )
    typed_counts: Counter[str] = Counter()
    panel_roles: Counter[str] = Counter()
    panel_scorers: set[tuple[str, str]] = set()
    machine_paths: set[str] = set()
    machine_run_ids: set[str] = set()
    for path, reference in references.items():
        kind = str(reference["evidence_kind"])
        if kind not in _TYPED_KINDS:
            continue
        value = _strict_json_bytes(actual[path], label="typed qualification evidence")
        typed_counts[kind] += 1
        role, scorer_a, scorer_b = _validate_typed(
            value,
            kind=kind,
            candidate=candidate,
            candidate_run_id=candidate_run,
            evidence_run_id=evidence_run,
            references=references,
            actual=actual,
            external=manifest["external_inputs"],
            candidate_binding=candidate_binding,
            candidate_inventory_sha256=manifest[
                "candidate_full_raw_inventory_sha256"
            ],
        )
        if kind == "machine_reference_scorer":
            machine_paths.add(path)
            run_binding = value.get("run_binding")
            run_id = run_binding.get("run_id") if isinstance(run_binding, Mapping) else None
            if not isinstance(run_id, str) or not run_id or run_id in machine_run_ids:
                _error("machine reference scorer runs are not distinct")
            machine_run_ids.add(run_id)
            if role is None or scorer_a is None or scorer_b is None:
                _error("machine reference scorer identity is incomplete")
            panel_roles[role] += 1
            panel_scorers.add((scorer_a, scorer_b))
    if dict(typed_counts) != _REQUIRED_TYPED_COUNTS:
        _error("typed qualification evidence inventory does not match required Core counts")
    if panel_roles != Counter({"qualification_holdout": 1, "final_blind": 1}):
        _error("machine reference scorer must cover holdout and final blind exactly once")
    if len(panel_scorers) != 1:
        _error("machine reference scorer panel is not shared exactly")
    if len(machine_paths) != 2:
        _error("machine reference scorer receipts are incomplete")
    # When supplied, the active v2 record must be the independently frozen
    # exact candidate.  A tracked 0.12 pending template cannot authorize an
    # external run or make this bundle pass.
    if active_qualification is not None:
        active_raw = _regular_path(
            Path(active_qualification),
            label="active qualification",
            max_bytes=MAX_ACTIVE_BYTES,
        )[1]
        active = _strict_json_bytes(active_raw, label="active qualification")
        active_schema = _load_schema(
            REPOSITORY / "contracts/v013-active-qualification.v2.schema.json",
            label="active qualification contract",
        )
        _validate_schema(active, active_schema, label="active qualification")
        if not isinstance(active, Mapping):
            _error("active qualification must be an object")
        active_candidate = active.get("candidate_binding")
        if (
            active.get("profile") != "machine_evaluated_no_human_attestation"
            or active.get("status")
            != "frozen_exact_candidate_machine_evaluation_pending"
            or not isinstance(active_candidate, Mapping)
            or active_candidate.get("source_commit") != candidate["commit"]
            or active_candidate.get("source_tree") != candidate["tree"]
            or active_candidate.get("lock_sha256") != candidate["lock_sha256"]
            or active_candidate.get("wheel_sha256") != candidate["wheel_sha256"]
            or active_candidate.get("sdist_sha256") != candidate["sdist_sha256"]
        ):
            _error("active qualification is not the frozen exact machine-only candidate")
        active_external = active.get("external_inputs")
        scorer_panel = candidate_binding.get("scorer_panel")
        if not isinstance(active_external, Mapping) or not isinstance(scorer_panel, Mapping):
            _error("active qualification machine inputs are incomplete")
        scorer_a = scorer_panel.get("scorer_a")
        scorer_b = scorer_panel.get("scorer_b")
        review_panel_sha256 = _sha256_bytes(
            _canonical_json(
                {
                    "agent_roster_sha256": manifest["external_inputs"]["agent_roster_sha256"],
                    "agent_consensus_sha256": manifest["external_inputs"]["agent_consensus_sha256"],
                    "agent_isolation_sha256": manifest["external_inputs"]["agent_isolation_sha256"],
                }
            ).encode("utf-8")
        )
        expected_active_external = {
            "semantic_machine_proposal_sha256": manifest["external_inputs"][
                "semantic_reference_sha256"
            ],
            "qualification_holdout_sha256": manifest["external_inputs"][
                "qualification_holdout_sha256"
            ],
            "final_blind_holdout_sha256": manifest["external_inputs"][
                "final_blind_holdout_sha256"
            ],
            "agent_review_panel_sha256": review_panel_sha256,
            "runner_sha256": manifest["external_inputs"]["runner_sha256"],
            "scorer_a_sha256": (
                scorer_a.get("sha256") if isinstance(scorer_a, Mapping) else None
            ),
            "scorer_b_sha256": (
                scorer_b.get("sha256") if isinstance(scorer_b, Mapping) else None
            ),
            "arbitration_sha256": manifest["external_inputs"]["arbiter_sha256"],
            "isolation_sha256": manifest["external_inputs"][
                "compiler_scorer_isolation_sha256"
            ],
        }
        if dict(active_external) != expected_active_external:
            _error("active qualification machine input binding differs")
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "validation_level": "structural_preflight",
        "typed_derivation_performed": False,
        "qualification_passed": False,
        "profile": "machine_evaluated_no_human_attestation",
        "candidate_run_id": candidate_run,
        "evidence_run_id": evidence_run,
        "file_count": len(actual),
        "referenced_file_count": len(references),
        "total_bytes": total_bytes,
        "bundle_manifest_sha256": _sha256_bytes(manifest_raw),
        "candidate_binding_sha256": _sha256_bytes(actual[binding_path]),
        "external_inputs_sha256": _sha256_bytes(
            _canonical_json(manifest["external_inputs"]).encode("utf-8")
        ),
        "candidate_full_raw_inventory_sha256": manifest["candidate_full_raw_inventory_sha256"],
        "evidence_kind_counts": dict(sorted(kind_counts.items())),
        "typed_evidence_kind_counts": dict(sorted(typed_counts.items())),
        "machine_reference_scorer_count": len(machine_paths),
        "machine_reference_roles": ["qualification_holdout", "final_blind"],
        "human_authenticity": "not_claimed",
    }


def validate_external_bundle_v4(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Explicit versioned alias for integrations that avoid unversioned names."""

    return validate_external_bundle(*args, **kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a machine-only External Qualification Bundle v4."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate-run-id", type=int, required=True)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    parser.add_argument("--active-qualification", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate_external_bundle(
            args.root,
            expected_candidate_run_id=args.candidate_run_id,
            expected_evidence_run_id=args.evidence_run_id,
            active_qualification=args.active_qualification,
        )
    except (OSError, ExternalQualificationBundleV4Error, ValueError):
        print("external qualification bundle v4 validation failed", file=sys.stderr)
        return 1
    print(_canonical_json(result))
    return 0


__all__ = [
    "BUNDLE_MANIFEST_NAME",
    "BUNDLE_MANIFEST_SCHEMA",
    "MACHINE_REVIEWER_OUTPUT_SCHEMA",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "ExternalQualificationBundleError",
    "ExternalQualificationBundleV4Error",
    "main",
    "validate_external_bundle",
    "validate_external_bundle_v4",
]


if __name__ == "__main__":
    raise SystemExit(main())

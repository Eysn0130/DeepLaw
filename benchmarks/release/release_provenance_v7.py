"""Fail-closed transitive provenance verification for the v0.13 release boundary.

This module is intentionally a consumer of already-produced receipts.  It does not build a
candidate, score a run, or infer a Gate result from caller supplied booleans.  The verifier
reopens the v7 manifest, the two qualification receipts, the active candidate document, and the
retained bytes before emitting a small, path-free derived receipt.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

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
    safe_asset_file as _core_safe_asset_file,
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

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACTS = REPOSITORY / "contracts"
_CURRENT_CLASSIFICATION_PATH = REPOSITORY / "benchmarks/release/v013-gate-classification-v7.json"
_CURRENT_CLASSIFICATION_SCHEMA_VERSION = "deeplaw.v013-release-gate-classification/v7"
_CURRENT_CLASSIFICATION_ID = "deeplaw-v013-commercial-gates-v7"
# Keep the current external-bundle contract at one explicit seam.
_EXTERNAL_BUNDLE_V3_SCHEMA_VERSION = "deeplaw.external-qualification-bundle-manifest/v3"
_EXTERNAL_BUNDLE_ROOT_NAME = "external-evidence"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:(?<![A-Za-z0-9])/(?:Users|home|private|var|tmp|root|etc|opt|Volumes)(?:/|$))"
    r"|(?:^|[\s\"'])[A-Za-z]:[\\/]",
    re.IGNORECASE,
)
_SECRET_FIELD = re.compile(
    r"(?:^|[._-])(?:auth|credential|credentials|secret|secrets|password|passwd|"
    r"api[_-]?key|private[_-]?key|token)(?:$|[._-])",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"api[_-]?key\s*[=:]\s*\S+|bearer\s+[A-Za-z0-9._-]{20,})",
    re.IGNORECASE,
)
_V3_SAFE_FALSE_FIELDS = frozenset(
    {
        "auth_file_read",
        "auth_store_read",
        "authentication_material_read",
        "credential_value_recorded",
    }
)
_V3_SAFE_DIGEST_FIELDS = frozenset(
    {
        "auth_file_sha256",
        "auth_store_sha256",
        "authentication_material_sha256",
        "credential_path_sha256",
        "credential_value_sha256",
    }
)

_TYPED_SCHEMA_VERSION = "deeplaw.typed-qualification-evidence/v1"
_TYPED_ARTIFACT_KIND = "typed-qualification-evidence"
_TYPED_KINDS = frozenset(
    {
        "candidate_full_junit",
        "candidate_platform_receipt",
        "host_event_sequence",
        "exact_wheel_execution",
        "human_gold_scorer",
        "legal_rows",
        "wiki_journey_rows",
        "context_capsule_selection_usage",
        "scale_report",
        "retained_supply_chain",
    }
)
_CANDIDATE_WORKFLOW_KINDS = frozenset(
    {
        "candidate_full_junit",
        "candidate_platform_receipt",
        "retained_supply_chain",
    }
)
_EXTERNAL_WORKFLOW_KINDS = _TYPED_KINDS - _CANDIDATE_WORKFLOW_KINDS
_EXTERNAL_CORPUS_ROLES = frozenset({"qualification_holdout", "final_blind"})
_FROZEN_HOST_TASK_CASES = frozenset(
    {
        "cold/new",
        "resume/fork/concurrent-worktree",
        "compaction/forget/stale",
    }
)
_HOST_RESPONSE_MODELS = {
    "codex": "gpt-5.6-luna",
    "opencode": "deepseek-v4-flash",
}
_SEMANTIC_GOLD_THRESHOLD_ID = "semantic-gold-thresholds"
_GATE_VALIDATOR_ID = "deeplaw-typed-qualification-v1"
_GATE_VALIDATOR_VERSION = "1"
_GATE_VALIDATOR_SOURCE = "benchmarks/release/typed_qualification_evidence.py"
_GATE_VALIDATOR_EXECUTABLE = "benchmarks/release/assemble_commercial_qualification_v7.py"

# Candidate Full provenance is not caller-selectable.  These are the only tracked source files
# allowed to identify the candidate workflow/parser pair; exact-wheel execution uses its own
# runner while retaining the same typed parser as scorer.
_CANDIDATE_PROVENANCE_FILES: dict[str, dict[str, tuple[str, ...]]] = {
    "candidate_full_junit": {
        "runner": (".github/workflows/candidate-full.yml",),
        "scorer": ("benchmarks/release/typed_qualification_evidence.py",),
    },
    "candidate_platform_receipt": {
        "runner": (".github/workflows/candidate-full.yml",),
        "scorer": (
            "benchmarks/release/typed_qualification_evidence.py",
            "benchmarks/release/platform-core-test-manifest-v2.json",
        ),
    },
    "retained_supply_chain": {
        # The workflow delegates the two reproducible builds to this tracked verifier.  Bind
        # both exact source files as one closed identity so a caller cannot replace the build
        # checker while retaining a self-consistent workflow digest.
        "runner": (
            ".github/workflows/candidate-full.yml",
            "benchmarks/release/verify_reproducible_build.py",
        ),
        "scorer": ("benchmarks/release/typed_qualification_evidence.py",),
    },
}

# The current v7 classification predates typed evidence-kind metadata.  Keep this closed mapping
# in the current verifier so
# a caller cannot select an arbitrary parser or make a semantically unrelated receipt satisfy a
# Core Gate.  The mapping is intentionally one-way: a typed kind not listed for a Gate is rejected.
_GATE_EVIDENCE_KINDS: dict[str, frozenset[str]] = {
    "canonical_integrity": frozenset({"exact_wheel_execution"}),
    "migration_recovery": frozenset({"candidate_full_junit"}),
    "secret_host_isolation": frozenset({"host_event_sequence"}),
    "bounded_context": frozenset({"context_capsule_selection_usage"}),
    "legal_evidence": frozenset({"legal_rows"}),
    "source_citation_locator": frozenset({"legal_rows"}),
    "scale_performance": frozenset({"scale_report"}),
    "supported_platforms": frozenset({"candidate_platform_receipt"}),
    "reproducible_supply_chain": frozenset({"retained_supply_chain"}),
    "human_gold_isolation": frozenset({"human_gold_scorer"}),
    "codex": frozenset({"host_event_sequence"}),
    "opencode": frozenset({"host_event_sequence"}),
    "selective_forget": frozenset({"wiki_journey_rows"}),
    "timeline": frozenset({"host_event_sequence"}),
}

_SCHEMAS = {
    "release": "commercial-release-manifest.v7.schema.json",
    "pre_publish": "pre-publish-artifact-gate.v1.schema.json",
    "candidate_gold": "candidate-gold-binding-receipt.v1.schema.json",
    "external_bundle": "external-qualification-bundle-manifest.v3.schema.json",
    "active": "v013-active-qualification.v1.schema.json",
    "semantic_gold": "semantic-human-gold.v3.schema.json",
    "commercial_report_v4": "commercial-evidence-report.v4.schema.json",
    "gate_result": "provenance-bound-gate-result.v3.schema.json",
}
_CANDIDATE_RAW_INVENTORY_NAME = "candidate-full-inventory-receipt.json"
_CANDIDATE_RAW_INVENTORY_SCHEMA = "deeplaw.candidate-full-inventory-receipt/v1"


class ReleaseProvenanceV7Error(ValueError):
    """Raised when a v7 provenance chain is missing, unsafe, or inconsistent."""


def _fail(message: str) -> None:
    raise ReleaseProvenanceV7Error(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _regular_file(path: Path, *, label: str, max_bytes: int = MAX_FILE_BYTES) -> None:
    _core_regular_file_bytes(
        path,
        label=label,
        max_bytes=max_bytes,
        error_type=ReleaseProvenanceV7Error,
    )


def _strict_json_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    _resolved, raw = _core_regular_file_bytes(
        path,
        label=label,
        max_bytes=MAX_FILE_BYTES,
        error_type=ReleaseProvenanceV7Error,
    )
    try:
        value = _core_strict_json_bytes(
            raw,
            label=label,
            error_type=ReleaseProvenanceV7Error,
            require_object=True,
        )
    except ReleaseProvenanceV7Error as error:
        # v7 exposed one stable parse-error contract.  Keep that historical
        # surface while sharing the stricter v8 parsing implementation.
        raise ReleaseProvenanceV7Error(f"{label} must be strict UTF-8 JSON") from error
    return value, raw


def _canonical_json(value: Any) -> str:
    return _core_canonical_json_bytes(
        value,
        error_type=ReleaseProvenanceV7Error,
        message="provenance JSON is not canonicalizable",
    ).decode("utf-8")


def _candidate_corpus_sha256(candidate: Mapping[str, Any]) -> str:
    projection = {
        field: candidate.get(field)
        for field in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256")
    }
    if any(not isinstance(value, str) or not value for value in projection.values()):
        _fail("Candidate Full corpus binding is incomplete")
    return _sha256_bytes(_canonical_json(projection).encode("utf-8"))


def _canonical_digest(value: Mapping[str, Any], *, excluded: str) -> str:
    return _core_digest_without(
        value,
        field=excluded,
        error_type=ReleaseProvenanceV7Error,
    )


def _derived_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _record_digest(value: Mapping[str, Any]) -> str:
    return _canonical_digest(value, excluded="record_sha256")


def _require_record(value: Mapping[str, Any], *, label: str) -> None:
    declared = value.get("record_sha256")
    if not isinstance(declared, str) or not _SHA256.fullmatch(declared):
        _fail(f"{label} record digest is missing")
    if declared != _record_digest(value):
        _fail(f"{label} record digest differs")


def _sha256_bytes(raw: bytes) -> str:
    return _core_sha256_bytes(raw)


def _load_schema(name: str) -> dict[str, Any]:
    schema_path = CONTRACTS / _SCHEMAS[name]
    try:
        schema, _raw = _strict_json_file(schema_path, label="contract schema")
        Draft202012Validator.check_schema(schema)
    except ReleaseProvenanceV7Error:
        raise
    except Exception as error:  # pragma: no cover - a repository contract failure
        raise ReleaseProvenanceV7Error("contract schema is unavailable") from error
    return schema


def _validate_schema(value: Mapping[str, Any], *, schema_name: str, label: str) -> None:
    schema = _load_schema(schema_name)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        _fail(f"{label} schema violation at {location}")


def _safe_root(path: Path, *, label: str) -> Path:
    return _core_safe_root_directory(
        path,
        label=label,
        error_type=ReleaseProvenanceV7Error,
    )


def _safe_relative(value: Any, *, label: str) -> str:
    return _core_safe_relative_posix(
        value,
        label=label,
        error_type=ReleaseProvenanceV7Error,
    )


def _safe_asset(root: Path, relative: Any, *, label: str) -> Path:
    return _core_safe_asset_file(
        root,
        relative,
        label=label,
        error_type=ReleaseProvenanceV7Error,
    )


def _load_candidate_raw_inventory(
    candidate_raw_root: Path,
    *,
    candidate_run_id: int,
    candidate_commit: str,
    external_bundle_root: Path,
) -> tuple[set[tuple[str, int]], bytes]:
    """Reopen the independent Candidate Full raw artifact inventory and its closure."""

    root = _safe_root(candidate_raw_root, label="Candidate Full raw artifact root")
    bundle_root = _safe_root(external_bundle_root, label="external bundle root")
    if root == bundle_root or root.is_relative_to(bundle_root) or bundle_root.is_relative_to(root):
        _fail("Candidate Full raw artifact root must be independent of the external bundle")
    inventory_path = root / _CANDIDATE_RAW_INVENTORY_NAME
    inventory, raw = _strict_json_file(
        inventory_path,
        label="Candidate Full raw inventory receipt",
    )
    if set(inventory) != {
        "schema_version",
        "record_kind",
        "run_id",
        "head_sha",
        "path_policy",
        "files",
    }:
        _fail("Candidate Full raw inventory receipt schema is not current")
    if (
        inventory.get("schema_version") != _CANDIDATE_RAW_INVENTORY_SCHEMA
        or inventory.get("record_kind") != "candidate_full_raw_inventory"
        or inventory.get("run_id") != candidate_run_id
        or inventory.get("head_sha") != candidate_commit
        or inventory.get("path_policy") != "logical_relative_paths_only"
    ):
        _fail("Candidate Full raw inventory identity differs")
    rows = inventory.get("files")
    if not isinstance(rows, list):
        _fail("Candidate Full raw inventory file list is missing")

    declared: dict[str, tuple[str, int]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"logical_path", "sha256", "bytes"}:
            _fail("Candidate Full raw inventory row is not closed")
        logical_path = _safe_relative(row.get("logical_path"), label="Candidate raw logical path")
        digest = row.get("sha256")
        byte_size = row.get("bytes")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _fail("Candidate raw inventory digest is invalid")
        if (
            isinstance(byte_size, bool)
            or not isinstance(byte_size, int)
            or not 1 <= byte_size <= MAX_FILE_BYTES
        ):
            _fail("Candidate raw inventory byte size is invalid")
        if logical_path in declared:
            _fail("Candidate raw inventory contains a duplicate path")
        declared[logical_path] = (digest, byte_size)

    observed: dict[str, tuple[str, int]] = {}
    total = 0
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                _fail("Candidate Full raw artifact root contains a symbolic link")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative == _CANDIDATE_RAW_INVENTORY_NAME:
                continue
            _regular_file(path, label="Candidate Full raw artifact")
            size = path.stat().st_size
            total += size
            if total > MAX_TOTAL_BYTES:
                _fail("Candidate Full raw artifact root exceeds its aggregate byte bound")
            observed[relative] = (_sha256_bytes(path.read_bytes()), size)
    except OSError as error:
        raise ReleaseProvenanceV7Error(
            "Candidate Full raw artifact inventory is unavailable"
        ) from error
    if observed != declared:
        _fail("Candidate Full raw inventory does not match retained bytes")
    return set(declared.values()), raw


def _typed_source_references(value: Any) -> list[Mapping[str, Any]]:
    references: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        if {"relative_path", "sha256", "byte_size"} <= set(value):
            references.append(value)
        for item in value.values():
            references.extend(_typed_source_references(item))
    elif isinstance(value, list):
        for item in value:
            references.extend(_typed_source_references(item))
    return references


def _require_candidate_typed_sources(
    payload: Any,
    *,
    candidate_raw_inventory: set[tuple[str, int]],
) -> None:
    references = _typed_source_references(payload)
    if not references:
        _fail("Candidate Full typed evidence has no source receipts")
    for reference in references:
        _safe_relative(reference.get("relative_path"), label="Candidate typed source path")
        digest = reference.get("sha256")
        byte_size = reference.get("byte_size")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _fail("Candidate typed source digest is invalid")
        if isinstance(byte_size, bool) or not isinstance(byte_size, int):
            _fail("Candidate typed source byte size is invalid")
        if (digest, byte_size) not in candidate_raw_inventory:
            _fail("Candidate Full typed source is not retained Candidate Full evidence")


def _load_candidate_provenance_identities() -> dict[str, dict[str, dict[str, str]]]:
    """Derive the closed Candidate Full runner/scorer identities from checkout bytes."""

    checkout = _safe_root(REPOSITORY, label="candidate checkout")
    identities: dict[str, dict[str, dict[str, str]]] = {}
    for kind, files in _CANDIDATE_PROVENANCE_FILES.items():
        kind_identities: dict[str, dict[str, str]] = {}
        for role, relatives in files.items():
            components: list[dict[str, str]] = []
            for relative in relatives:
                selected = _safe_asset(
                    checkout,
                    relative,
                    label=f"Candidate Full {kind} {role} source",
                )
                _regular_file(selected, label=f"Candidate Full {kind} {role} source")
                try:
                    raw = selected.read_bytes()
                except OSError as error:
                    raise ReleaseProvenanceV7Error(
                        f"Candidate Full {kind} {role} source is unavailable"
                    ) from error
                components.append({"path": relative, "sha256": _sha256_bytes(raw)})
            identity = "candidate-{}-set/{}".format(role, "/".join(relatives))
            kind_identities[role] = {
                "identity": identity,
                "sha256": _sha256_bytes(_canonical_json({"files": components}).encode("utf-8")),
            }
        identities[kind] = kind_identities
    return identities


def _tracked_file_binding(relative_path: str) -> dict[str, Any]:
    """Recompute a release-bound validator identity from the exact checkout bytes."""

    safe = _safe_relative(relative_path, label="tracked validator path")
    selected = _safe_asset(REPOSITORY, safe, label="tracked validator source")
    _regular_file(selected, label="tracked validator source")
    try:
        raw = selected.read_bytes()
    except OSError as error:
        raise ReleaseProvenanceV7Error("tracked validator source is unavailable") from error
    return {
        "relative_path": safe,
        "byte_size": len(raw),
        "file_sha256": _sha256_bytes(raw),
    }


def _read_asset(
    root: Path,
    relative: Any,
    *,
    expected_sha256: str,
    expected_size: int | None = None,
    label: str,
) -> tuple[Path, bytes]:
    selected = _safe_asset(root, relative, label=label)
    _regular_file(selected, label=label)
    try:
        raw = selected.read_bytes()
    except OSError as error:
        raise ReleaseProvenanceV7Error(f"{label} is unavailable") from error
    if expected_size is not None and len(raw) != expected_size:
        _fail(f"{label} byte size differs")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        _fail(f"{label} digest is invalid")
    if _sha256_bytes(raw) != expected_sha256:
        _fail(f"{label} digest differs")
    return selected, raw


def _projection(value: Any, *, depth: int = 0) -> None:
    if depth > 24:
        _fail("sanitized evidence exceeds its depth bound")
    if isinstance(value, str):
        if _ABSOLUTE_PATH.search(value) or _SECRET_VALUE.search(value):
            _fail("sanitized evidence contains a private path or Secret")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("sanitized evidence contains a non-string field")
            if key in _V3_SAFE_FALSE_FIELDS:
                if item is not False:
                    _fail("authentication receipt field must be false")
            elif key in _V3_SAFE_DIGEST_FIELDS:
                if not isinstance(item, str) or not _SHA256.fullmatch(item):
                    _fail("authentication receipt digest is invalid")
            elif _SECRET_FIELD.search(key):
                _fail("sanitized evidence contains a Secret-shaped field")
            _projection(item, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _projection(item, depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    _fail("sanitized evidence contains an unsupported value")


def _check_text_asset(raw: bytes, *, media_type: str | None, label: str) -> None:
    if media_type == "application/json":
        return
    if media_type == "text/plain" or media_type == "text/csv":
        try:
            text = raw.decode("utf-8")
        except UnicodeError as error:
            raise ReleaseProvenanceV7Error(f"{label} is not UTF-8 text") from error
        if _ABSOLUTE_PATH.search(text) or _SECRET_VALUE.search(text):
            _fail("sanitized evidence contains a private path or Secret")


def _assert_equal(label: str, *values: Any) -> None:
    if not values or any(value != values[0] for value in values[1:]):
        _fail(f"{label} differs across provenance receipts")


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(f"{label} is invalid")
    return value


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} is missing")
    return value


def _semantic_gold_threshold_binding(gold: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the frozen threshold identity from the retained semantic Gold bytes."""

    thresholds = _mapping(gold.get("thresholds"), label="semantic Gold thresholds")
    return {
        "threshold_id": _SEMANTIC_GOLD_THRESHOLD_ID,
        "threshold_sha256": _sha256_bytes(_canonical_json(thresholds).encode("utf-8")),
        "frozen": True,
    }


def _load_validated_input(
    path: Path, *, schema_name: str, label: str
) -> tuple[dict[str, Any], bytes]:
    value, raw = _strict_json_file(path, label=label)
    _validate_schema(value, schema_name=schema_name, label=label)
    return value, raw


def _load_current_classification(
    path: Path,
) -> tuple[dict[str, Any], bytes, set[str], dict[str, frozenset[str]]]:
    try:
        supplied = path.expanduser().resolve(strict=True)
        canonical = _CURRENT_CLASSIFICATION_PATH.resolve(strict=True)
    except OSError as error:
        raise ReleaseProvenanceV7Error("canonical v7 Gate classification is unavailable") from error
    if supplied != canonical or path.is_symlink():
        _fail("current Gate classification is not the canonical v7 document")
    classification, raw = _strict_json_file(path, label="current Gate classification")
    if classification.get("schema_version") != _CURRENT_CLASSIFICATION_SCHEMA_VERSION:
        _fail("current Gate classification schema is not v7")
    if classification.get("classification_id") != _CURRENT_CLASSIFICATION_ID:
        _fail("current Gate classification identity is not canonical")
    categories = classification.get("categories")
    gates = classification.get("gates")
    if not isinstance(categories, list) or not isinstance(gates, list):
        _fail("current Gate classification inventory is missing")
    core_ids: set[str] = set()
    for category in categories:
        if isinstance(category, Mapping) and category.get("category") == "Core":
            listed = category.get("gate_ids")
            if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
                _fail("current Core Gate inventory is invalid")
            if len(set(listed)) != len(listed):
                _fail("current Core Gate inventory contains duplicate identities")
            core_ids.update(listed)
    gate_by_id: dict[str, Mapping[str, Any]] = {}
    for gate in gates:
        item = _mapping(gate, label="current Gate classification entry")
        gate_id = item.get("gate_id")
        if not isinstance(gate_id, str) or gate_id in gate_by_id:
            _fail("current Gate classification contains duplicate identities")
        gate_by_id[gate_id] = item
    if not core_ids or any(
        gate_by_id.get(gate_id, {}).get("category") != "Core" for gate_id in core_ids
    ):
        _fail("current Core Gate inventory is not closed")
    if set(gate_by_id) < core_ids:
        _fail("current Core Gate inventory references a missing Gate")
    if len(core_ids) != 14 or set(_GATE_EVIDENCE_KINDS) != core_ids:
        _fail("current v7 Core Gate inventory is not the closed 14-Gate mapping")
    gate_corpus_roles: dict[str, frozenset[str]] = {}
    for gate_id in core_ids:
        required_roles = gate_by_id[gate_id].get("required_corpus_roles")
        if not isinstance(required_roles, list) or not required_roles:
            _fail("current Core Gate corpus role inventory is missing")
        if not all(isinstance(role, str) for role in required_roles):
            _fail("current Core Gate corpus role inventory is invalid")
        roles = frozenset(required_roles)
        if _GATE_EVIDENCE_KINDS[gate_id] <= _CANDIDATE_WORKFLOW_KINDS:
            # Candidate Full is a separate corpus domain from the later external holdout.
            # A v7 classification must name that domain explicitly; historical `development`
            # roles cannot be silently reinterpreted here.
            if roles != frozenset({"candidate_full"}):
                _fail("current Candidate Full corpus role inventory is not v7")
        else:
            if not roles or not roles.issubset(_EXTERNAL_CORPUS_ROLES):
                _fail("current external corpus role inventory is not v7")
        gate_corpus_roles[gate_id] = roles
    if raw != canonical.read_bytes():
        _fail("current Gate classification bytes differ from canonical v7")
    return classification, raw, core_ids, gate_corpus_roles


def _bundle_inventory(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    assets_root: Path,
) -> dict[str, list[tuple[str, Path, bytes, Mapping[str, Any]]]]:
    root = _safe_root(manifest_path.parent, label="external bundle root")
    asset_root = _safe_root(assets_root, label="assets root")
    try:
        bundle_prefix = root.relative_to(asset_root).as_posix()
    except ValueError as error:
        raise ReleaseProvenanceV7Error(
            "external bundle must be inside the assets root"
        ) from error
    if bundle_prefix != _EXTERNAL_BUNDLE_ROOT_NAME:
        _fail("external bundle must be rooted at assets_root/external-evidence")
    references = manifest.get("files")
    if not isinstance(references, list) or not references:
        _fail("external bundle file inventory is missing")
    by_path: dict[str, Mapping[str, Any]] = {}
    for reference in references:
        item = _mapping(reference, label="external bundle file reference")
        name = _safe_relative(item.get("relative_path"), label="external bundle file path")
        if name in by_path:
            _fail("external bundle file inventory contains a duplicate")
        by_path[name] = item

    actual: dict[str, Path] = {}
    total = 0
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                _fail("external bundle contains a symbolic link")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            if not 1 <= size <= MAX_FILE_BYTES:
                _fail("external bundle file exceeds its byte bound")
            total += size
            if total > MAX_TOTAL_BYTES:
                _fail("external bundle exceeds its aggregate byte bound")
            actual[relative] = path
    except OSError as error:
        raise ReleaseProvenanceV7Error("external bundle inventory is unavailable") from error

    manifest_name = manifest_path.name
    expected_actual = set(by_path) | {manifest_name}
    if set(actual) != expected_actual:
        _fail("external bundle contains an orphan or unreferenced file")

    index: dict[str, list[tuple[str, Path, bytes, Mapping[str, Any]]]] = {}
    for name, reference in by_path.items():
        selected = actual.get(name)
        if selected is None:
            _fail("external bundle references a missing file")
        _regular_file(selected, label="external bundle file")
        try:
            raw = selected.read_bytes()
        except OSError as error:
            raise ReleaseProvenanceV7Error("external bundle file is unavailable") from error
        expected_size = reference.get("byte_size")
        expected_sha = reference.get("sha256")
        if expected_size != len(raw) or expected_sha != _sha256_bytes(raw):
            _fail("external bundle file binding differs from retained bytes")
        media_type = reference.get("media_type")
        if reference.get("evidence_kind") in {
            "retained_wheel",
            "retained_sdist",
            "original_legal_pdf",
            "original_legal_docx",
        }:
            # Distribution archives are intentionally opaque to this verifier.  Their exact
            # bytes are checked by _read_asset; decoding them as JSON would reject valid wheels.
            pass
        elif media_type == "application/json":
            value, _ = _strict_json_file(selected, label="external JSON evidence")
            _projection(value)
        else:
            _check_text_asset(raw, media_type=media_type, label="external text evidence")
        indexed_name = f"{bundle_prefix}/{name}"
        index.setdefault(expected_sha, []).append((indexed_name, selected, raw, reference))
    return index


def _index_raw(
    index: Mapping[str, list[tuple[str, Path, bytes, Mapping[str, Any]]]],
    digest: Any,
    *,
    label: str,
    expected_evidence_kind: str | None = None,
) -> tuple[str, Path, bytes, Mapping[str, Any]]:
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        _fail(f"{label} digest is invalid")
    matches = index.get(digest)
    if expected_evidence_kind is not None:
        matches = [
            item for item in matches or [] if item[3].get("evidence_kind") == expected_evidence_kind
        ]
    if not matches:
        _fail(f"{label} has no retained receipt")
    return sorted(matches, key=lambda item: item[0])[0]


def _require_bundle_ref(
    index: Mapping[str, list[tuple[str, Path, bytes, Mapping[str, Any]]]],
    *,
    relative_path: str,
    expected_sha: str,
    expected_size: int,
    label: str,
    expected_evidence_kind: str | None = None,
) -> None:
    matches = index.get(expected_sha)
    if not matches or not any(
        item[0] == relative_path
        and item[3].get("byte_size") == expected_size
        and item[3].get("sha256") == expected_sha
        and (
            expected_evidence_kind is None
            or item[3].get("evidence_kind") == expected_evidence_kind
        )
        for item in matches
    ):
        _fail(f"{label} is not referenced by the external bundle")


def _validate_gate_input(
    reference: Mapping[str, Any],
    *,
    assets_root: Path,
    bundle_index: Mapping[str, list[tuple[str, Path, bytes, Mapping[str, Any]]]],
    expected_candidate: Mapping[str, Any],
    expected_workflow_run_id: int,
    expected_corpus_sha256: str,
    expected_corpus_role: str,
    expected_runner: Mapping[str, Any] | None,
    expected_scorer: Mapping[str, Any] | None,
    candidate_identities: Mapping[str, Mapping[str, Mapping[str, str]]],
    candidate_raw_inventory: set[tuple[str, int]],
    trusted_human_approver: Mapping[str, Any] | None,
    allowed_kinds: frozenset[str],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    relative_path = _safe_relative(reference.get("relative_path"), label="Gate input path")
    expected_sha = reference.get("file_sha256")
    expected_size = reference.get("byte_size")
    path, _raw = _read_asset(
        assets_root,
        relative_path,
        expected_sha256=expected_sha,
        expected_size=expected_size,
        label="Gate input receipt",
    )
    _require_bundle_ref(
        bundle_index,
        relative_path=relative_path,
        expected_sha=expected_sha,
        expected_size=expected_size,
        label="Gate input",
    )
    manifest, _manifest_raw = _strict_json_file(path, label="typed evidence manifest")
    if manifest.get("schema_version") != _TYPED_SCHEMA_VERSION:
        _fail("Gate input must be a typed qualification evidence manifest")
    kind = manifest.get("kind")
    if not isinstance(kind, str) or kind not in _TYPED_KINDS or kind not in allowed_kinds:
        _fail("Gate input evidence kind is not allowed for this Core Gate")
    _require_bundle_ref(
        bundle_index,
        relative_path=relative_path,
        expected_sha=expected_sha,
        expected_size=expected_size,
        label="typed evidence manifest",
        expected_evidence_kind=kind,
    )
    if reference.get("schema_version") != _TYPED_SCHEMA_VERSION:
        _fail("Gate input schema identity is not the typed qualification contract")
    if reference.get("artifact_kind") != _TYPED_ARTIFACT_KIND:
        _fail("Gate input artifact kind is not typed qualification evidence")
    if reference.get("evidence_kind") != kind:
        _fail("Gate input evidence kind differs from its manifest")
    manifest_record = manifest.get("record_sha256")
    if not isinstance(manifest_record, str) or not _SHA256.fullmatch(manifest_record):
        _fail("typed evidence manifest record digest is missing")
    _require_record(manifest, label="typed evidence manifest")
    if reference.get("record_sha256") != manifest_record:
        _fail("Gate input manifest record binding differs")
    if kind in _CANDIDATE_WORKFLOW_KINDS:
        _require_candidate_typed_sources(
            manifest.get("payload"),
            candidate_raw_inventory=candidate_raw_inventory,
        )

    run_binding = _mapping(manifest.get("run_binding"), label="typed evidence run binding")
    run_id = run_binding.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        _fail("typed evidence run identity is missing")
    _assert_equal(
        "typed evidence workflow run",
        run_binding.get("workflow_run_id"),
        expected_workflow_run_id,
    )
    candidate_binding = _mapping(
        manifest.get("candidate_binding"), label="typed evidence candidate binding"
    )
    for field in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"):
        _assert_equal(
            f"typed evidence candidate {field}",
            candidate_binding.get(field),
            expected_candidate.get(field),
        )
    corpus = _mapping(manifest.get("corpus"), label="typed evidence corpus")
    _assert_equal("typed evidence corpus role", corpus.get("role"), expected_corpus_role)
    _assert_equal("typed evidence corpus", corpus.get("sha256"), expected_corpus_sha256)
    runner = _mapping(manifest.get("runner"), label="typed evidence runner")
    scorer = _mapping(manifest.get("scorer"), label="typed evidence scorer")
    if kind in _CANDIDATE_WORKFLOW_KINDS:
        # Candidate Full is produced before the external qualification runner/scorer exists.
        # Both identities are recomputed from the exact checkout bytes above; a self-hashed
        # caller string is never accepted as a substitute.
        tracked = candidate_identities.get(kind)
        if not isinstance(tracked, Mapping):
            _fail("Candidate Full tracked provenance mapping is missing")
        tracked_runner = tracked.get("runner")
        tracked_scorer = tracked.get("scorer")
        if not isinstance(tracked_runner, Mapping) or not isinstance(tracked_scorer, Mapping):
            _fail("Candidate Full tracked runner/scorer mapping is incomplete")
        _assert_equal("Candidate Full tracked runner", runner, tracked_runner)
        _assert_equal("Candidate Full tracked scorer", scorer, tracked_scorer)
        parser_runner: Mapping[str, Any] | None = tracked_runner
        parser_scorer: Mapping[str, Any] | None = tracked_scorer
    else:
        _assert_equal("typed evidence runner", runner, expected_runner)
        _assert_equal("typed evidence scorer", scorer, expected_scorer)
        parser_runner = expected_runner
        parser_scorer = expected_scorer
    try:
        from benchmarks.release.typed_qualification_evidence import parse_typed_evidence
        derived = parse_typed_evidence(
            path,
            expected_candidate=expected_candidate,
            expected_run_id=run_id,
            expected_corpus_sha256=expected_corpus_sha256,
            expected_runner=parser_runner,
            expected_scorer=parser_scorer,
            trusted_human_approver=trusted_human_approver,
        )
    except Exception as error:
        raise ReleaseProvenanceV7Error("typed evidence parser rejected Gate input") from error
    if not isinstance(derived, dict) or derived.get("kind") != kind:
        _fail("typed evidence parser returned an unexpected derived kind")
    if derived.get("evidence_record_sha256") != manifest_record:
        _fail("typed evidence derived receipt is not bound to its manifest")
    if derived.get("status") != "passed":
        _fail("typed evidence derived status is not passed")
    derived_record = _derived_digest(derived)
    if reference.get("derived_record_sha256") != derived_record:
        _fail("Gate input derived record binding differs")
    return kind, manifest, derived


def _validate_semantic_report(
    report_path: Path,
    *,
    report_ref: Mapping[str, Any],
    assets_root: Path,
    bundle_index: Mapping[str, list[tuple[str, Path, bytes, Mapping[str, Any]]]],
    classification: Mapping[str, Any],
    classification_sha256: str,
    core_gate_ids: set[str],
    classification_corpus_roles: Mapping[str, frozenset[str]],
    candidate: Mapping[str, Any],
    external: Mapping[str, Any],
    active_protocol: Mapping[str, Any],
    candidate_workflow_run_id: int,
    evidence_workflow_run_id: int,
    qualification_workflow_run_id: int,
    runner: Mapping[str, Any],
    scorer: Mapping[str, Any],
    candidate_identities: Mapping[str, Mapping[str, Mapping[str, str]]],
    candidate_raw_inventory: set[tuple[str, int]],
    trusted_human_approver: Mapping[str, Any] | None,
    threshold_binding: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes]:
    report, raw = _strict_json_file(report_path, label="semantic evidence report")
    actual_sha = _sha256_bytes(raw)
    if actual_sha != report_ref.get("report_sha256"):
        _fail("semantic evidence report bytes differ")

    version = report.get("schema_version")
    if version != "deeplaw.commercial-evidence-report/v4":
        _fail("semantic evidence report must use current v4 schema")
    _validate_schema(report, schema_name="commercial_report_v4", label="semantic evidence report")
    digest_field = "report_sha256" if "report_sha256" in report else "record_sha256"
    digest = report.get(digest_field)
    if not isinstance(digest, str) or digest != _canonical_digest(report, excluded=digest_field):
        _fail("semantic evidence report canonical digest differs")
    if report_ref.get("record_sha256") != digest:
        _fail("semantic evidence report record binding differs")
    _assert_equal(
        "semantic report qualification run",
        report.get("qualification_run_id"),
        qualification_workflow_run_id,
    )

    report_classification = _mapping(
        report.get("classification_binding"), label="semantic report classification"
    )
    _assert_equal(
        "semantic report classification schema",
        report_classification.get("classification_schema_version"),
        classification.get("schema_version"),
    )
    _assert_equal(
        "semantic report classification bytes",
        report_classification.get("classification_sha256"),
        classification_sha256,
    )
    _assert_equal(
        "semantic report classification identity",
        report_classification.get("classification_id"),
        classification.get("classification_id"),
    )

    report_candidate = _mapping(report.get("candidate_binding"), label="semantic report candidate")
    _assert_equal(
        "semantic report commit",
        report_candidate.get("candidate_commit", report_candidate.get("commit")),
        candidate["commit"],
    )
    _assert_equal(
        "semantic report tree",
        report_candidate.get("candidate_tree", report_candidate.get("tree")),
        candidate["tree"],
    )
    _assert_equal(
        "semantic report wheel",
        report_candidate.get("candidate_wheel_sha256", report_candidate.get("wheel_sha256")),
        candidate["wheel_sha256"],
    )
    _assert_equal(
        "semantic report sdist",
        report_candidate.get("candidate_sdist_sha256", report_candidate.get("sdist_sha256")),
        candidate["sdist_sha256"],
    )

    gold = report.get("gold_binding")
    if isinstance(gold, Mapping):
        _assert_equal(
            "semantic report Gold", gold.get("gold_sha256"), external["semantic_gold_sha256"]
        )
    report_threshold = _mapping(
        report.get("threshold_binding"), label="semantic report threshold binding"
    )
    _assert_equal("semantic report threshold", report_threshold, threshold_binding)
    corpus = report.get("corpus")
    if isinstance(corpus, Mapping):
        role = corpus.get("role")
        expected_corpus = (
            external["qualification_holdout_sha256"]
            if role == "qualification_holdout"
            else external["final_blind_holdout_sha256"]
            if role == "final_blind"
            else None
        )
        if expected_corpus is None:
            _fail("semantic report corpus is not an external holdout")
        _assert_equal("semantic report corpus", corpus.get("sha256"), expected_corpus)
    protocol = _mapping(report.get("protocol_binding"), label="semantic report protocol")
    expected_protocol = {
        "protocol_id": active_protocol["protocol_id"],
        "protocol_sha256": active_protocol["sha256"],
        "frozen": True,
    }
    _assert_equal("semantic report protocol", protocol, expected_protocol)
    _assert_equal(
        "semantic report Gold binding",
        gold,
        {
            "gold_sha256": external["semantic_gold_sha256"],
            "role": "qualification_gold",
            "source": "repository_external",
            "frozen": True,
        },
    )
    if not isinstance(corpus, Mapping):
        _fail("semantic report corpus binding is missing")
    if corpus.get("source") != "repository_external" or corpus.get("frozen") is not True:
        _fail("semantic report corpus is not a frozen repository-external input")

    expected_validator_source = _tracked_file_binding(_GATE_VALIDATOR_SOURCE)
    expected_validator_executable = _tracked_file_binding(_GATE_VALIDATOR_EXECUTABLE)

    # A v4 report is a closed index of v3 Gate Result files.  Reopen each result and verify its
    # own canonical result digest instead of treating the report's status fields as observations.
    gate_results = report.get("gate_results")
    if not isinstance(gate_results, list):
        _fail("semantic report Core Gate inventory is missing")
    observed_gate_ids: set[str] = set()
    consumed_typed_paths: set[str] = set()
    consumed_typed_kinds: set[str] = set()
    context_projections: set[str] = set()
    for reference in gate_results:
        item = _mapping(reference, label="semantic report Gate reference")
        gate_id = item.get("gate_id")
        if not isinstance(gate_id, str) or gate_id in observed_gate_ids:
            _fail("semantic report contains duplicate Gate identities")
        observed_gate_ids.add(gate_id)
        if item.get("category") != "Core" or gate_id not in core_gate_ids:
            _fail("semantic report contains a non-Core or unknown Gate")
        result_ref = _mapping(item.get("result"), label="semantic report Gate artifact")
        path = _safe_asset(
            assets_root, result_ref.get("relative_path"), label="Gate result path"
        )
        _regular_file(path, label="Gate result")
        try:
            result_raw = path.read_bytes()
        except OSError as error:
            raise ReleaseProvenanceV7Error("Gate result is unavailable") from error
        if len(result_raw) != result_ref.get("byte_size") or _sha256_bytes(
            result_raw
        ) != result_ref.get("file_sha256"):
            _fail("Gate result bytes differ")
        result, _ = _strict_json_file(path, label="Gate result")
        _validate_schema(result, schema_name="gate_result", label="Gate result")
        if result.get("gate_id") != gate_id:
            _fail("Gate result identity differs from report")
        if result.get("category") != "Core":
            _fail("Gate result category is not Core")
        _assert_equal(
            "Gate result qualification run",
            result.get("qualification_run_id"),
            qualification_workflow_run_id,
        )
        _assert_equal("Gate result validator id", result.get("validator_id"), _GATE_VALIDATOR_ID)
        _assert_equal(
            "Gate result validator version",
            result.get("validator_version"),
            _GATE_VALIDATOR_VERSION,
        )
        _assert_equal(
            "Gate result validator source",
            result.get("validator_source"),
            expected_validator_source,
        )
        _assert_equal(
            "Gate result validator executable",
            result.get("validator_executable"),
            expected_validator_executable,
        )
        if result.get("result_sha256") != _canonical_digest(
            result, excluded="result_sha256"
        ):
            _fail("Gate result canonical digest differs")
        if result_ref.get("record_sha256") != result.get("result_sha256"):
            _fail("Gate result record binding differs")
        result_candidate = _mapping(
            result.get("candidate_binding"), label="Gate result candidate"
        )
        _assert_equal(
            "Gate result commit", result_candidate.get("candidate_commit"), candidate["commit"]
        )
        _assert_equal("Gate result tree", result_candidate.get("candidate_tree"), candidate["tree"])
        _assert_equal(
            "Gate result wheel",
            result_candidate.get("candidate_wheel_sha256"),
            candidate["wheel_sha256"],
        )
        _assert_equal(
            "Gate result sdist",
            result_candidate.get("candidate_sdist_sha256"),
            candidate["sdist_sha256"],
        )
        result_classification = _mapping(
            result.get("classification_binding"), label="Gate result classification"
        )
        _assert_equal(
            "Gate result classification id",
            result_classification.get("classification_id"),
            classification["classification_id"],
        )
        _assert_equal(
            "Gate result classification schema",
            result_classification.get("classification_schema_version"),
            classification["schema_version"],
        )
        _assert_equal(
            "Gate result classification bytes",
            result_classification.get("classification_sha256"),
            classification_sha256,
        )
        result_protocol = _mapping(result.get("protocol_binding"), label="Gate result protocol")
        _assert_equal("Gate result protocol", result_protocol, expected_protocol)
        result_gold = _mapping(result.get("gold_binding"), label="Gate result Gold")
        _assert_equal(
            "Gate result Gold binding",
            result_gold,
            {
                "gold_sha256": external["semantic_gold_sha256"],
                "role": "qualification_gold",
                "source": "repository_external",
                "frozen": True,
            },
        )
        result_threshold = _mapping(
            result.get("threshold_binding"), label="Gate result threshold binding"
        )
        _assert_equal("Gate result threshold", result_threshold, threshold_binding)
        allowed_kinds = _GATE_EVIDENCE_KINDS.get(gate_id)
        if not allowed_kinds:
            _fail("current Core Gate has no closed typed evidence mapping")
        candidate_domain = bool(allowed_kinds & _CANDIDATE_WORKFLOW_KINDS)
        external_domain = bool(allowed_kinds & _EXTERNAL_WORKFLOW_KINDS)
        if candidate_domain == external_domain:
            _fail("Core Gate typed evidence mapping crosses provenance domains")
        classification_roles = classification_corpus_roles.get(gate_id)
        if classification_roles is None:
            _fail("current Core Gate has no corpus role mapping")
        if candidate_domain and classification_roles != frozenset({"candidate_full"}):
            _fail("Candidate Full Gate corpus role is not current")
        if external_domain and not classification_roles.issubset(_EXTERNAL_CORPUS_ROLES):
            _fail("external Gate corpus role is not current")
        result_corpus = _mapping(result.get("corpus"), label="Gate result corpus")
        result_corpus_role = result_corpus.get("role")
        if result_corpus_role not in classification_roles:
            _fail("Gate result corpus role is not allowed by the current classification")
        if candidate_domain:
            expected_corpus_role = "candidate_full"
            _assert_equal(
                "Gate result Candidate Full corpus",
                result_corpus.get("sha256"),
                _candidate_corpus_sha256(candidate),
            )
        else:
            expected_corpus_role = result_corpus_role
            expected_external_corpus = {
                "qualification_holdout": external["qualification_holdout_sha256"],
                "final_blind": external["final_blind_holdout_sha256"],
            }.get(expected_corpus_role)
            if expected_external_corpus is None:
                _fail("external Gate corpus role is unsupported")
            _assert_equal(
                "Gate result external corpus",
                result_corpus.get("sha256"),
                expected_external_corpus,
            )
        for field in ("executions", "run_ids", "metrics", "inputs"):
            values = result.get(field)
            if not isinstance(values, list) or not values:
                _fail(f"Core Gate {gate_id} has no real {field} receipt")
        typed_inputs: list[tuple[str, Mapping[str, Any], dict[str, Any]]] = []
        input_domains: set[str] = set()
        for input_reference in result["inputs"]:
            input_item = _mapping(input_reference, label="Gate input provenance")
            declared_kind = input_item.get("evidence_kind")
            if declared_kind not in allowed_kinds:
                _fail("Gate input evidence kind is not allowed for this Core Gate")
            input_is_candidate = declared_kind in _CANDIDATE_WORKFLOW_KINDS
            if input_is_candidate != candidate_domain:
                _fail("Gate input crosses candidate/external provenance domains")
            input_domains.add("candidate" if input_is_candidate else "external")
            kind, manifest, derived = _validate_gate_input(
                input_item,
                assets_root=assets_root,
                bundle_index=bundle_index,
                expected_candidate=candidate,
                expected_workflow_run_id=(
                    candidate_workflow_run_id if input_is_candidate else evidence_workflow_run_id
                ),
                expected_corpus_sha256=result_corpus["sha256"],
                expected_corpus_role=expected_corpus_role,
                expected_runner=None if input_is_candidate else runner,
                expected_scorer=None if input_is_candidate else scorer,
                candidate_identities=candidate_identities,
                candidate_raw_inventory=candidate_raw_inventory,
                trusted_human_approver=trusted_human_approver,
                allowed_kinds=allowed_kinds,
            )
            typed_inputs.append((kind, manifest, derived))
            consumed_typed_paths.add(input_item["relative_path"])
            consumed_typed_kinds.add(kind)
            if kind == "context_capsule_selection_usage":
                projection = derived.get("metrics", {}).get("projection")
                if not isinstance(projection, str):
                    _fail("bounded Context evidence lacks a projection identity")
                context_projections.add(projection)
        if len(input_domains) != 1:
            _fail("Gate result inputs cross provenance domains")
        if gate_id in {"codex", "opencode"}:
            if len(typed_inputs) != 3:
                _fail(f"Core Gate {gate_id} requires three distinct typed Host runs")
            run_ids = [item[1]["run_binding"]["run_id"] for item in typed_inputs]
            if len(set(run_ids)) != 3:
                _fail(f"Core Gate {gate_id} requires distinct typed Host runs")
            task_cases = [item[2].get("metrics", {}).get("task_case") for item in typed_inputs]
            if any(not isinstance(value, str) or not value for value in task_cases):
                _fail(f"Core Gate {gate_id} typed Host evidence lacks task_case")
            if set(task_cases) != _FROZEN_HOST_TASK_CASES:
                _fail(f"Core Gate {gate_id} task cases do not match the frozen composite task set")
            for _kind, _manifest, derived in typed_inputs:
                derived_metrics = _mapping(derived.get("metrics"), label="typed Host metrics")
                _assert_equal(
                    f"Core Gate {gate_id} Host identity",
                    derived_metrics.get("host"),
                    gate_id,
                )
                _assert_equal(
                    f"Core Gate {gate_id} response model",
                    derived_metrics.get("actual_response_model_id"),
                    _HOST_RESPONSE_MODELS[gate_id],
                )
        if gate_id in {"secret_host_isolation", "timeline"}:
            if len(typed_inputs) != 6:
                _fail(f"Core Gate {gate_id} requires all six distinct Native Host runs")
            by_host: dict[str, set[str]] = {"codex": set(), "opencode": set()}
            host_run_ids: set[str] = set()
            for _kind, manifest, derived in typed_inputs:
                metrics = _mapping(derived.get("metrics"), label="typed Host metrics")
                host = metrics.get("host")
                task_case = metrics.get("task_case")
                run_id = manifest["run_binding"]["run_id"]
                if host not in by_host or not isinstance(task_case, str):
                    _fail(f"Core Gate {gate_id} contains an invalid Host identity")
                if run_id in host_run_ids:
                    _fail(f"Core Gate {gate_id} contains a duplicate Host run")
                host_run_ids.add(run_id)
                by_host[host].add(task_case)
            if any(cases != _FROZEN_HOST_TASK_CASES for cases in by_host.values()):
                _fail(f"Core Gate {gate_id} does not cover both frozen Host task sets")
        input_ids = [item.get("input_id") for item in result["inputs"]]
        if (
            any(not isinstance(value, str) or not value for value in input_ids)
            or len(input_ids) != len(set(input_ids))
        ):
            _fail("Gate result input identities are not unique")
        typed_run_ids = [item[1]["run_binding"]["run_id"] for item in typed_inputs]
        _assert_equal("Gate result run IDs", sorted(result["run_ids"]), sorted(typed_run_ids))
        expected_executions = [
            {
                "run_id": manifest["run_binding"]["run_id"],
                "workflow_run_id": manifest["run_binding"]["workflow_run_id"],
                "input_refs": [input_reference["input_id"]],
                "evidence_kind": kind,
            }
            for input_reference, (kind, manifest, _derived) in zip(
                result["inputs"], typed_inputs, strict=True
            )
        ]
        _assert_equal("Gate result executions", result["executions"], expected_executions)
        expected_metrics: dict[str, Any] = {}
        expected_failures: dict[str, int] = {}
        for input_item, (_kind, _manifest, derived) in zip(
            result["inputs"], typed_inputs, strict=True
        ):
            input_id = input_item["input_id"]
            for metric, observed in derived["metrics"].items():
                expected_metrics[f"{input_id}:{metric}"] = observed
            for failure_id, count in derived["hard_failure_counts"].items():
                expected_failures[f"{input_id}:{failure_id}"] = count
        actual_metrics: dict[str, Any] = {}
        for metric in result["metrics"]:
            metric_item = _mapping(metric, label="Gate result metric")
            metric_id = metric_item.get("metric")
            if not isinstance(metric_id, str) or metric_id in actual_metrics:
                _fail("Gate result metrics contain duplicate identities")
            refs = metric_item.get("input_refs")
            if not isinstance(refs, list) or len(refs) != 1 or refs[0] not in {
                item["input_id"] for item in result["inputs"]
            }:
                _fail("Gate result metric input provenance is invalid")
            actual_metrics[metric_id] = metric_item.get("observed")
        if actual_metrics != expected_metrics:
            _fail("Gate result metrics differ from typed derived metrics")
        actual_failures: dict[str, int] = {}
        for failure in result["hard_failures"]:
            failure_item = _mapping(failure, label="Gate result hard failure")
            failure_id = failure_item.get("failure_id")
            count = failure_item.get("count")
            if (
                not isinstance(failure_id, str)
                or failure_id in actual_failures
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                _fail("Gate result hard failure identity is invalid")
            actual_failures[failure_id] = count
        if actual_failures != expected_failures:
            _fail("Gate result hard failures differ from typed derived counts")
        if result.get("status") != "passed":
            _fail(f"Core Gate {gate_id} is not passed by its receipt")
        if any(value != 0 for value in actual_failures.values()):
            _fail(f"Core Gate {gate_id} contains a failure receipt")
        if any(item[2].get("status") != result.get("status") for item in typed_inputs):
            _fail(f"Core Gate {gate_id} status differs from typed derived status")
    if observed_gate_ids != core_gate_ids:
        _fail("semantic report Core Gate inventory is incomplete")
    retained_typed_paths = {
        indexed_name
        for entries in bundle_index.values()
        for indexed_name, _path, _raw, reference in entries
        if reference.get("evidence_kind") in _TYPED_KINDS
    }
    if consumed_typed_paths != retained_typed_paths:
        _fail("commercial Core Gates do not consume the closed typed evidence inventory")
    if consumed_typed_kinds != _TYPED_KINDS:
        _fail("commercial Core Gates do not cover every required typed evidence kind")
    if context_projections != {"continuity", "normal", "legal_source_first"}:
        _fail("bounded Context evidence does not cover all three frozen projection classes")
    return report, raw


def validate_release_provenance(
    release_manifest_path: str | Path,
    *,
    classification_path: str | Path,
    pre_publish_receipt_path: str | Path,
    candidate_gold_binding_path: str | Path,
    external_bundle_manifest_path: str | Path,
    active_qualification_path: str | Path,
    assets_root: str | Path,
    candidate_raw_root: str | Path,
    expected_candidate_run_id: int,
    expected_evidence_run_id: int,
    expected_qualification_run_id: int,
    trusted_human_approver: Mapping[str, Any] | None = None,
    trusted_human_approver_path: str | Path | None = None,
) -> dict[str, Any]:
    """Reopen and cross-check the v7 release provenance chain.

    The returned object is a derived validation receipt, not a caller-supplied Gate result.  It
    contains only digests, run identities, and counts; no input path or evidence prose is copied.
    """

    candidate_run = _positive_int(expected_candidate_run_id, label="candidate run id")
    evidence_run = _positive_int(expected_evidence_run_id, label="evidence run id")
    qualification_run = _positive_int(expected_qualification_run_id, label="qualification run id")
    if len({candidate_run, evidence_run, qualification_run}) != 3:
        _fail("candidate, evidence, and qualification run IDs must be distinct")

    root = _safe_root(Path(assets_root), label="assets root")
    release, release_raw = _load_validated_input(
        Path(release_manifest_path), schema_name="release", label="release manifest"
    )
    pre_publish, pre_publish_raw = _load_validated_input(
        Path(pre_publish_receipt_path), schema_name="pre_publish", label="pre-publish receipt"
    )
    candidate_gold, candidate_gold_raw = _load_validated_input(
        Path(candidate_gold_binding_path),
        schema_name="candidate_gold",
        label="Candidate Gold binding receipt",
    )
    bundle, _bundle_raw = _load_validated_input(
        Path(external_bundle_manifest_path),
        schema_name="external_bundle",
        label="external qualification bundle manifest",
    )
    if bundle.get("schema_version") != _EXTERNAL_BUNDLE_V3_SCHEMA_VERSION:
        _fail("external qualification bundle must use current v3 schema")
    active, _active_raw = _load_validated_input(
        Path(active_qualification_path), schema_name="active", label="active qualification"
    )
    (
        classification,
        classification_raw,
        core_gate_ids,
        classification_corpus_roles,
    ) = _load_current_classification(Path(classification_path))
    candidate_identities = _load_candidate_provenance_identities()
    if trusted_human_approver_path is None:
        _fail("trusted human approver descriptor path is required")
    descriptor_value, _descriptor_raw, descriptor_sha = _load_trusted_human_approver(
        Path(trusted_human_approver_path),
        bundle_manifest_path=Path(external_bundle_manifest_path),
    )
    if trusted_human_approver is not None and trusted_human_approver != descriptor_value:
        _fail("trusted human approver descriptor differs from its path")
    trusted_human_approver = descriptor_value
    v3_boundary = _validate_external_bundle_v3_boundary(
        bundle_root=Path(external_bundle_manifest_path).parent,
        active_qualification_path=Path(active_qualification_path),
        trusted_human_approver_path=Path(trusted_human_approver_path),
        candidate_run_id=candidate_run,
        evidence_run_id=evidence_run,
    )
    _assert_equal(
        "trusted human approver descriptor",
        bundle.get("trusted_human_approver_descriptor_sha256"),
        descriptor_sha,
        v3_boundary.get("trusted_human_approver_descriptor_sha256"),
    )
    _assert_equal(
        "Candidate Full raw inventory",
        bundle.get("candidate_full_raw_inventory_sha256"),
        v3_boundary.get("candidate_full_raw_inventory_sha256"),
    )

    _require_record(release, label="release manifest")
    _require_record(pre_publish, label="pre-publish receipt")
    _require_record(candidate_gold, label="Candidate Gold binding receipt")
    if bundle.get("record_sha256") != _record_digest(bundle):
        _fail("external bundle manifest record digest differs")

    run_ids = _mapping(release.get("run_ids"), label="release run IDs")
    _assert_equal("candidate run", run_ids.get("candidate_run_id"), candidate_run)
    _assert_equal("evidence run", run_ids.get("evidence_run_id"), evidence_run)
    _assert_equal("qualification run", run_ids.get("qualification_run_id"), qualification_run)
    _assert_equal("external bundle candidate run", bundle.get("candidate_run_id"), candidate_run)
    _assert_equal("external bundle evidence run", bundle.get("evidence_run_id"), evidence_run)

    if (
        release.get("release_ready") is not True
        or release.get("public_release_verified") is not False
    ):
        _fail("pre-public authorization requires release_ready and public verification separation")
    if release.get("post_public_verification") is not None:
        _fail("post-public verification must be absent during pre-public authorization")
    if release.get("claim_eligible") is not True:
        _fail("pre-public authorization requires claim_eligible")
    if release.get("competitive_claim_eligible") is not False:
        _fail("competitive claims are not part of this authorization")

    release_info = _mapping(release.get("release"), label="release identity")
    candidate = _mapping(release.get("candidate_binding"), label="release candidate binding")
    artifact_binding = _mapping(release.get("artifact_binding"), label="release artifact binding")
    pre_candidate = _mapping(pre_publish.get("candidate"), label="pre-publish candidate")
    gold_candidate = _mapping(candidate_gold.get("candidate"), label="Candidate Gold candidate")
    bundle_candidate = _mapping(bundle.get("candidate_binding"), label="bundle candidate")
    active_candidate = _mapping(active.get("candidate_binding"), label="active candidate")
    candidate_raw_inventory, candidate_raw_inventory_raw = _load_candidate_raw_inventory(
        Path(candidate_raw_root),
        candidate_run_id=candidate_run,
        candidate_commit=candidate["commit"],
        external_bundle_root=Path(external_bundle_manifest_path).parent,
    )

    _assert_equal(
        "release version", release_info.get("version"), candidate.get("version"), "0.13.0"
    )
    _assert_equal(
        "candidate commit",
        release_info.get("commit"),
        candidate.get("commit"),
        pre_candidate.get("commit"),
        gold_candidate.get("commit"),
        bundle_candidate.get("commit"),
        active_candidate.get("source_commit"),
    )
    _assert_equal(
        "candidate tree",
        release_info.get("tree"),
        candidate.get("tree"),
        pre_candidate.get("tree"),
        gold_candidate.get("tree"),
        bundle_candidate.get("tree"),
        active_candidate.get("source_tree"),
    )
    _assert_equal(
        "candidate lock",
        candidate.get("lock_sha256"),
        pre_candidate.get("lock_sha256"),
        gold_candidate.get("lock_sha256"),
        bundle_candidate.get("lock_sha256"),
        active_candidate.get("lock_sha256"),
    )
    _assert_equal("release tag", release_info.get("tag"), "v0.13.0")

    if (
        active.get("status") != "frozen_exact_candidate"
        or active.get("candidate_version") != "0.13.0"
    ):
        _fail("active qualification is not a frozen exact v0.13 candidate")
    for field in (
        "source_commit",
        "source_tree",
        "wheel_filename",
        "wheel_sha256",
        "sdist_filename",
        "sdist_sha256",
        "artifact_manifest_sha256",
    ):
        if active_candidate.get(field) in (None, ""):
            _fail("active qualification is missing current candidate fields")
    active_external = _mapping(
        active.get("external_inputs"), label="active qualification external inputs"
    )
    for field in (
        "human_gold_manifest_sha256",
        "qualification_holdout_sha256",
        "final_blind_holdout_sha256",
        "compiler_scorer_isolation_sha256",
    ):
        if active_external.get(field) in (None, ""):
            _fail("active qualification is missing current external input fields")

    gold = _mapping(candidate_gold.get("semantic_gold"), label="Candidate Gold semantic Gold")
    artifacts = _mapping(candidate_gold.get("artifacts"), label="Candidate Gold artifacts")
    holdout = _mapping(candidate_gold.get("holdout"), label="Candidate Gold holdout")
    blind = _mapping(candidate_gold.get("blind"), label="Candidate Gold blind")
    scorer = _mapping(candidate_gold.get("scorer"), label="Candidate Gold scorer")
    runner = _mapping(candidate_gold.get("runner"), label="Candidate Gold runner")
    expected_external = {
        "semantic_gold_sha256": gold.get("sha256"),
        "candidate_gold_binding_sha256": _sha256_bytes(candidate_gold_raw),
        "qualification_holdout_sha256": holdout.get("sha256"),
        "final_blind_holdout_sha256": blind.get("sha256"),
        "runner_sha256": runner.get("sha256"),
        "scorer_sha256": scorer.get("sha256"),
        "compiler_scorer_isolation_sha256": active_external.get("compiler_scorer_isolation_sha256"),
    }
    bundle_external = _mapping(bundle.get("external_inputs"), label="bundle external inputs")
    for field, expected in expected_external.items():
        _assert_equal(f"bundle {field}", bundle_external.get(field), expected)
    manifest_external = _mapping(
        release.get("external_bindings"), label="release external bindings"
    )
    for manifest_field, expected_field in (
        ("semantic_gold_sha256", "semantic_gold_sha256"),
        ("holdout_sha256", "qualification_holdout_sha256"),
        ("blind_sha256", "final_blind_holdout_sha256"),
        ("scorer_sha256", "scorer_sha256"),
        ("runner_sha256", "runner_sha256"),
        ("isolation_sha256", "compiler_scorer_isolation_sha256"),
    ):
        _assert_equal(
            f"release {manifest_field}",
            manifest_external.get(manifest_field),
            expected_external[expected_field],
        )
    _assert_equal(
        "active semantic Gold",
        active_external.get("human_gold_manifest_sha256"),
        expected_external["semantic_gold_sha256"],
    )
    _assert_equal(
        "active qualification holdout",
        active_external.get("qualification_holdout_sha256"),
        expected_external["qualification_holdout_sha256"],
    )
    _assert_equal(
        "active final blind",
        active_external.get("final_blind_holdout_sha256"),
        expected_external["final_blind_holdout_sha256"],
    )

    bundle_index = _bundle_inventory(
        Path(external_bundle_manifest_path),
        bundle,
        assets_root=root,
    )
    try:
        candidate_gold_relative = (
            Path(candidate_gold_binding_path).resolve(strict=True).relative_to(root).as_posix()
        )
    except (OSError, ValueError) as error:
        raise ReleaseProvenanceV7Error(
            "Candidate Gold binding is outside the assets root"
        ) from error
    _require_bundle_ref(
        bundle_index,
        relative_path=candidate_gold_relative,
        expected_sha=_sha256_bytes(candidate_gold_raw),
        expected_size=len(candidate_gold_raw),
        label="Candidate Gold binding",
        expected_evidence_kind="post_build_gold_binding",
    )
    _inventory_name, inventory_path, embedded_inventory_raw, inventory_reference = _index_raw(
        bundle_index,
        bundle["candidate_full_raw_inventory_sha256"],
        label="Candidate Full raw inventory",
        expected_evidence_kind="candidate_full_raw_inventory",
    )
    if inventory_reference.get("evidence_kind") != "candidate_full_raw_inventory":
        _fail("Candidate Full raw inventory has the wrong evidence kind")
    _assert_equal(
        "Candidate Full raw inventory bytes",
        embedded_inventory_raw,
        candidate_raw_inventory_raw,
    )
    inventory, _ = _strict_json_file(
        inventory_path,
        label="Candidate Full raw inventory",
    )
    _projection(inventory)
    external_kinds = {
        "semantic_gold_sha256": "human_gold_scorer",
        "candidate_gold_binding_sha256": "post_build_gold_binding",
    }
    for field, digest in expected_external.items():
        _index_raw(
            bundle_index,
            digest,
            label=f"bundle {field}",
            expected_evidence_kind=external_kinds.get(field),
        )
    _semantic_gold_name, semantic_gold_path, semantic_gold_raw, _ = _index_raw(
        bundle_index,
        expected_external["semantic_gold_sha256"],
        label="semantic Gold",
        expected_evidence_kind="human_gold_scorer",
    )
    semantic_gold, _ = _strict_json_file(semantic_gold_path, label="semantic Gold")
    _validate_schema(semantic_gold, schema_name="semantic_gold", label="semantic Gold")
    _assert_equal("semantic Gold id", semantic_gold.get("gold_id"), gold.get("gold_id"))
    _assert_equal(
        "semantic Gold bytes",
        _sha256_bytes(semantic_gold_raw),
        expected_external["semantic_gold_sha256"],
    )
    threshold_binding = _semantic_gold_threshold_binding(semantic_gold)
    pre_ref = _mapping(
        release.get("pre_publish_artifact_gate"), label="release pre-publish binding"
    )
    pre_path = pre_ref.get("path")
    manifest_pre_path = _safe_relative(pre_path, label="release pre-publish path")
    supplied_pre_path = Path(pre_publish_receipt_path)
    try:
        supplied_pre_relative = supplied_pre_path.resolve(strict=True).relative_to(root).as_posix()
    except (OSError, ValueError):
        supplied_pre_relative = None
    if supplied_pre_relative is not None and supplied_pre_relative != manifest_pre_path:
        _fail("release pre-publish receipt path differs")
    _assert_equal(
        "pre-publish receipt bytes", pre_ref.get("receipt_sha256"), _sha256_bytes(pre_publish_raw)
    )

    pre_retained = _mapping(pre_publish.get("retained_artifacts"), label="retained artifacts")
    _assert_equal(
        "retained manifest digest",
        pre_retained.get("manifest_sha256"),
        artifact_binding.get("retained_manifest_sha256"),
        active_candidate.get("artifact_manifest_sha256"),
    )
    retained_manifest_path, retained_manifest_raw = _read_asset(
        root,
        pre_retained.get("manifest_path"),
        expected_sha256=pre_retained["manifest_sha256"],
        label="retained artifact manifest",
    )
    if retained_manifest_path.name == "":  # pragma: no cover - Path always has a name
        _fail("retained artifact manifest is invalid")
    for kind, release_key, gold_key, active_name_key, active_sha_key in (
        ("wheel", "wheel", "wheel", "wheel_filename", "wheel_sha256"),
        ("sdist", "sdist", "sdist", "sdist_filename", "sdist_sha256"),
    ):
        retained = _mapping(pre_retained.get(kind), label=f"retained {kind}")
        release_artifact = _mapping(artifact_binding.get(release_key), label=f"release {kind}")
        gold_artifact = _mapping(artifacts.get(gold_key), label=f"Candidate Gold {kind}")
        _assert_equal(
            f"{kind} digest",
            retained.get("sha256"),
            release_artifact.get("sha256"),
            gold_artifact.get("sha256"),
            active_candidate.get(active_sha_key),
            bundle_candidate.get(f"{kind}_sha256"),
        )
        _assert_equal(
            f"{kind} byte size",
            retained.get("byte_size"),
            release_artifact.get("byte_size"),
            gold_artifact.get("byte_size"),
        )
        _assert_equal(
            f"{kind} filename",
            retained.get("name"),
            gold_artifact.get("name"),
            active_candidate.get(active_name_key),
            Path(release_artifact.get("path", "")).name,
        )
        retained_selected, _retained_raw = _read_asset(
            root,
            retained.get("retained_path"),
            expected_sha256=retained["sha256"],
            expected_size=retained["byte_size"],
            label=f"retained {kind}",
        )
        if retained_selected.relative_to(root).as_posix() != retained.get("retained_path"):
            _fail(f"retained {kind} path is not canonical")
        _read_asset(
            root,
            release_artifact.get("path"),
            expected_sha256=release_artifact["sha256"],
            expected_size=release_artifact["byte_size"],
            label=f"release {kind}",
        )
        _bundle_name, _bundle_path, bundle_raw, _bundle_ref = _index_raw(
            bundle_index,
            retained["sha256"],
            label=f"bundle retained {kind}",
            expected_evidence_kind=f"retained_{kind}",
        )
        if len(bundle_raw) != retained["byte_size"]:
            _fail(f"bundle retained {kind} byte size differs")
        build_first = _mapping(
            _mapping(pre_publish.get("builds"), label="pre-publish builds").get("first"),
            label="first reproducible build",
        )
        build_second = _mapping(
            _mapping(pre_publish.get("builds"), label="pre-publish builds").get("second"),
            label="second reproducible build",
        )
        _assert_equal(
            f"{kind} reproducible builds",
            build_first[f"{kind}_sha256"],
            build_second[f"{kind}_sha256"],
            retained["sha256"],
        )

    for kind in ("sbom", "openvex", "licenses", "provenance"):
        item = _mapping(pre_publish.get(kind), label=f"{kind} receipt")
        selected, raw = _read_asset(
            root, item.get("path"), expected_sha256=item["sha256"], label=f"{kind} artifact"
        )
        _check_text_asset(
            raw,
            media_type="application/json" if selected.suffix == ".json" else item.get("format"),
            label=f"{kind} artifact",
        )
        if selected.suffix == ".json":
            value, _ = _strict_json_file(selected, label=f"{kind} artifact")
            _projection(value)
        _bundle_name, _bundle_path, bundle_raw, _bundle_ref = _index_raw(
            bundle_index,
            item["sha256"],
            label=f"bundle {kind}",
            expected_evidence_kind=kind,
        )
        if len(bundle_raw) != len(raw):
            _fail(f"bundle {kind} byte size differs")

    protocol = _mapping(active.get("protocol_binding"), label="active protocol binding")
    _read_asset(
        root,
        protocol.get("relative_path"),
        expected_sha256=protocol["sha256"],
        label="qualification protocol",
    )

    semantic_ref = _mapping(release.get("semantic_evidence"), label="semantic evidence binding")
    semantic_path = _safe_asset(root, semantic_ref.get("report_path"), label="semantic report path")
    report, report_raw = _validate_semantic_report(
        semantic_path,
        report_ref=semantic_ref,
        assets_root=root,
        bundle_index=bundle_index,
        classification=classification,
        classification_sha256=_sha256_bytes(classification_raw),
        core_gate_ids=core_gate_ids,
        classification_corpus_roles=classification_corpus_roles,
        candidate=candidate,
        external=expected_external,
        active_protocol=protocol,
        candidate_workflow_run_id=candidate_run,
        evidence_workflow_run_id=evidence_run,
        qualification_workflow_run_id=qualification_run,
        runner=runner,
        scorer=scorer,
        candidate_identities=candidate_identities,
        candidate_raw_inventory=candidate_raw_inventory,
        trusted_human_approver=trusted_human_approver,
        threshold_binding=threshold_binding,
    )
    result = {
        "schema_version": "deeplaw.release-provenance-validation/v7",
        "status": "transitive_provenance_validated",
        "authorization_stage": "pre_public",
        "candidate_run_id": candidate_run,
        "evidence_run_id": evidence_run,
        "qualification_run_id": qualification_run,
        "classification": {
            "classification_id": classification["classification_id"],
            "schema_version": classification["schema_version"],
            "sha256": _sha256_bytes(classification_raw),
        },
        "candidate": {
            "commit": candidate["commit"],
            "tree": candidate["tree"],
            "lock_sha256": candidate["lock_sha256"],
            "wheel_sha256": candidate["wheel_sha256"],
            "sdist_sha256": candidate["sdist_sha256"],
        },
        "external_input_hashes": {
            key: expected_external[key]
            for key in (
                "semantic_gold_sha256",
                "qualification_holdout_sha256",
                "final_blind_holdout_sha256",
                "runner_sha256",
                "scorer_sha256",
                "compiler_scorer_isolation_sha256",
            )
        },
        "artifact_hashes": {
            "wheel_sha256": candidate["wheel_sha256"],
            "sdist_sha256": candidate["sdist_sha256"],
            "candidate_raw_inventory_sha256": bundle["candidate_full_raw_inventory_sha256"],
            "retained_manifest_sha256": pre_retained["manifest_sha256"],
            "semantic_report_sha256": semantic_ref["report_sha256"],
        },
        "checked": {
            "schema_inputs": 6,
            "bundle_files": len(bundle.get("files", [])),
            "supply_chain_artifacts": 4,
            "gate_result_count": len(report.get("gate_results", []))
            if isinstance(report.get("gate_results"), list)
            else 0,
            "byte_reopened": True,
        },
        "release_ready": True,
        "claim_eligible": True,
        "public_release_verified": False,
        "post_public_verification": None,
    }
    result["record_sha256"] = _record_digest(result)
    # Keep local variables intentionally consumed so a future refactor cannot accidentally replace
    # byte verification with record-only validation.
    if not release_raw or not report_raw or not retained_manifest_raw:
        _fail("required provenance bytes are empty")
    return result


def verify_release_provenance(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for callers that use ``verify_*`` naming."""

    return validate_release_provenance(*args, **kwargs)


def _load_trusted_human_approver(
    path: Path | None,
    *,
    bundle_manifest_path: Path,
) -> tuple[Mapping[str, Any], bytes, str]:
    if path is None:
        _fail("trusted human approver descriptor path is required")
    trusted_path = path.expanduser()
    bundle_root = _safe_root(bundle_manifest_path.parent, label="external bundle root")
    if trusted_path.is_symlink():
        _fail("trusted human approver record must be a regular non-symlink file")
    try:
        trusted_path = trusted_path.resolve(strict=True)
    except OSError as error:
        raise ReleaseProvenanceV7Error(
            "trusted human approver record is unavailable"
        ) from error
    if trusted_path.is_relative_to(bundle_root):
        _fail("trusted human approver record must be outside the external bundle")
    value, raw = _strict_json_file(trusted_path, label="trusted human approver record")
    if set(value) != {"identity", "key_id", "public_key_b64"}:
        _fail("trusted human approver record is not closed")
    identity = value.get("identity")
    key_id = value.get("key_id")
    encoded = value.get("public_key_b64")
    if (
        not isinstance(identity, str)
        or not identity
        or _ABSOLUTE_PATH.search(identity)
        or _SECRET_VALUE.search(identity)
        or not isinstance(key_id, str)
        or not _SHA256.fullmatch(key_id)
        or not isinstance(encoded, str)
        or not encoded
    ):
        _fail("trusted human approver record fields are invalid")
    try:
        public_key = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ReleaseProvenanceV7Error(
            "trusted human approver public key encoding is invalid"
        ) from error
    if len(public_key) != 32 or base64.b64encode(public_key).decode("ascii") != encoded:
        _fail("trusted human approver public key is not canonical")
    if key_id != _sha256_bytes(public_key):
        _fail("trusted human approver key id differs from public key bytes")
    return value, raw, _sha256_bytes(raw)


def _validate_external_bundle_v3_boundary(
    *,
    bundle_root: Path,
    active_qualification_path: Path,
    trusted_human_approver_path: Path,
    candidate_run_id: int,
    evidence_run_id: int,
) -> Mapping[str, Any]:
    try:
        from benchmarks.release.external_qualification_bundle_v3 import (
            validate_external_bundle,
        )

        return validate_external_bundle(
            bundle_root,
            active_qualification=active_qualification_path,
            trusted_human_approver=trusted_human_approver_path,
            expected_candidate_run_id=candidate_run_id,
            expected_evidence_run_id=evidence_run_id,
        )
    except Exception as error:
        raise ReleaseProvenanceV7Error(
            "external qualification bundle v3 boundary rejected"
        ) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--pre-publish-receipt", type=Path, required=True)
    parser.add_argument("--candidate-gold-binding", type=Path, required=True)
    parser.add_argument("--external-bundle-manifest", type=Path, required=True)
    parser.add_argument("--active-qualification", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--candidate-raw-root", type=Path, required=True)
    parser.add_argument("--candidate-run-id", type=int, required=True)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    parser.add_argument("--qualification-run-id", type=int, required=True)
    parser.add_argument("--trusted-human-approver", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate_release_provenance(
            args.release_manifest,
            classification_path=args.classification,
            pre_publish_receipt_path=args.pre_publish_receipt,
            candidate_gold_binding_path=args.candidate_gold_binding,
            external_bundle_manifest_path=args.external_bundle_manifest,
            active_qualification_path=args.active_qualification,
            assets_root=args.assets_root,
            candidate_raw_root=args.candidate_raw_root,
            expected_candidate_run_id=args.candidate_run_id,
            expected_evidence_run_id=args.evidence_run_id,
            expected_qualification_run_id=args.qualification_run_id,
            trusted_human_approver_path=args.trusted_human_approver,
        )
    except (OSError, ReleaseProvenanceV7Error):
        print("release provenance validation failed", file=sys.stderr)
        return 1
    print(_canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

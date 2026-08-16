"""Assemble the current v8 commercial qualification evidence boundary.

This module is deliberately a small, decision-free consumer of the external
qualification bundle.  It does not execute a Host, score a model, or accept a
caller supplied ``passed`` value.  Every Gate metric, failure count, run id,
and input binding is derived by :func:`parse_typed_evidence`; the resulting
Gate files and report are therefore suitable for the v8 provenance verifier to
reopen later.

The assembler writes only sanitized, path-relative records.  It never reads
credentials, transcripts, or model reasoning and has no network or Ledger side
effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.external_qualification_bundle_v4 import (
    ExternalQualificationBundleV4Error,
    validate_external_bundle_v4,
)
from benchmarks.release.typed_qualification_evidence import _PARSERS as _LEGACY_PARSERS
from benchmarks.release.typed_qualification_evidence import TypedQualificationEvidenceError
from benchmarks.release.typed_qualification_evidence import (
    _reject_forbidden_keys as _legacy_reject_forbidden_keys,
)
from benchmarks.release.typed_qualification_evidence import _source_data as _legacy_source_data
from benchmarks.release.typed_qualification_evidence import _strict_json as _legacy_strict_json

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACTS = REPOSITORY / "contracts"
TYPED_SCHEMA_VERSION = "deeplaw.typed-qualification-evidence/v2"
EXACT_WHEEL_RUNNER_SOURCE = REPOSITORY / "benchmarks/release/exact_wheel_runner.py"
DERIVED_SCHEMA_VERSION = "deeplaw.typed-qualification-derived/v2"
GATE_SCHEMA_VERSION = "deeplaw.provenance-bound-gate-result/v4"
REPORT_SCHEMA_VERSION = "deeplaw.commercial-evidence-report/v5"
RELEASE_SCHEMA_VERSION = "deeplaw.commercial-release-manifest/v8"
CLASSIFICATION_SCHEMA_VERSION = "deeplaw.v013-release-gate-classification/v8"
CLASSIFICATION_ID = "deeplaw-v013-commercial-gates-v8"
PROFILE = "machine_evaluated_no_human_attestation"
REFERENCE_PROVENANCE = "agent_consensus"
HUMAN_AUTHENTICITY = "not_claimed"
VALIDATOR_ID = "deeplaw-typed-qualification-v2"
VALIDATOR_VERSION = "2"
PACKAGE_NAME = "deeplaw"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ABSOLUTE_PATH = re.compile(
    r"(?:(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var|root|etc|opt|Volumes|workspace)(?:/|$))"
    r"|(?:^|[\s\"'])[A-Za-z]:[\\/]",
    re.IGNORECASE,
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|private[_-]?key|secret)\s*[:=]"
)

_CANDIDATE_WORKFLOW_KINDS = frozenset(
    {"candidate_full_junit", "candidate_platform_receipt", "retained_supply_chain"}
)
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
    "machine_reference_isolation": frozenset({"machine_reference_scorer"}),
    "codex": frozenset({"host_event_sequence"}),
    "opencode": frozenset({"host_event_sequence"}),
    "selective_forget": frozenset({"wiki_journey_rows"}),
    "timeline": frozenset({"host_event_sequence"}),
}
_FROZEN_HOST_TASK_CASES = frozenset(
    {
        "cold/new",
        "resume/fork/concurrent-worktree",
        "compaction/forget/stale",
    }
)


class CommercialQualificationAssemblerError(ValueError):
    """Raised when current v8 qualification inputs are absent or inconsistent."""


class _TypedRecord:
    """One manifest and the metrics derived from its immutable raw sources."""

    __slots__ = ("bundle_relative", "derived", "kind", "manifest", "path")

    def __init__(
        self,
        *,
        kind: str,
        path: Path,
        manifest: Mapping[str, Any],
        derived: Mapping[str, Any],
        bundle_relative: str,
    ) -> None:
        self.kind = kind
        self.path = path
        self.manifest = manifest
        self.derived = derived
        self.bundle_relative = bundle_relative


class _BundleFile:
    __slots__ = ("path", "raw", "reference", "relative")

    def __init__(self, *, relative: str, path: Path, reference: Mapping[str, Any], raw: bytes):
        self.relative = relative
        self.path = path
        self.reference = reference
        self.raw = raw


def _fail(message: str) -> None:
    raise CommercialQualificationAssemblerError(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw:
        _fail(f"{label} is empty")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CommercialQualificationAssemblerError(
            f"{label} must be strict UTF-8 JSON"
        ) from error
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    _projection(value, label=label)
    return value


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _projection(value: Any, *, label: str, depth: int = 0) -> None:
    if depth > 32:
        _fail(f"{label} exceeds its depth bound")
    if isinstance(value, str):
        if _ABSOLUTE_PATH.search(value) or _SECRET.search(value):
            _fail(f"{label} contains a private path or Secret")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(f"{label} contains a non-string field")
            _projection(item, label=label, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _projection(item, label=label, depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    _fail(f"{label} contains an unsupported value")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise CommercialQualificationAssemblerError(
            "qualification value is not canonical JSON"
        ) from error


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _record_digest(value: Mapping[str, Any], *, field: str = "record_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return _sha256(_canonical(body))


def _derived_digest(value: Mapping[str, Any]) -> str:
    return _sha256(_canonical(value))


def _regular_file(path: Path, *, label: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} must be a regular non-symlink file")
    try:
        raw = path.read_bytes()
        size = path.stat().st_size
    except OSError as error:
        raise CommercialQualificationAssemblerError(f"{label} is unavailable") from error
    if not 1 <= size <= max_bytes or len(raw) != size:
        _fail(f"{label} exceeds its byte bound")
    return raw


def _exact_wheel_runner_identity() -> dict[str, str]:
    return {
        "identity": "exact-wheel-runner:v2",
        "sha256": _sha256(
            _regular_file(EXACT_WHEEL_RUNNER_SOURCE, label="exact-wheel runner source")
        ),
    }


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _regular_file(path, label=label)
    return _strict_json(raw, label=label), raw


def _safe_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        _fail(f"{label} is not a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        _fail(f"{label} is not a safe relative path")
    if ":" in path.parts[0] or len(value) > 512:
        _fail(f"{label} is not a safe relative path")
    return path.as_posix()


def _safe_asset(root: Path, relative: Any, *, label: str) -> Path:
    name = _safe_relative(relative, label=label)
    selected = root.joinpath(*name.split("/"))
    cursor = root
    try:
        for part in name.split("/"):
            cursor = cursor / part
            if cursor.is_symlink():
                _fail(f"{label} resolves through a symbolic link")
        resolved = selected.resolve(strict=True)
    except OSError as error:
        raise CommercialQualificationAssemblerError(f"{label} is unavailable") from error
    if selected.is_symlink() or not selected.is_file() or not resolved.is_relative_to(root):
        _fail(f"{label} is outside the asset root")
    return selected


def _safe_root(path: Path, *, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        _fail(f"{label} must be a regular non-symlink directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise CommercialQualificationAssemblerError(f"{label} is unavailable") from error
    if resolved.is_symlink() or not resolved.is_dir():
        _fail(f"{label} must be a regular non-symlink directory")
    return resolved


def _validate_schema(value: Mapping[str, Any], filename: str, *, label: str) -> None:
    schema_path = CONTRACTS / filename
    schema_raw = _regular_file(schema_path, label=f"{label} contract")
    schema = _strict_json(schema_raw, label=f"{label} contract")
    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
            key=lambda error: list(error.path),
        )
    except Exception as error:
        raise CommercialQualificationAssemblerError(
            f"{label} contract validation failed"
        ) from error
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        _fail(f"{label} schema violation at {location}")


def _write_json(path: Path, value: Mapping[str, Any], *, label: str) -> tuple[str, int]:
    raw = _canonical(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        _fail(f"{label} must not overwrite a symbolic link")
    try:
        path.write_bytes(raw)
    except OSError as error:
        raise CommercialQualificationAssemblerError(f"{label} could not be written") from error
    return _sha256(raw), len(raw)


def _copy_regular(source: Path, target: Path, *, label: str) -> None:
    raw = _regular_file(source, label=label)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.is_symlink():
        _fail(f"{label} target must not be a symbolic link")
    try:
        target.write_bytes(raw)
    except OSError as error:
        raise CommercialQualificationAssemblerError(f"{label} could not be copied") from error


def _copy_bundle(source: Path, target: Path) -> None:
    """Copy a verified bundle without following or creating symbolic links."""

    source = _safe_root(source, label="external bundle root")
    if target.exists():
        target = _safe_root(target, label="external evidence output root")
        if target != source:
            _fail("external evidence output root already exists")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        for current, directories, filenames in os.walk(source, topdown=True, followlinks=False):
            current_path = Path(current)
            for directory in directories:
                selected = current_path / directory
                if selected.is_symlink():
                    _fail("external bundle contains a symbolic link")
            for filename in filenames:
                selected = current_path / filename
                if selected.is_symlink():
                    _fail("external bundle contains a symbolic link")
                relative = selected.relative_to(source)
                _copy_regular(selected, target / relative, label="external bundle file")
    except OSError as error:
        raise CommercialQualificationAssemblerError(
            "external evidence bundle could not be copied"
        ) from error


def _bundle_index(root: Path, manifest: Mapping[str, Any]) -> dict[str, _BundleFile]:
    index: dict[str, _BundleFile] = {}
    references = manifest.get("files")
    if not isinstance(references, list) or not references:
        _fail("external bundle file inventory is missing")
    for reference in references:
        if not isinstance(reference, Mapping):
            _fail("external bundle file reference is invalid")
        relative = _safe_relative(reference.get("relative_path"), label="external bundle path")
        if relative in index:
            _fail("external bundle contains a duplicate file reference")
        selected = root / relative
        raw = _regular_file(selected, label="external bundle file")
        if reference.get("byte_size") != len(raw) or reference.get("sha256") != _sha256(raw):
            _fail("external bundle file binding differs from retained bytes")
        index[relative] = _BundleFile(
            relative=relative,
            path=selected,
            reference=reference,
            raw=raw,
        )
    return index


def _find_bundle_file(
    index: Mapping[str, _BundleFile],
    *,
    digest: str | None = None,
    evidence_kind: str | None = None,
    label: str,
) -> _BundleFile:
    matches = [
        item
        for item in index.values()
        if (digest is None or item.reference.get("sha256") == digest)
        and (evidence_kind is None or item.reference.get("evidence_kind") == evidence_kind)
    ]
    if len(matches) != 1:
        _fail(f"{label} must bind exactly one retained file")
    return matches[0]


def _load_active(path: Path) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, label="active qualification")
    _validate_schema(
        value,
        "v013-active-qualification.v2.schema.json",
        label="active qualification",
    )
    human_review = value.get("human_review")
    if (
        value.get("profile") != PROFILE
        or not isinstance(human_review, Mapping)
        or human_review.get("authenticity") != HUMAN_AUTHENTICITY
    ):
        _fail("active qualification is not the machine-only profile")
    return value, raw


def _load_classification(path: Path) -> tuple[dict[str, Any], bytes, list[str]]:
    value, raw = _read_json(path, label="v8 Gate classification")
    _validate_schema(
        value,
        "v013-release-gate-classification.v8.schema.json",
        label="v8 Gate classification",
    )
    if value.get("schema_version") != CLASSIFICATION_SCHEMA_VERSION:
        _fail("Gate classification must use the current v8 schema")
    if value.get("classification_id") != CLASSIFICATION_ID:
        _fail("Gate classification identity differs")
    categories = value.get("categories")
    gates = value.get("gates")
    if not isinstance(categories, list) or not isinstance(gates, list):
        _fail("Gate classification inventory is missing")
    core_ids: list[str] = []
    for category in categories:
        if not isinstance(category, Mapping):
            _fail("Gate classification category is invalid")
        if category.get("category") == "Core":
            listed = category.get("gate_ids")
            if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
                _fail("Core Gate classification inventory is invalid")
            core_ids.extend(listed)
    if len(core_ids) != len(set(core_ids)) or len(core_ids) != 14:
        _fail("current Core Gate inventory is not exactly 14 Gates")
    gate_by_id: dict[str, Mapping[str, Any]] = {}
    for gate in gates:
        if not isinstance(gate, Mapping) or not isinstance(gate.get("gate_id"), str):
            _fail("Gate classification entry is invalid")
        gate_id = gate["gate_id"]
        if gate_id in gate_by_id:
            _fail("Gate classification contains duplicate Gate identities")
        gate_by_id[gate_id] = gate
    if set(core_ids) != set(_GATE_EVIDENCE_KINDS) or any(
        gate_by_id.get(gate_id, {}).get("category") != "Core" for gate_id in core_ids
    ):
        _fail("v8 Gate classification is not closed against the machine verifier mapping")
    for gate_id in core_ids:
        required_roles = gate_by_id[gate_id].get("required_corpus_roles")
        if _GATE_EVIDENCE_KINDS[gate_id] <= _CANDIDATE_WORKFLOW_KINDS:
            expected_roles = ["candidate_full"]
        elif gate_id == "machine_reference_isolation":
            expected_roles = ["qualification_holdout", "final_blind"]
        else:
            expected_roles = ["qualification_holdout"]
        if required_roles != expected_roles:
            _fail(f"Gate {gate_id} corpus roles are not current v8")
        artifact_kinds = gate_by_id[gate_id].get("artifact_kinds")
        if artifact_kinds != sorted(_GATE_EVIDENCE_KINDS[gate_id]):
            _fail(f"Gate {gate_id} artifact mapping is not closed")
        if gate_by_id[gate_id].get("validator_id") != VALIDATOR_ID:
            _fail(f"Gate {gate_id} validator identity is not current v2")
        if str(gate_by_id[gate_id].get("validator_version")) != VALIDATOR_VERSION:
            _fail(f"Gate {gate_id} validator version is not current v2")
    return value, raw, core_ids


def _candidate_from_active(active: Mapping[str, Any]) -> dict[str, str]:
    binding = active.get("candidate_binding")
    if not isinstance(binding, Mapping):
        _fail("active candidate binding is missing")
    result = {
        "commit": binding.get("source_commit"),
        "tree": binding.get("source_tree"),
        "lock_sha256": binding.get("lock_sha256"),
        "wheel_sha256": binding.get("wheel_sha256"),
        "sdist_sha256": binding.get("sdist_sha256"),
    }
    if not GIT.fullmatch(str(result["commit"])) or not GIT.fullmatch(str(result["tree"])):
        _fail("active candidate commit/tree is invalid")
    for field in ("lock_sha256", "wheel_sha256", "sdist_sha256"):
        if not isinstance(result[field], str) or not SHA256.fullmatch(result[field]):
            _fail(f"active candidate {field} is invalid")
    return result  # type: ignore[return-value]


def _load_candidate_raw_inventory(
    candidate_raw_root: Path,
    *,
    candidate_run_id: int,
    candidate_commit: str,
    external_bundle_root: Path,
) -> tuple[dict[str, tuple[str, int]], bytes]:
    """Reopen Candidate Full bytes independently of the external bundle."""

    root = _safe_root(candidate_raw_root, label="Candidate Full raw artifact root")
    bundle = _safe_root(external_bundle_root, label="external evidence root")
    if root == bundle or root.is_relative_to(bundle) or bundle.is_relative_to(root):
        _fail("Candidate Full raw artifact root must be independent of the external bundle")
    inventory_path = root / "candidate-full-inventory-receipt.json"
    inventory, raw = _read_json(inventory_path, label="Candidate Full raw inventory receipt")
    if inventory.get("schema_version") != "deeplaw.candidate-full-inventory-receipt/v1":
        _fail("Candidate Full raw inventory schema is not current")
    if (
        inventory.get("record_kind") != "candidate_full_raw_inventory"
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
        path = _safe_relative(row.get("logical_path"), label="Candidate raw logical path")
        digest = row.get("sha256")
        size = row.get("bytes")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            _fail("Candidate raw inventory digest is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_FILE_BYTES:
            _fail("Candidate raw inventory byte size is invalid")
        if path in declared:
            _fail("Candidate Full raw inventory contains a duplicate path")
        declared[path] = (digest, size)
    observed: dict[str, tuple[str, int]] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            _fail("Candidate Full raw artifact root contains a symbolic link")
        if not path.is_file() or path == inventory_path:
            continue
        data = _regular_file(path, label="Candidate Full raw artifact")
        observed[path.relative_to(root).as_posix()] = (_sha256(data), len(data))
    if observed != declared:
        _fail("Candidate Full raw inventory does not match retained bytes")
    run_receipt, _run_receipt_raw = _read_json(
        root / "candidate-full-run-receipt.json",
        label="Candidate Full raw run receipt",
    )
    if (
        set(run_receipt)
        != {
            "schema_version",
            "record_kind",
            "workflow",
            "workflow_path",
            "run_id",
            "run_attempt",
            "head_sha",
            "event",
            "retention_days",
            "path_policy",
        }
        or run_receipt.get("schema_version") != "deeplaw.candidate-full-run-receipt/v1"
        or run_receipt.get("record_kind") != "candidate_full_raw_evidence"
        or run_receipt.get("workflow") != "Candidate Full"
        or run_receipt.get("workflow_path") != ".github/workflows/candidate-full.yml"
        or run_receipt.get("run_id") != candidate_run_id
        or run_receipt.get("head_sha") != candidate_commit
        or isinstance(run_receipt.get("run_attempt"), bool)
        or not isinstance(run_receipt.get("run_attempt"), int)
        or run_receipt["run_attempt"] < 1
        or run_receipt.get("event") not in {"workflow_dispatch", "pull_request"}
        or run_receipt.get("retention_days") != 90
        or run_receipt.get("path_policy") != "logical_relative_paths_only"
    ):
        _fail("Candidate Full raw run receipt identity differs")
    return declared, raw


def _assert_exact_bindings(
    *,
    candidate: Mapping[str, str],
    expected_candidate: Mapping[str, str],
    external: Mapping[str, str],
    expected_external: Mapping[str, str],
    candidate_run_id: int,
    evidence_run_id: int,
    qualification_run_id: int,
) -> None:
    if dict(candidate) != dict(expected_candidate):
        _fail("candidate identity differs from the exact requested binding")
    if dict(external) != dict(expected_external):
        _fail("external identity differs from the exact requested binding")
    if len({candidate_run_id, evidence_run_id, qualification_run_id}) != 3:
        _fail("candidate, evidence, and qualification run ids must be distinct")


def _threshold_binding(reference: Mapping[str, Any]) -> dict[str, Any]:
    thresholds = reference.get("thresholds")
    if not isinstance(thresholds, Mapping):
        _fail("semantic machine reference thresholds are missing")
    return {
        "threshold_id": "semantic-machine-reference-thresholds",
        "threshold_sha256": _sha256(_canonical(thresholds)),
        "frozen": True,
    }


def _typed_source_refs(value: Any) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        if {"relative_path", "sha256", "byte_size"} <= set(value):
            result.append(value)
        for item in value.values():
            result.extend(_typed_source_refs(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_typed_source_refs(item))
    return result


def _typed_source_data(
    reference: Mapping[str, Any], *, root: Path, label: str
) -> tuple[Any, Path]:
    """Read one v2 source through the legacy byte-hardening seam."""

    if reference.get("media_type") != "application/json":
        _fail(f"{label} must be JSON")
    try:
        source = _legacy_source_data(reference, root=root, label=label)
        value = _legacy_strict_json(source.raw, label=label)
        _legacy_reject_forbidden_keys(value)
    except (OSError, TypedQualificationEvidenceError, ValueError) as error:
        raise CommercialQualificationAssemblerError(f"{label} source was rejected") from error
    _projection(value, label=label)
    return value, source.path


def _parse_typed_records(
    *,
    bundle_root: Path,
    index: Mapping[str, _BundleFile],
    candidate: Mapping[str, str],
    candidate_run_id: int,
    evidence_run_id: int,
    holdout_sha256: str,
    blind_sha256: str,
    external_runner: Mapping[str, Any],
    external_scorer_panel: Mapping[str, Any],
    external_arbiter: Mapping[str, Any],
) -> list[_TypedRecord]:
    typed_kinds = set().union(*_GATE_EVIDENCE_KINDS.values()) | {"machine_reference_scorer"}
    records: list[_TypedRecord] = []
    for item in sorted(index.values(), key=lambda entry: entry.relative):
        if item.reference.get("evidence_kind") not in typed_kinds:
            continue
        envelope = _strict_json(item.raw, label="typed qualification manifest")
        _validate_schema(
            envelope,
            "typed-qualification-evidence.v2.schema.json",
            label="typed qualification manifest",
        )
        kind = envelope.get("kind")
        if kind == "human_gold_scorer":
            _fail("human_gold_scorer is not a valid v8 input")
        if (
            not isinstance(kind, str)
            or kind != item.reference.get("evidence_kind")
            or kind not in typed_kinds
        ):
            _fail("typed qualification evidence kind differs from the closed v8 mapping")
        candidate_binding = envelope.get("candidate_binding")
        if candidate_binding != dict(candidate):
            _fail("typed qualification candidate binding differs")
        run_binding = envelope.get("run_binding")
        corpus = envelope.get("corpus")
        if not isinstance(run_binding, Mapping) or not isinstance(corpus, Mapping):
            _fail("typed qualification run/corpus binding is missing")
        is_candidate = kind in _CANDIDATE_WORKFLOW_KINDS
        expected_workflow = candidate_run_id if is_candidate else evidence_run_id
        run_id = run_binding.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            _fail("typed qualification run id is invalid")
        if run_binding.get("workflow_run_id") != expected_workflow:
            _fail("typed qualification workflow run binding differs")
        corpus_role = corpus.get("role")
        corpus_sha = corpus.get("sha256")
        if not isinstance(corpus_sha, str) or not SHA256.fullmatch(corpus_sha):
            _fail("typed qualification corpus hash is invalid")
        expected_corpus = None
        if is_candidate:
            if corpus_role != "candidate_full":
                _fail("Candidate Full typed evidence has the wrong corpus role")
        elif corpus_role == "qualification_holdout":
            expected_corpus = holdout_sha256
        elif corpus_role == "final_blind":
            expected_corpus = blind_sha256
        else:
            _fail("external typed evidence has an unsupported corpus role")
        if expected_corpus is not None and corpus_sha != expected_corpus:
            _fail("typed qualification corpus hash differs from its frozen input")
        if (
            envelope.get("profile") != PROFILE
            or envelope.get("reference_provenance") != REFERENCE_PROVENANCE
        ):
            _fail("typed qualification manifest is not machine-only")
        if envelope.get("human_authenticity") != HUMAN_AUTHENTICITY:
            _fail("typed qualification manifest makes a human authenticity claim")
        if not is_candidate:
            expected_runner = (
                _exact_wheel_runner_identity()
                if kind == "exact_wheel_execution"
                else external_runner
            )
            if envelope.get("runner") != expected_runner:
                _fail("typed qualification runner identity differs")
            if kind == "machine_reference_scorer":
                if envelope.get("scorer_panel") != external_scorer_panel:
                    _fail("typed qualification scorer panel differs")
                if envelope.get("arbiter") != external_arbiter:
                    _fail("typed qualification arbiter differs")
        record_sha = envelope.get("record_sha256")
        if (
            not isinstance(record_sha, str)
            or not SHA256.fullmatch(record_sha)
            or record_sha != _record_digest(envelope)
        ):
            _fail("typed qualification manifest record digest differs")
        sources = _typed_source_refs(envelope.get("payload"))
        if not sources:
            _fail("typed qualification manifest contains no source receipts")
        for source in sources:
            _safe_relative(source.get("relative_path"), label="typed source path")
            try:
                _legacy_source_data(
                    source,
                    root=bundle_root,
                    label="typed source",
                )
            except (OSError, TypedQualificationEvidenceError, ValueError) as error:
                raise CommercialQualificationAssemblerError(
                    "typed source receipt was rejected"
                ) from error
        try:
            parser = _LEGACY_PARSERS.get(kind)
            if parser is None:
                _fail(f"typed qualification kind {kind} has no parser")
            kwargs: dict[str, Any] = {"root": bundle_root, "record_sha256": record_sha}
            if kind in {
                "legal_rows",
                "wiki_journey_rows",
                "context_capsule_selection_usage",
                "scale_report",
                "host_event_sequence",
            }:
                kwargs["expected_corpus_sha256"] = expected_corpus
            if kind == "exact_wheel_execution":
                kwargs["expected_candidate_run_id"] = candidate_run_id
            derived = parser(envelope, **kwargs)
        except (OSError, TypedQualificationEvidenceError, ValueError, KeyError) as error:
            raise CommercialQualificationAssemblerError(
                "typed qualification parser rejected a bundle manifest"
            ) from error
        derived = dict(derived)
        derived["schema_version"] = DERIVED_SCHEMA_VERSION
        if derived.get("kind") != kind or derived.get("status") != "passed":
            _fail("typed qualification evidence did not derive a passed result")
        if derived.get("evidence_record_sha256") != record_sha:
            _fail("typed qualification derived record is not bound to its manifest")
        records.append(
            _TypedRecord(
                kind=kind,
                path=item.path,
                manifest=envelope,
                derived=derived,
                bundle_relative=item.relative,
            )
        )
    if not records:
        _fail("external bundle contains no typed qualification manifests")
    return records


def _record_by_kind(records: Sequence[_TypedRecord], kind: str, *, label: str) -> _TypedRecord:
    matches = [item for item in records if item.kind == kind]
    if len(matches) != 1:
        _fail(f"{label} requires exactly one {kind} manifest")
    return matches[0]


def _select_records(
    records: Sequence[_TypedRecord],
    *,
    holdout_sha256: str,
    blind_sha256: str,
) -> tuple[list[_TypedRecord], str, str]:
    external = [item for item in records if item.kind not in _CANDIDATE_WORKFLOW_KINDS]
    if not external:
        _fail("external typed evidence is missing")
    holdout = [
        item
        for item in external
        if item.manifest.get("corpus", {}).get("role") == "qualification_holdout"
    ]
    blind = [
        item
        for item in external
        if item.manifest.get("corpus", {}).get("role") == "final_blind"
    ]
    if any(
        item.manifest.get("corpus", {}).get("sha256") != holdout_sha256
        for item in holdout
    ) or any(
        item.manifest.get("corpus", {}).get("sha256") != blind_sha256
        for item in blind
    ):
        _fail("external typed evidence differs from its frozen corpus hash")
    holdout_counts = {
        "host_event_sequence": 6,
        "exact_wheel_execution": 1,
        "machine_reference_scorer": 1,
        "legal_rows": 1,
        "wiki_journey_rows": 1,
        "context_capsule_selection_usage": 1,
        "scale_report": 1,
    }
    by_holdout_kind: dict[str, list[_TypedRecord]] = defaultdict(list)
    for item in holdout:
        by_holdout_kind[item.kind].append(item)
    for kind, count in holdout_counts.items():
        if len(by_holdout_kind[kind]) != count:
            _fail(f"external typed evidence requires {count} {kind} manifests")
    if len(holdout) != sum(holdout_counts.values()):
        _fail("qualification holdout typed evidence inventory is not closed")
    if len(blind) != 1 or blind[0].kind != "machine_reference_scorer":
        _fail("final blind must contain exactly one machine-reference scorer manifest")
    hosts: dict[str, list[_TypedRecord]] = defaultdict(list)
    for item in by_holdout_kind["host_event_sequence"]:
        host = item.derived.get("metrics", {}).get("host")
        task_case = item.derived.get("metrics", {}).get("task_case")
        if host not in {"codex", "opencode"} or task_case not in {
            "cold/new",
            "resume/fork/concurrent-worktree",
            "compaction/forget/stale",
        }:
            _fail("Host typed evidence has an invalid derived identity")
        hosts[host].append(item)
    for host in ("codex", "opencode"):
        host_records = hosts[host]
        if len(host_records) != 3:
            _fail(f"{host} Host qualification requires three typed runs")
        if len({item.manifest["run_binding"]["run_id"] for item in host_records}) != 3:
            _fail(f"{host} Host qualification requires distinct run ids")
        if {item.derived["metrics"]["task_case"] for item in host_records} != {
            "cold/new",
            "resume/fork/concurrent-worktree",
            "compaction/forget/stale",
        }:
            _fail(f"{host} Host qualification does not cover the frozen task set")
    return external, "qualification_holdout", holdout_sha256


def _validator_binding(path: Path, *, label: str) -> dict[str, Any]:
    raw = _regular_file(path, label=label)
    return {
        "relative_path": path.resolve(strict=True).relative_to(REPOSITORY).as_posix(),
        "byte_size": len(raw),
        "file_sha256": _sha256(raw),
    }


def _execution_schema_fields() -> set[str]:
    schema_path = CONTRACTS / "provenance-bound-gate-result.v4.schema.json"
    schema = _strict_json(
        _regular_file(schema_path, label="Gate result contract"),
        label="Gate result contract",
    )
    execution = schema.get("$defs", {}).get("execution", {})
    properties = execution.get("properties", {}) if isinstance(execution, Mapping) else {}
    required = execution.get("required", []) if isinstance(execution, Mapping) else []
    if not isinstance(properties, Mapping) or not isinstance(required, list):
        _fail("Gate result execution contract is unavailable")
    return set(properties) | set(required)


def _execution(
    manifest: Mapping[str, Any],
    *,
    input_id: str,
    kind: str,
    derived: Mapping[str, Any],
) -> dict[str, Any]:
    run = manifest.get("run_binding")
    if not isinstance(run, Mapping):
        _fail("typed execution run binding is missing")
    run_id = run.get("run_id")
    workflow_run_id = run.get("workflow_run_id")
    fields = _execution_schema_fields()
    if fields != {"run_id", "workflow_run_id", "input_refs", "evidence_kind"}:
        _fail("Gate execution contract is not the converged v4 contract")
    return {
        "run_id": run_id,
        "workflow_run_id": workflow_run_id,
        "input_refs": [input_id],
        "evidence_kind": kind,
    }


def _input_reference(record: _TypedRecord, *, assets_root: Path, input_id: str) -> dict[str, Any]:
    try:
        relative = (
            record.path.resolve(strict=True)
            .relative_to(assets_root.resolve(strict=True))
            .as_posix()
        )
    except (OSError, ValueError) as error:
        raise CommercialQualificationAssemblerError(
            "typed evidence manifest is outside the assets root"
        ) from error
    raw = record.path.read_bytes()
    record_sha = record.manifest.get("record_sha256")
    if not isinstance(record_sha, str) or not SHA256.fullmatch(record_sha):
        _fail("typed evidence manifest record digest is missing")
    return {
        "input_id": input_id,
        "relative_path": _safe_relative(relative, label="Gate input path"),
        "byte_size": len(raw),
        "file_sha256": _sha256(raw),
        "schema_version": TYPED_SCHEMA_VERSION,
        "record_sha256": record_sha,
        "artifact_kind": "typed-qualification-evidence",
        "evidence_kind": record.kind,
        "derived_record_sha256": _derived_digest(record.derived),
    }


def _gate_result(
    gate_id: str,
    records: Sequence[_TypedRecord],
    *,
    assets_root: Path,
    qualification_run_id: int,
    candidate: Mapping[str, str],
    classification_binding: Mapping[str, Any],
    protocol_binding: Mapping[str, Any],
    threshold_binding: Mapping[str, Any],
    reference_binding: Mapping[str, Any],
    corpora: Sequence[Mapping[str, Any]],
    validator_source: Mapping[str, Any],
    validator_executable: Mapping[str, Any],
) -> dict[str, Any]:
    if not records:
        _fail(f"Gate {gate_id} has no typed evidence")
    inputs: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    hard_failures: list[dict[str, Any]] = []
    run_ids: list[str] = []
    hosts: set[str] = set()
    task_cases: set[str] = set()
    for index, record in enumerate(records):
        input_id = f"input:{gate_id}:{index + 1}"
        reference = _input_reference(record, assets_root=assets_root, input_id=input_id)
        inputs.append(reference)
        run_id = record.manifest["run_binding"]["run_id"]
        run_ids.append(run_id)
        derived_metrics = record.derived.get("metrics")
        derived_failures = record.derived.get("hard_failure_counts")
        if not isinstance(derived_metrics, Mapping) or not isinstance(derived_failures, Mapping):
            _fail(f"Gate {gate_id} typed parser output is incomplete")
        if record.derived.get("status") != "passed":
            _fail(f"Gate {gate_id} typed parser output did not pass")
        host = derived_metrics.get("host")
        task_case = derived_metrics.get("task_case")
        if isinstance(host, str):
            hosts.add(host)
        if isinstance(task_case, str):
            task_cases.add(task_case)
        executions.append(
            _execution(
                record.manifest,
                input_id=input_id,
                kind=record.kind,
                derived=record.derived,
            )
        )
        for metric_name, observed in sorted(derived_metrics.items()):
            metrics.append(
                {
                    "metric": f"{input_id}:{metric_name}",
                    "observed": observed,
                    "input_refs": [input_id],
                }
            )
        for failure_id, count in sorted(derived_failures.items()):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                _fail(f"Gate {gate_id} typed hard-failure count is invalid")
            hard_failures.append(
                {
                    "failure_id": f"{input_id}:{failure_id}",
                    "count": count,
                    "maximum_allowed": 0,
                    "input_refs": [input_id],
                }
            )
    if len(run_ids) != len(set(run_ids)):
        _fail(f"Gate {gate_id} contains duplicate typed run identities")
    result: dict[str, Any] = {
        "schema_version": GATE_SCHEMA_VERSION,
        "profile": PROFILE,
        "reference_provenance": REFERENCE_PROVENANCE,
        "human_authenticity": HUMAN_AUTHENTICITY,
        "qualification_run_id": qualification_run_id,
        "gate_id": gate_id,
        "category": "Core",
        "validator_id": VALIDATOR_ID,
        "validator_version": VALIDATOR_VERSION,
        "validator_source": dict(validator_source),
        "validator_executable": dict(validator_executable),
        "classification_binding": dict(classification_binding),
        "candidate_binding": {
            "candidate_commit": candidate["commit"],
            "candidate_tree": candidate["tree"],
            "candidate_wheel_sha256": candidate["wheel_sha256"],
            "candidate_sdist_sha256": candidate["sdist_sha256"],
        },
        "protocol_binding": dict(protocol_binding),
        "threshold_binding": dict(threshold_binding),
        "reference_binding": dict(reference_binding),
        "corpora": [dict(corpus) for corpus in corpora],
        "status": "passed",
        "executions": executions,
        "run_ids": run_ids,
        "metrics": metrics,
        "hard_failures": hard_failures,
        "inputs": inputs,
    }
    result["result_sha256"] = _record_digest(result, field="result_sha256")
    _validate_schema(result, "provenance-bound-gate-result.v4.schema.json", label=f"Gate {gate_id}")
    return result


def _copy_and_verify_source(
    source: Path,
    *,
    output_root: Path,
    relative: str,
    expected_sha256: str,
    label: str,
) -> Path:
    relative = _safe_relative(relative, label=f"{label} path")
    target = output_root / relative
    raw = _regular_file(source, label=label)
    if _sha256(raw) != expected_sha256:
        _fail(f"{label} hash differs")
    _copy_regular(source, target, label=label)
    return target


def _candidate_version(name: str, *, suffix: str) -> str:
    if suffix == "wheel":
        match = re.fullmatch(r"deeplaw-([0-9][A-Za-z0-9.!+_-]*)-[^-]+-[^-]+-[^-]+\.whl", name)
    else:
        match = re.fullmatch(r"deeplaw-([0-9][A-Za-z0-9.!+_-]*)\.tar\.gz", name)
    if match is None:
        _fail(f"candidate {suffix} artifact filename is invalid")
    return match.group(1)


def _environment() -> dict[str, Any]:
    return {
        "platform_system": platform.system() or "unknown",
        "platform_release": platform.release() or "unknown",
        "platform_version": platform.version() or "unknown",
        "machine": platform.machine() or "unknown",
        "python_implementation": platform.python_implementation() or "unknown",
        "python_version": platform.python_version() or "unknown",
        "python_executable_name": Path(sys.executable).name or "python",
        "uv_version": os.environ.get("UV_VERSION", "unknown"),
        "ci": os.environ.get("CI", "").lower() == "true",
        "github_actions": os.environ.get("GITHUB_ACTIONS", "").lower() == "true",
        "github_runner_os": os.environ.get("RUNNER_OS") or platform.system() or "unknown",
        "github_runner_arch": os.environ.get("RUNNER_ARCH") or platform.machine() or "unknown",
    }


def _external_binding(
    *,
    semantic_reference_sha256: str,
    candidate_binding_sha256: str,
    qualification_holdout_sha256: str,
    final_blind_holdout_sha256: str,
    agent_roster_sha256: str,
    agent_consensus_sha256: str,
    agent_isolation_sha256: str,
    runner_sha256: str,
    scorer_panel_sha256: str,
    arbiter_sha256: str,
    compiler_scorer_isolation_sha256: str,
) -> dict[str, str]:
    result = {
        "semantic_reference_sha256": semantic_reference_sha256,
        "candidate_binding_sha256": candidate_binding_sha256,
        "qualification_holdout_sha256": qualification_holdout_sha256,
        "final_blind_holdout_sha256": final_blind_holdout_sha256,
        "agent_roster_sha256": agent_roster_sha256,
        "agent_consensus_sha256": agent_consensus_sha256,
        "agent_isolation_sha256": agent_isolation_sha256,
        "runner_sha256": runner_sha256,
        "scorer_panel_sha256": scorer_panel_sha256,
        "arbiter_sha256": arbiter_sha256,
        "compiler_scorer_isolation_sha256": compiler_scorer_isolation_sha256,
    }
    for field, value in result.items():
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            _fail(f"external {field} is not a SHA-256 digest")
    return result


def assemble_commercial_qualification(
    *,
    bundle_root: Path | str,
    assets_root: Path | str,
    candidate_raw_root: Path | str,
    active_qualification: Path | str,
    classification: Path | str,
    protocol: Path | str,
    candidate_run_id: int,
    evidence_run_id: int,
    qualification_run_id: int,
    candidate: Mapping[str, str],
    external_inputs: Mapping[str, str],
) -> dict[str, Any]:
    """Validate typed evidence and write a v8 report, Gates, and release manifest.

    ``candidate`` must contain exactly ``commit``, ``tree``, ``lock_sha256``,
    ``wheel_sha256``, and ``sdist_sha256``.  ``external_inputs`` must contain
    exactly the eleven machine reference hash fields used by the v8 release manifest.  No status or
    metric field is accepted from either mapping.
    """

    candidate_keys = {"commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"}
    external_keys = {
        "semantic_reference_sha256",
        "candidate_binding_sha256",
        "qualification_holdout_sha256",
        "final_blind_holdout_sha256",
        "agent_roster_sha256",
        "agent_consensus_sha256",
        "agent_isolation_sha256",
        "runner_sha256",
        "scorer_panel_sha256",
        "arbiter_sha256",
        "compiler_scorer_isolation_sha256",
    }
    if set(candidate) != candidate_keys or set(external_inputs) != external_keys:
        _fail("assembler identity descriptors are not closed")
    for field in ("commit", "tree"):
        if not isinstance(candidate[field], str) or not GIT.fullmatch(candidate[field]):
            _fail(f"candidate {field} is invalid")
    for field in candidate_keys - {"commit", "tree"}:
        if not isinstance(candidate[field], str) or not SHA256.fullmatch(candidate[field]):
            _fail(f"candidate {field} is invalid")
    expected_candidate = dict(candidate)
    expected_external = _external_binding(**dict(external_inputs))
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (candidate_run_id, evidence_run_id, qualification_run_id)
    ):
        _fail("workflow run ids must be positive integers")

    source_bundle = _safe_root(Path(bundle_root), label="external bundle root")
    try:
        validate_external_bundle_v4(
            source_bundle,
            expected_candidate_run_id=candidate_run_id,
            expected_evidence_run_id=evidence_run_id,
            active_qualification=Path(active_qualification).expanduser(),
        )
    except (OSError, ExternalQualificationBundleV4Error, ValueError) as error:
        raise CommercialQualificationAssemblerError(
            "external qualification bundle v4 rejected"
        ) from error
    output_root = Path(assets_root).expanduser()
    if output_root.exists() and output_root.is_symlink():
        _fail("assets root must not be a symbolic link")
    output_root.mkdir(parents=True, exist_ok=True)
    output_root = _safe_root(output_root, label="assets root")
    bundle_destination = output_root / "external-evidence"
    if source_bundle != bundle_destination.resolve(strict=False):
        _copy_bundle(source_bundle, bundle_destination)
    bundle_root_resolved = _safe_root(bundle_destination, label="external evidence output root")

    active, active_raw = _load_active(Path(active_qualification).expanduser())
    _classification_value, classification_raw, core_ids = _load_classification(
        Path(classification).expanduser()
    )
    manifest, _manifest_raw = _read_json(
        bundle_root_resolved / "bundle-manifest.json",
        label="external bundle manifest",
    )
    _validate_schema(
        manifest,
        "external-qualification-bundle-manifest.v4.schema.json",
        label="external qualification bundle manifest",
    )
    if manifest.get("record_sha256") != _record_digest(manifest):
        _fail("external bundle manifest record digest differs")
    for field, expected in (
        ("candidate_run_id", candidate_run_id),
        ("evidence_run_id", evidence_run_id),
    ):
        if manifest.get(field) != expected:
            _fail(f"external bundle {field} differs")
    if (
        manifest.get("profile") != PROFILE
        or manifest.get("reference_provenance") != REFERENCE_PROVENANCE
        or manifest.get("human_authenticity") != HUMAN_AUTHENTICITY
    ):
        _fail("external bundle is not the machine-only profile")
    index = _bundle_index(bundle_root_resolved, manifest)
    active_candidate = _candidate_from_active(active)
    manifest_candidate = manifest.get("candidate_binding")
    if not isinstance(manifest_candidate, Mapping):
        _fail("external bundle candidate binding is missing")
    if active_candidate != expected_candidate or dict(manifest_candidate) != active_candidate:
        _fail("external bundle candidate binding differs from the exact candidate")
    if manifest.get("external_inputs") != {
        "semantic_reference_sha256": expected_external["semantic_reference_sha256"],
        "candidate_binding_sha256": expected_external["candidate_binding_sha256"],
        "qualification_holdout_sha256": expected_external["qualification_holdout_sha256"],
        "final_blind_holdout_sha256": expected_external["final_blind_holdout_sha256"],
        "agent_roster_sha256": expected_external["agent_roster_sha256"],
        "agent_consensus_sha256": expected_external["agent_consensus_sha256"],
        "agent_isolation_sha256": expected_external["agent_isolation_sha256"],
        "runner_sha256": expected_external["runner_sha256"],
        "scorer_panel_sha256": expected_external["scorer_panel_sha256"],
        "arbiter_sha256": expected_external["arbiter_sha256"],
        "compiler_scorer_isolation_sha256": expected_external["compiler_scorer_isolation_sha256"],
    }:
        _fail("external bundle inputs differ from the exact machine reference binding")
    if dict(manifest_candidate) != active_candidate:
        _fail("external bundle candidate binding differs from the exact candidate")
    # Reopen the independent Candidate Full inventory before consuming any candidate typed source.
    try:
        candidate_raw_inventory, candidate_raw_inventory_bytes = _load_candidate_raw_inventory(
            Path(candidate_raw_root).expanduser(),
            candidate_run_id=candidate_run_id,
            candidate_commit=active_candidate["commit"],
            external_bundle_root=bundle_root_resolved,
        )
    except Exception as error:
        raise CommercialQualificationAssemblerError(
            "Candidate Full raw inventory rejected"
        ) from error
    bundle_inventory_file = _find_bundle_file(
        index,
        digest=manifest.get("candidate_full_raw_inventory_sha256"),
        evidence_kind="candidate_full_raw_inventory",
        label="external Candidate Full raw inventory",
    )
    bundle_inventory = _strict_json(
        bundle_inventory_file.raw,
        label="external Candidate Full raw inventory",
    )
    if bundle_inventory_file.raw != candidate_raw_inventory_bytes:
        _fail("external evidence does not retain the exact Candidate Full inventory receipt")
    bundle_rows = bundle_inventory.get("files")
    if not isinstance(bundle_rows, list):
        _fail("external Candidate Full raw inventory rows are missing")
    bundle_candidate_inventory: dict[str, tuple[str, int]] = {}
    for row in bundle_rows:
        if not isinstance(row, Mapping) or set(row) != {"logical_path", "sha256", "bytes"}:
            _fail("external Candidate Full raw inventory row is not closed")
        logical_path = _safe_relative(
            row.get("logical_path"),
            label="external Candidate Full raw logical path",
        )
        digest = row.get("sha256")
        size = row.get("bytes")
        if (
            not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or logical_path in bundle_candidate_inventory
        ):
            _fail("external Candidate Full raw inventory row is invalid")
        bundle_candidate_inventory[logical_path] = (digest, size)
    if bundle_candidate_inventory != candidate_raw_inventory:
        _fail("external evidence does not bind the independent Candidate Full raw inventory")

    semantic_reference_file = _find_bundle_file(
        index,
        digest=expected_external["semantic_reference_sha256"],
        evidence_kind="semantic_machine_reference",
        label="semantic machine reference",
    )
    semantic_reference = _strict_json(
        semantic_reference_file.raw,
        label="semantic machine reference",
    )
    _validate_schema(
        semantic_reference,
        "semantic-machine-reference.v1.schema.json",
        label="semantic machine reference",
    )
    threshold_binding = _threshold_binding(semantic_reference)
    machine_binding_file = _find_bundle_file(
        index,
        digest=expected_external["candidate_binding_sha256"],
        evidence_kind="post_build_machine_reference_binding",
        label="Candidate machine reference binding",
    )
    machine_binding = _strict_json(
        machine_binding_file.raw,
        label="Candidate machine reference binding",
    )
    _validate_schema(
        machine_binding,
        "candidate-gold-binding-receipt.v2.schema.json",
        label="Candidate machine reference binding",
    )
    if machine_binding.get("record_sha256") != _record_digest(machine_binding):
        _fail("Candidate machine reference binding record digest differs")
    external_runner = machine_binding.get("runner")
    external_scorer_panel = machine_binding.get("scorer_panel")
    external_arbiter = machine_binding.get("arbiter")
    if (
        not isinstance(external_runner, Mapping)
        or not isinstance(external_scorer_panel, Mapping)
        or not isinstance(external_arbiter, Mapping)
    ):
        _fail("Candidate machine reference runner/scorer panel/arbiter identities are missing")
    if external_runner.get("sha256") != expected_external["runner_sha256"]:
        _fail("Candidate machine reference runner hash differs")
    if external_scorer_panel.get("panel_sha256") != expected_external["scorer_panel_sha256"]:
        _fail("Candidate machine reference scorer panel hash differs")
    for field, binding_field in (
        ("semantic_reference_sha256", ("semantic_reference", "sha256")),
        ("agent_roster_sha256", ("agent_roster", "sha256")),
        ("agent_consensus_sha256", ("agent_consensus", "sha256")),
        ("agent_isolation_sha256", ("agent_isolation", "sha256")),
        ("runner_sha256", ("runner", "sha256")),
        ("qualification_holdout_sha256", ("holdout", "sha256")),
        ("final_blind_holdout_sha256", ("blind", "sha256")),
    ):
        section, key = binding_field
        section_value = machine_binding.get(section)
        if (
            not isinstance(section_value, Mapping)
            or section_value.get(key) != expected_external[field]
        ):
            _fail(f"Candidate machine reference binding differs for {field}")
    if external_arbiter.get("sha256") != expected_external["arbiter_sha256"]:
        _fail("Candidate machine reference arbiter hash differs")
    records = _parse_typed_records(
        bundle_root=bundle_root_resolved,
        index=index,
        candidate=active_candidate,
        candidate_run_id=candidate_run_id,
        evidence_run_id=evidence_run_id,
        holdout_sha256=expected_external["qualification_holdout_sha256"],
        blind_sha256=expected_external["final_blind_holdout_sha256"],
        external_runner=external_runner,
        external_scorer_panel=external_scorer_panel,
        external_arbiter=external_arbiter,
    )
    selected_external, external_role, external_corpus_sha256 = _select_records(
        records,
        holdout_sha256=expected_external["qualification_holdout_sha256"],
        blind_sha256=expected_external["final_blind_holdout_sha256"],
    )
    candidate_records = [item for item in records if item.kind in _CANDIDATE_WORKFLOW_KINDS]
    if len(candidate_records) != 3:
        _fail("Candidate Full typed evidence requires one JUnit, platform, and supply manifest")
    candidate_corpus_values = {
        item.manifest["corpus"]["sha256"] for item in candidate_records
    }
    if len(candidate_corpus_values) != 1:
        _fail("Candidate Full typed evidence does not share one corpus identity")
    candidate_corpus_sha256 = next(iter(candidate_corpus_values))
    expected_candidate_corpus_sha256 = _sha256(
        _canonical(
            {
                field: active_candidate[field]
                for field in (
                    "commit",
                    "tree",
                    "lock_sha256",
                    "wheel_sha256",
                    "sdist_sha256",
                )
            }
        )
    )
    if candidate_corpus_sha256 != expected_candidate_corpus_sha256:
        _fail("Candidate Full typed corpus is not derived from the exact candidate binding")
    candidate_corpus = {
        "role": "candidate_full",
        "source": "repository",
        "sha256": candidate_corpus_sha256,
        "frozen": True,
    }
    external_corpus = {
        "role": external_role,
        "source": "repository_external",
        "sha256": external_corpus_sha256,
        "frozen": True,
    }
    classification_binding = {
        "classification_id": CLASSIFICATION_ID,
        "classification_schema_version": CLASSIFICATION_SCHEMA_VERSION,
        "classification_sha256": _sha256(classification_raw),
    }
    protocol_binding_active = active.get("protocol_binding")
    if not isinstance(protocol_binding_active, Mapping):
        _fail("active protocol binding is missing")
    protocol_relative = _safe_relative(
        protocol_binding_active.get("relative_path"),
        label="qualification protocol path",
    )
    protocol_raw = _regular_file(Path(protocol).expanduser(), label="qualification protocol")
    protocol_value = _strict_json(protocol_raw, label="qualification protocol")
    _validate_schema(
        protocol_value,
        "v013-qualification-protocol.v2.schema.json",
        label="qualification protocol",
    )
    protocol_sha256 = _sha256(protocol_raw)
    if (
        protocol_sha256 != protocol_binding_active.get("sha256")
        or protocol_binding_active.get("schema_version")
        != "deeplaw.v013-qualification-protocol/v2"
        or protocol_value.get("protocol_id") != protocol_binding_active.get("protocol_id")
        or protocol_value.get("profile") != PROFILE
    ):
        _fail("qualification protocol hash differs from active qualification")
    _copy_and_verify_source(
        Path(protocol).expanduser(),
        output_root=output_root,
        relative=protocol_relative,
        expected_sha256=protocol_sha256,
        label="qualification protocol",
    )
    protocol_binding = {
        "protocol_id": protocol_binding_active.get("protocol_id"),
        "protocol_sha256": protocol_sha256,
        "frozen": True,
    }
    if not isinstance(protocol_binding["protocol_id"], str) or not protocol_binding["protocol_id"]:
        _fail("qualification protocol identity is invalid")
    reference_binding = {
        "semantic_reference_sha256": expected_external["semantic_reference_sha256"],
        "agent_roster_sha256": expected_external["agent_roster_sha256"],
        "agent_consensus_sha256": expected_external["agent_consensus_sha256"],
        "agent_isolation_sha256": expected_external["agent_isolation_sha256"],
        "scorer_panel_sha256": expected_external["scorer_panel_sha256"],
        "arbiter_sha256": expected_external["arbiter_sha256"],
        "frozen": True,
    }
    validator_source = _validator_binding(
        REPOSITORY / "benchmarks/release/typed_qualification_evidence.py",
        label="typed qualification parser source",
    )
    validator_executable = _validator_binding(
        Path(__file__).resolve(),
        label="commercial qualification assembler source",
    )

    # Closed source-kind mapping for the machine-only v8 profile.
    by_kind: dict[str, list[_TypedRecord]] = defaultdict(list)
    for item in candidate_records + selected_external:
        by_kind[item.kind].append(item)
    gate_records: dict[str, list[_TypedRecord]] = {
        "canonical_integrity": [
            _record_by_kind(selected_external, "exact_wheel_execution", label="canonical_integrity")
        ],
        "migration_recovery": [
            _record_by_kind(candidate_records, "candidate_full_junit", label="migration_recovery")
        ],
        "supported_platforms": [
            _record_by_kind(
                candidate_records,
                "candidate_platform_receipt",
                label="supported_platforms",
            )
        ],
        "reproducible_supply_chain": [
            _record_by_kind(
                candidate_records,
                "retained_supply_chain",
                label="reproducible_supply_chain",
            )
        ],
        "secret_host_isolation": by_kind["host_event_sequence"],
        "timeline": by_kind["host_event_sequence"],
        "codex": [
            item
            for item in by_kind["host_event_sequence"]
            if item.derived["metrics"]["host"] == "codex"
        ],
        "opencode": [
            item
            for item in by_kind["host_event_sequence"]
            if item.derived["metrics"]["host"] == "opencode"
        ],
        "machine_reference_isolation": by_kind["machine_reference_scorer"],
        "legal_evidence": [
            _record_by_kind(selected_external, "legal_rows", label="legal_evidence")
        ],
        "source_citation_locator": [
            _record_by_kind(selected_external, "legal_rows", label="source_citation_locator")
        ],
        "bounded_context": [
            _record_by_kind(
                selected_external,
                "context_capsule_selection_usage",
                label="bounded_context",
            )
        ],
        "scale_performance": [
            _record_by_kind(selected_external, "scale_report", label="scale_performance")
        ],
        "selective_forget": [
            _record_by_kind(selected_external, "wiki_journey_rows", label="selective_forget")
        ],
    }
    if set(gate_records) != set(_GATE_EVIDENCE_KINDS) or set(gate_records) != set(core_ids):
        _fail("assembler Gate mapping is not the closed v8 14-Gate mapping")
    gate_results: dict[str, dict[str, Any]] = {}
    gate_paths: dict[str, tuple[str, int, str]] = {}
    for gate_id in core_ids:
        if all(item.kind in _CANDIDATE_WORKFLOW_KINDS for item in gate_records[gate_id]):
            gate_corpora = [candidate_corpus]
        elif gate_id == "machine_reference_isolation":
            gate_corpora = [
                external_corpus,
                {
                    "role": "final_blind",
                    "source": "repository_external",
                    "sha256": expected_external["final_blind_holdout_sha256"],
                    "frozen": True,
                },
            ]
        else:
            gate_corpora = [external_corpus]
        gate = _gate_result(
            gate_id,
            gate_records[gate_id],
            assets_root=output_root,
            qualification_run_id=qualification_run_id,
            candidate=active_candidate,
            classification_binding=classification_binding,
            protocol_binding=protocol_binding,
            threshold_binding=threshold_binding,
            reference_binding=reference_binding,
            corpora=gate_corpora,
            validator_source=validator_source,
            validator_executable=validator_executable,
        )
        relative = f"evidence/gate-results/{gate_id}.json"
        file_sha, size = _write_json(output_root / relative, gate, label=f"Gate {gate_id}")
        gate_results[gate_id] = gate
        gate_paths[gate_id] = (relative, size, file_sha)

    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "profile": PROFILE,
        "reference_provenance": REFERENCE_PROVENANCE,
        "human_authenticity": HUMAN_AUTHENTICITY,
        "report_kind": "v013_machine_provenance_bound_gate_collection",
        "report_id": f"commercial-v013-{qualification_run_id}",
        "qualification_run_id": qualification_run_id,
        "candidate_binding": {
            "candidate_commit": active_candidate["commit"],
            "candidate_tree": active_candidate["tree"],
            "candidate_wheel_sha256": active_candidate["wheel_sha256"],
            "candidate_sdist_sha256": active_candidate["sdist_sha256"],
        },
        "protocol_binding": protocol_binding,
        "threshold_binding": threshold_binding,
        "reference_binding": reference_binding,
        "corpora": [
            candidate_corpus,
            external_corpus,
            {
                "role": "final_blind",
                "source": "repository_external",
                "sha256": expected_external["final_blind_holdout_sha256"],
                "frozen": True,
            },
        ],
        "classification_binding": classification_binding,
        "gate_results": [
            {
                "gate_id": gate_id,
                "category": "Core",
                "result": {
                    "relative_path": gate_paths[gate_id][0],
                    "byte_size": gate_paths[gate_id][1],
                    "file_sha256": gate_paths[gate_id][2],
                    "schema_version": GATE_SCHEMA_VERSION,
                    "record_sha256": gate_results[gate_id]["result_sha256"],
                    "artifact_kind": "provenance-bound-gate-result",
                },
            }
            for gate_id in core_ids
        ],
        "machine_qualification_claim_eligible": True,
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
    }
    report["report_sha256"] = _record_digest(report, field="report_sha256")
    _validate_schema(
        report,
        "commercial-evidence-report.v5.schema.json",
        label="commercial evidence report",
    )
    report_relative = "evidence/commercial-evidence-report.json"
    report_sha, _report_size = _write_json(
        output_root / report_relative,
        report,
        label="commercial evidence report",
    )
    if report_sha != _sha256((output_root / report_relative).read_bytes()):  # pragma: no cover
        _fail("commercial evidence report bytes changed while writing")

    retained = _record_by_kind(
        candidate_records,
        "retained_supply_chain",
        label="retained supply chain",
    )
    payload = retained.manifest.get("payload")
    if not isinstance(payload, Mapping):
        _fail("retained supply-chain payload is missing")
    pre_ref = payload.get("pre_publish_receipt_source")
    retained_ref = payload.get("retained_candidate_source")
    wheel_ref = payload.get("wheel_source")
    sdist_ref = payload.get("sdist_source")
    if not all(isinstance(item, Mapping) for item in (pre_ref, retained_ref, wheel_ref, sdist_ref)):
        _fail("retained supply-chain source bindings are incomplete")
    def source_path(ref: Mapping[str, Any], *, label: str) -> Path:
        relative = _safe_relative(ref.get("relative_path"), label=f"{label} source path")
        selected = bundle_root_resolved / relative
        _regular_file(selected, label=label)
        try:
            selected.resolve(strict=True).relative_to(output_root.resolve(strict=True))
        except ValueError as error:
            raise CommercialQualificationAssemblerError(
                f"{label} source is outside the assets root"
            ) from error
        return selected
    pre_path = source_path(pre_ref, label="pre-publish receipt")
    retained_manifest_path = source_path(retained_ref, label="retained artifact manifest")
    wheel_source_path = source_path(wheel_ref, label="retained wheel")
    sdist_source_path = source_path(sdist_ref, label="retained sdist")
    pre_publish, pre_publish_raw = _read_json(pre_path, label="pre-publish receipt")
    _validate_schema(
        pre_publish,
        "pre-publish-artifact-gate.v1.schema.json",
        label="pre-publish receipt",
    )
    retained_manifest, retained_manifest_raw = _read_json(
        retained_manifest_path,
        label="retained artifact manifest",
    )
    _validate_schema(
        retained_manifest,
        "retained-candidate-artifacts.v1.schema.json",
        label="retained artifact manifest",
    )
    wheel_name = retained_manifest.get("wheel", {}).get("filename")
    sdist_name = retained_manifest.get("sdist", {}).get("filename")
    if not isinstance(wheel_name, str) or not isinstance(sdist_name, str):
        _fail("retained artifact manifest lacks wheel/sdist identities")
    candidate_version = _candidate_version(wheel_name, suffix="wheel")
    if candidate_version != _candidate_version(sdist_name, suffix="sdist"):
        _fail("wheel and sdist versions differ")
    active_version = active.get("candidate_version")
    if active_version != candidate_version or not VERSION.fullmatch(str(candidate_version)):
        _fail("retained candidate version differs from active qualification")
    wheel_raw = wheel_source_path.read_bytes()
    sdist_raw = sdist_source_path.read_bytes()
    if (
        _sha256(wheel_raw) != active_candidate["wheel_sha256"]
        or _sha256(sdist_raw) != active_candidate["sdist_sha256"]
    ):
        _fail("retained artifact bytes differ from exact candidate hashes")
    pre_retained = pre_publish.get("retained_artifacts")
    if not isinstance(pre_retained, Mapping):
        _fail("pre-publish retained artifact binding is missing")
    for role, source_path_value, expected_hash in (
        ("wheel", wheel_source_path, active_candidate["wheel_sha256"]),
        ("sdist", sdist_source_path, active_candidate["sdist_sha256"]),
    ):
        receipt = pre_retained.get(role)
        if not isinstance(receipt, Mapping):
            _fail(f"pre-publish retained {role} binding is missing")
        if (
            receipt.get("sha256") != expected_hash
            or receipt.get("byte_size") != source_path_value.stat().st_size
        ):
            _fail(f"pre-publish retained {role} binding differs")
    manifest_sha = _sha256(retained_manifest_raw)
    if pre_retained.get("manifest_sha256") != manifest_sha:
        _fail("pre-publish retained manifest hash differs")
    active_binding = active.get("candidate_binding")
    if (
        not isinstance(active_binding, Mapping)
        or active_binding.get("artifact_manifest_sha256") != manifest_sha
    ):
        _fail("active candidate retained artifact manifest hash differs")
    pre_relative = (
        pre_path.resolve(strict=True)
        .relative_to(output_root.resolve(strict=True))
        .as_posix()
    )
    manifest_path_value = pre_retained.get("manifest_path")
    wheel_path_value = pre_retained["wheel"].get("retained_path")
    sdist_path_value = pre_retained["sdist"].get("retained_path")
    for relative, expected_hash, expected_size, label in (
        (manifest_path_value, manifest_sha, len(retained_manifest_raw), "retained manifest"),
        (wheel_path_value, active_candidate["wheel_sha256"], len(wheel_raw), "retained wheel"),
        (sdist_path_value, active_candidate["sdist_sha256"], len(sdist_raw), "retained sdist"),
    ):
        asset = _safe_asset(output_root, relative, label=label)
        raw = asset.read_bytes()
        if _sha256(raw) != expected_hash or len(raw) != expected_size:
            _fail(f"{label} path does not bind retained bytes")
    active_output = output_root / "evidence/active-qualification.json"
    if active_output.exists() or active_output.is_symlink():
        _fail("active qualification output must be new and non-symlink")
    active_output.parent.mkdir(parents=True, exist_ok=True)
    active_output.write_bytes(active_raw)
    if _regular_file(active_output, label="active qualification output") != active_raw:
        _fail("active qualification output differs from its frozen source bytes")
    manifest: dict[str, Any] = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "profile": PROFILE,
        "reference_provenance": REFERENCE_PROVENANCE,
        "human_authenticity": HUMAN_AUTHENTICITY,
        "environment": _environment(),
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": candidate_version,
            "tag": f"v{candidate_version}",
            "commit": active_candidate["commit"],
            "tree": active_candidate["tree"],
        },
        "run_ids": {
            "candidate_run_id": candidate_run_id,
            "evidence_run_id": evidence_run_id,
            "qualification_run_id": qualification_run_id,
        },
        "candidate_binding": {
            "commit": active_candidate["commit"],
            "tree": active_candidate["tree"],
            "lock_sha256": active_candidate["lock_sha256"],
            "wheel_sha256": active_candidate["wheel_sha256"],
            "sdist_sha256": active_candidate["sdist_sha256"],
            "version": candidate_version,
        },
        "artifact_binding": {
            "wheel": {
                "path": _safe_relative(wheel_path_value, label="release wheel path"),
                "sha256": active_candidate["wheel_sha256"],
                "byte_size": len(wheel_raw),
            },
            "sdist": {
                "path": _safe_relative(sdist_path_value, label="release sdist path"),
                "sha256": active_candidate["sdist_sha256"],
                "byte_size": len(sdist_raw),
            },
            "retained_manifest_sha256": manifest_sha,
        },
        "external_bindings": {
            "semantic_reference_sha256": expected_external["semantic_reference_sha256"],
            "machine_binding_sha256": expected_external["candidate_binding_sha256"],
            "holdout_sha256": expected_external["qualification_holdout_sha256"],
            "blind_sha256": expected_external["final_blind_holdout_sha256"],
            "agent_roster_sha256": expected_external["agent_roster_sha256"],
            "agent_consensus_sha256": expected_external["agent_consensus_sha256"],
            "agent_isolation_sha256": expected_external["agent_isolation_sha256"],
            "scorer_panel_sha256": expected_external["scorer_panel_sha256"],
            "arbiter_sha256": expected_external["arbiter_sha256"],
            "runner_sha256": expected_external["runner_sha256"],
            "isolation_sha256": expected_external["compiler_scorer_isolation_sha256"],
        },
        "pre_publish_artifact_gate": {
            "path": pre_relative,
            "receipt_sha256": _sha256(pre_publish_raw),
            "status": "pre_publish_passed",
        },
        "machine_evidence": {
            "report_path": report_relative,
            "report_sha256": report_sha,
            "record_sha256": report["report_sha256"],
            "status": "passed",
            "hard_zero": True,
            "core_gates_passed": True,
            "reference_binding": reference_binding,
        },
        "release_ready": True,
        "public_release_verified": False,
        "post_public_verification": None,
        "machine_qualification_claim_eligible": True,
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
    }
    manifest["record_sha256"] = _record_digest(manifest)
    _validate_schema(
        manifest,
        "commercial-release-manifest.v8.schema.json",
        label="commercial release manifest",
    )
    output_manifest = output_root / "commercial-release-manifest.json"
    _write_json(output_manifest, manifest, label="commercial release manifest")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--candidate-raw-root", type=Path, required=True)
    parser.add_argument("--active-qualification", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--candidate-run-id", type=int, required=True)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    parser.add_argument("--qualification-run-id", type=int, required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--candidate-tree", required=True)
    parser.add_argument("--lock-sha256", required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--sdist-sha256", required=True)
    parser.add_argument("--semantic-reference-sha256", required=True)
    parser.add_argument("--candidate-binding-sha256", required=True)
    parser.add_argument("--qualification-holdout-sha256", required=True)
    parser.add_argument("--final-blind-holdout-sha256", required=True)
    parser.add_argument("--agent-roster-sha256", required=True)
    parser.add_argument("--agent-consensus-sha256", required=True)
    parser.add_argument("--agent-isolation-sha256", required=True)
    parser.add_argument("--runner-sha256", required=True)
    parser.add_argument("--scorer-panel-sha256", required=True)
    parser.add_argument("--arbiter-sha256", required=True)
    parser.add_argument("--compiler-scorer-isolation-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = assemble_commercial_qualification(
            bundle_root=args.bundle_root,
            assets_root=args.assets_root,
            candidate_raw_root=args.candidate_raw_root,
            active_qualification=args.active_qualification,
            classification=args.classification,
            protocol=args.protocol,
            candidate_run_id=args.candidate_run_id,
            evidence_run_id=args.evidence_run_id,
            qualification_run_id=args.qualification_run_id,
            candidate={
                "commit": args.candidate_commit,
                "tree": args.candidate_tree,
                "lock_sha256": args.lock_sha256,
                "wheel_sha256": args.wheel_sha256,
                "sdist_sha256": args.sdist_sha256,
            },
            external_inputs={
                "semantic_reference_sha256": args.semantic_reference_sha256,
                "candidate_binding_sha256": args.candidate_binding_sha256,
                "qualification_holdout_sha256": args.qualification_holdout_sha256,
                "final_blind_holdout_sha256": args.final_blind_holdout_sha256,
                "agent_roster_sha256": args.agent_roster_sha256,
                "agent_consensus_sha256": args.agent_consensus_sha256,
                "agent_isolation_sha256": args.agent_isolation_sha256,
                "runner_sha256": args.runner_sha256,
                "scorer_panel_sha256": args.scorer_panel_sha256,
                "arbiter_sha256": args.arbiter_sha256,
                "compiler_scorer_isolation_sha256": args.compiler_scorer_isolation_sha256,
            },
        )
    except (OSError, CommercialQualificationAssemblerError, ValueError):
        print("commercial qualification v8 assembly failed", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CommercialQualificationAssemblerError",
    "assemble_commercial_qualification",
    "main",
]

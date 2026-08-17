"""Assemble the current v7 commercial qualification evidence boundary.

This module is deliberately a small, decision-free consumer of the external
qualification bundle.  It does not execute a Host, score a model, or accept a
caller supplied ``passed`` value.  Every Gate metric, failure count, run id,
and input binding is derived by :func:`parse_typed_evidence`; the resulting
Gate files and report are therefore suitable for the v7 provenance verifier to
reopen later.

The assembler writes only sanitized, path-relative records.  It never reads
credentials, transcripts, or model reasoning and has no network or Ledger side
effects.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.external_qualification_bundle_v3 import (
    ExternalQualificationBundleV3Error,
    validate_external_bundle,
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
from benchmarks.release.release_provenance_v7 import (
    _CANDIDATE_WORKFLOW_KINDS,
    _GATE_EVIDENCE_KINDS,
    _load_candidate_provenance_identities,
    _load_candidate_raw_inventory,
)
from benchmarks.release.typed_qualification_evidence import (
    TypedQualificationEvidenceError,
    parse_typed_evidence,
)

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACTS = REPOSITORY / "contracts"
TYPED_SCHEMA_VERSION = "deeplaw.typed-qualification-evidence/v1"
DERIVED_SCHEMA_VERSION = "deeplaw.typed-qualification-derived/v1"
GATE_SCHEMA_VERSION = "deeplaw.provenance-bound-gate-result/v3"
REPORT_SCHEMA_VERSION = "deeplaw.commercial-evidence-report/v4"
RELEASE_SCHEMA_VERSION = "deeplaw.commercial-release-manifest/v7"
CLASSIFICATION_SCHEMA_VERSION = "deeplaw.v013-release-gate-classification/v7"
CLASSIFICATION_ID = "deeplaw-v013-commercial-gates-v7"
VALIDATOR_ID = "deeplaw-typed-qualification-v1"
VALIDATOR_VERSION = "1"
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


class CommercialQualificationAssemblerError(ValueError):
    """Raised when current v7 qualification inputs are absent or inconsistent."""


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
    return _core_strict_json_bytes(
        raw,
        label=label,
        error_type=CommercialQualificationAssemblerError,
        projection=lambda value: _projection(value, label=label),
        require_object=True,
    )


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
    return _core_canonical_json_bytes(
        value,
        error_type=CommercialQualificationAssemblerError,
        message="qualification value is not canonical JSON",
    )


def _sha256(raw: bytes) -> str:
    return _core_sha256_bytes(raw)


def _record_digest(value: Mapping[str, Any], *, field: str = "record_sha256") -> str:
    return _core_digest_without(
        value,
        field=field,
        error_type=CommercialQualificationAssemblerError,
    )


def _derived_digest(value: Mapping[str, Any]) -> str:
    return _sha256(_canonical(value))


def _regular_file(path: Path, *, label: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    _resolved, raw = _core_regular_file_bytes(
        path,
        label=label,
        max_bytes=max_bytes,
        error_type=CommercialQualificationAssemblerError,
    )
    return raw


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _regular_file(path, label=label)
    return _strict_json(raw, label=label), raw


def _safe_relative(value: Any, *, label: str) -> str:
    return _core_safe_relative_posix(
        value,
        label=label,
        error_type=CommercialQualificationAssemblerError,
    )


def _safe_asset(root: Path, relative: Any, *, label: str) -> Path:
    return _core_safe_asset_file(
        root,
        relative,
        label=label,
        error_type=CommercialQualificationAssemblerError,
    )


def _safe_root(path: Path, *, label: str) -> Path:
    return _core_safe_root_directory(
        path,
        label=label,
        error_type=CommercialQualificationAssemblerError,
    )


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
        "v013-active-qualification.v1.schema.json",
        label="active qualification",
    )
    if value.get("status") != "frozen_exact_candidate":
        _fail("active qualification candidate is not frozen")
    return value, raw


def _load_classification(path: Path) -> tuple[dict[str, Any], bytes, list[str]]:
    value, raw = _read_json(path, label="v7 Gate classification")
    if value.get("schema_version") != CLASSIFICATION_SCHEMA_VERSION:
        _fail("Gate classification must use the current v7 schema")
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
        _fail("v7 Gate classification is not closed against the verifier mapping")
    for gate_id in core_ids:
        required_roles = gate_by_id[gate_id].get("required_corpus_roles")
        expected_roles = (
            ["candidate_full"]
            if _GATE_EVIDENCE_KINDS[gate_id] <= _CANDIDATE_WORKFLOW_KINDS
            else ["qualification_holdout", "final_blind"]
        )
        if required_roles != expected_roles:
            _fail(f"Gate {gate_id} corpus roles are not current v7")
    return value, raw, core_ids


def _trusted_approver(path: Path, *, bundle_root: Path) -> Mapping[str, Any]:
    selected = path.expanduser()
    if selected.is_symlink() or not selected.is_file():
        _fail("trusted human approver descriptor must be a regular file")
    try:
        resolved = selected.resolve(strict=True)
        bundle_resolved = bundle_root.resolve(strict=True)
    except OSError as error:
        raise CommercialQualificationAssemblerError(
            "trusted human approver descriptor is unavailable"
        ) from error
    if resolved.is_relative_to(bundle_resolved):
        _fail("trusted human approver descriptor must be outside the external bundle")
    value, _raw = _read_json(resolved, label="trusted human approver descriptor")
    if set(value) != {"identity", "key_id", "public_key_b64"}:
        _fail("trusted human approver descriptor is not closed")
    if not isinstance(value["identity"], str) or not value["identity"]:
        _fail("trusted human approver identity is invalid")
    if not isinstance(value["key_id"], str) or not SHA256.fullmatch(value["key_id"]):
        _fail("trusted human approver key id is invalid")
    if not isinstance(value["public_key_b64"], str) or not value["public_key_b64"]:
        _fail("trusted human approver public key is invalid")
    return value


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


def _threshold_binding(gold: Mapping[str, Any]) -> dict[str, Any]:
    thresholds = gold.get("thresholds")
    if not isinstance(thresholds, Mapping):
        _fail("semantic Gold thresholds are missing")
    return {
        "threshold_id": "semantic-gold-thresholds",
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
    external_scorer: Mapping[str, Any],
    candidate_identities: Mapping[str, Mapping[str, Mapping[str, str]]],
    trusted: Mapping[str, Any],
) -> list[_TypedRecord]:
    typed_kinds = set(_GATE_EVIDENCE_KINDS) | {"exact_wheel_execution", "human_gold_scorer"}
    records: list[_TypedRecord] = []
    for item in sorted(index.values(), key=lambda entry: entry.relative):
        if item.reference.get("evidence_kind") not in typed_kinds:
            continue
        envelope = _strict_json(item.raw, label="typed qualification manifest")
        if envelope.get("schema_version") != TYPED_SCHEMA_VERSION:
            _fail("typed bundle reference is not a typed qualification manifest")
        kind = envelope.get("kind")
        if not isinstance(kind, str) or kind != item.reference.get("evidence_kind"):
            _fail("typed qualification evidence kind differs from bundle reference")
        run_binding = envelope.get("run_binding")
        corpus = envelope.get("corpus")
        if not isinstance(run_binding, Mapping) or not isinstance(corpus, Mapping):
            _fail("typed qualification run/corpus binding is missing")
        is_candidate = kind in _CANDIDATE_WORKFLOW_KINDS
        run_id = run_binding.get("run_id")
        workflow_run_id = candidate_run_id if is_candidate else evidence_run_id
        if not isinstance(run_id, str) or not run_id:
            _fail("typed qualification run id is invalid")
        corpus_role = corpus.get("role")
        corpus_sha = corpus.get("sha256")
        if not isinstance(corpus_sha, str) or not SHA256.fullmatch(corpus_sha):
            _fail("typed qualification corpus hash is invalid")
        if is_candidate:
            if corpus_role != "candidate_full":
                _fail("Candidate Full typed evidence has the wrong corpus role")
            expected_corpus = None
            identity = candidate_identities.get(kind)
            if not isinstance(identity, Mapping):
                _fail(f"Candidate Full provenance identity is missing for {kind}")
            expected_runner = identity.get("runner")
            expected_scorer = identity.get("scorer")
        else:
            if corpus_role == "qualification_holdout":
                expected_corpus = holdout_sha256
            elif corpus_role == "final_blind":
                expected_corpus = blind_sha256
            else:
                _fail("external typed evidence has an unsupported corpus role")
            expected_runner = external_runner
            expected_scorer = external_scorer
        if corpus_sha != expected_corpus and expected_corpus is not None:
            _fail("typed qualification corpus hash differs from its frozen input")
        try:
            derived = parse_typed_evidence(
                item.path,
                root=item.path.parent,
                expected_candidate=candidate,
                expected_run_id=run_id,
                expected_workflow_run_id=workflow_run_id,
                expected_corpus_sha256=expected_corpus,
                expected_runner=expected_runner,
                expected_scorer=expected_scorer,
                trusted_human_approver=trusted,
            )
        except (OSError, TypedQualificationEvidenceError, ValueError) as error:
            raise CommercialQualificationAssemblerError(
                "typed qualification parser rejected a bundle manifest"
            ) from error
        if derived.get("kind") != kind or derived.get("status") != "passed":
            _fail("typed qualification evidence did not derive a passed result")
        if derived.get("evidence_record_sha256") != envelope.get("record_sha256"):
            _fail("typed qualification derived record is not bound to its manifest")
        sources = _typed_source_refs(envelope.get("payload"))
        if not sources:
            _fail("typed qualification manifest contains no source receipts")
        for source in sources:
            source_path = source.get("relative_path")
            _safe_relative(source_path, label="typed source path")
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
    selected = holdout or blind
    role = "qualification_holdout" if holdout else "final_blind"
    corpus_sha = holdout_sha256 if holdout else blind_sha256
    if not selected:
        _fail("external typed evidence has no frozen holdout")
    if any(item.manifest.get("corpus", {}).get("sha256") != corpus_sha for item in selected):
        _fail("selected external typed evidence does not share one corpus hash")
    # A v7 report has one corpus binding.  Do not silently combine holdout and blind rows.
    expected_counts = {
        "host_event_sequence": 6,
        "exact_wheel_execution": 1,
        "human_gold_scorer": 1,
        "legal_rows": 1,
        "wiki_journey_rows": 1,
        "context_capsule_selection_usage": 1,
        "scale_report": 1,
    }
    by_kind: dict[str, list[_TypedRecord]] = defaultdict(list)
    for item in selected:
        by_kind[item.kind].append(item)
    for kind, count in expected_counts.items():
        if len(by_kind[kind]) != count:
            _fail(f"external typed evidence requires {count} {kind} manifests")
    hosts: dict[str, list[_TypedRecord]] = defaultdict(list)
    for item in by_kind["host_event_sequence"]:
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
    return selected, role, corpus_sha


def _validator_binding(path: Path, *, label: str) -> dict[str, Any]:
    raw = _regular_file(path, label=label)
    return {
        "relative_path": path.resolve(strict=True).relative_to(REPOSITORY).as_posix(),
        "byte_size": len(raw),
        "file_sha256": _sha256(raw),
    }


def _execution_schema_fields() -> set[str]:
    schema_path = CONTRACTS / "provenance-bound-gate-result.v3.schema.json"
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
        _fail("Gate execution contract is not the converged v3 contract")
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
    gold_binding: Mapping[str, Any],
    corpus: Mapping[str, Any],
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
        "gold_binding": dict(gold_binding),
        "corpus": dict(corpus),
        "status": "passed",
        "executions": executions,
        "run_ids": run_ids,
        "metrics": metrics,
        "hard_failures": hard_failures,
        "inputs": inputs,
    }
    result["result_sha256"] = _record_digest(result, field="result_sha256")
    _validate_schema(result, "provenance-bound-gate-result.v3.schema.json", label=f"Gate {gate_id}")
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
    semantic_gold_sha256: str,
    holdout_sha256: str,
    blind_sha256: str,
    scorer_sha256: str,
    runner_sha256: str,
    isolation_sha256: str,
) -> dict[str, str]:
    result = {
        "semantic_gold_sha256": semantic_gold_sha256,
        "holdout_sha256": holdout_sha256,
        "blind_sha256": blind_sha256,
        "scorer_sha256": scorer_sha256,
        "runner_sha256": runner_sha256,
        "isolation_sha256": isolation_sha256,
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
    trusted_human_approver: Path | str,
    protocol: Path | str,
    candidate_run_id: int,
    evidence_run_id: int,
    qualification_run_id: int,
    candidate: Mapping[str, str],
    external_inputs: Mapping[str, str],
) -> dict[str, Any]:
    """Validate typed evidence and write a v7 report, Gates, and release manifest.

    ``candidate`` must contain exactly ``commit``, ``tree``, ``lock_sha256``,
    ``wheel_sha256``, and ``sdist_sha256``.  ``external_inputs`` must contain
    exactly the six hash fields used by the v7 release manifest.  No status or
    metric field is accepted from either mapping.
    """

    candidate_keys = {"commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"}
    external_keys = {
        "semantic_gold_sha256",
        "holdout_sha256",
        "blind_sha256",
        "scorer_sha256",
        "runner_sha256",
        "isolation_sha256",
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
    expected_external = _external_binding(**{
        "semantic_gold_sha256": external_inputs["semantic_gold_sha256"],
        "holdout_sha256": external_inputs["holdout_sha256"],
        "blind_sha256": external_inputs["blind_sha256"],
        "scorer_sha256": external_inputs["scorer_sha256"],
        "runner_sha256": external_inputs["runner_sha256"],
        "isolation_sha256": external_inputs["isolation_sha256"],
    })
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (candidate_run_id, evidence_run_id, qualification_run_id)
    ):
        _fail("workflow run ids must be positive integers")

    source_bundle = _safe_root(Path(bundle_root), label="external bundle root")
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
    trusted = _trusted_approver(
        Path(trusted_human_approver).expanduser(),
        bundle_root=bundle_root_resolved,
    )
    try:
        bundle_validation = validate_external_bundle(
            bundle_root_resolved,
            active_qualification=Path(active_qualification).expanduser(),
            trusted_human_approver=Path(trusted_human_approver).expanduser(),
            expected_candidate_run_id=candidate_run_id,
            expected_evidence_run_id=evidence_run_id,
        )
    except (OSError, ExternalQualificationBundleV3Error, ValueError) as error:
        raise CommercialQualificationAssemblerError(
            "external qualification bundle v3 boundary rejected"
        ) from error
    if bundle_validation.get("candidate_run_id") != candidate_run_id:
        _fail("external bundle candidate run id differs")
    if bundle_validation.get("evidence_run_id") != evidence_run_id:
        _fail("external bundle evidence run id differs")
    manifest, _manifest_raw = _read_json(
        bundle_root_resolved / "bundle-manifest.json",
        label="external bundle manifest",
    )
    index = _bundle_index(bundle_root_resolved, manifest)
    active_candidate = _candidate_from_active(active)
    manifest_candidate = manifest.get("candidate_binding")
    if not isinstance(manifest_candidate, Mapping):
        _fail("external bundle candidate binding is missing")
    _assert_exact_bindings(
        candidate=active_candidate,
        expected_candidate=expected_candidate,
        external=manifest.get("external_inputs", {}),
        expected_external={
            "semantic_gold_sha256": expected_external["semantic_gold_sha256"],
            "candidate_gold_binding_sha256": manifest["external_inputs"].get(
                "candidate_gold_binding_sha256"
            ),
            "qualification_holdout_sha256": expected_external["holdout_sha256"],
            "final_blind_holdout_sha256": expected_external["blind_sha256"],
            "runner_sha256": expected_external["runner_sha256"],
            "scorer_sha256": expected_external["scorer_sha256"],
            "compiler_scorer_isolation_sha256": expected_external["isolation_sha256"],
        },
        candidate_run_id=candidate_run_id,
        evidence_run_id=evidence_run_id,
        qualification_run_id=qualification_run_id,
    )
    if dict(manifest_candidate) != active_candidate:
        _fail("external bundle candidate binding differs from the exact candidate")
    # Reopen the independent Candidate Full inventory before consuming any candidate typed source.
    try:
        _load_candidate_raw_inventory(
            Path(candidate_raw_root).expanduser(),
            candidate_run_id=candidate_run_id,
            candidate_commit=active_candidate["commit"],
            external_bundle_root=bundle_root_resolved,
        )
    except Exception as error:
        raise CommercialQualificationAssemblerError(
            "Candidate Full raw inventory rejected"
        ) from error

    candidate_gold_file = _find_bundle_file(
        index,
        digest=expected_external["semantic_gold_sha256"],
        evidence_kind="human_gold_scorer",
        label="semantic Human Gold",
    )
    semantic_gold = _strict_json(candidate_gold_file.raw, label="semantic Human Gold")
    _validate_schema(
        semantic_gold,
        "semantic-human-gold.v3.schema.json",
        label="semantic Human Gold",
    )
    threshold_binding = _threshold_binding(semantic_gold)
    candidate_gold_binding_file = _find_bundle_file(
        index,
        digest=manifest["external_inputs"]["candidate_gold_binding_sha256"],
        evidence_kind="post_build_gold_binding",
        label="Candidate Gold binding",
    )
    candidate_gold_binding = _strict_json(
        candidate_gold_binding_file.raw,
        label="Candidate Gold binding",
    )
    _validate_schema(
        candidate_gold_binding,
        "candidate-gold-binding-receipt.v1.schema.json",
        label="Candidate Gold binding",
    )
    if candidate_gold_binding.get("record_sha256") != _record_digest(candidate_gold_binding):
        _fail("Candidate Gold binding record digest differs")
    external_runner = candidate_gold_binding.get("runner")
    external_scorer = candidate_gold_binding.get("scorer")
    if not isinstance(external_runner, Mapping) or not isinstance(external_scorer, Mapping):
        _fail("Candidate Gold runner/scorer identities are missing")
    if external_runner.get("sha256") != expected_external["runner_sha256"]:
        _fail("Candidate Gold runner hash differs")
    if external_scorer.get("sha256") != expected_external["scorer_sha256"]:
        _fail("Candidate Gold scorer hash differs")
    candidate_identities = _load_candidate_provenance_identities()
    records = _parse_typed_records(
        bundle_root=bundle_root_resolved,
        index=index,
        candidate=active_candidate,
        candidate_run_id=candidate_run_id,
        evidence_run_id=evidence_run_id,
        holdout_sha256=expected_external["holdout_sha256"],
        blind_sha256=expected_external["blind_sha256"],
        external_runner=external_runner,
        external_scorer=external_scorer,
        candidate_identities=candidate_identities,
        trusted=trusted,
    )
    selected_external, external_role, external_corpus_sha256 = _select_records(
        records,
        holdout_sha256=expected_external["holdout_sha256"],
        blind_sha256=expected_external["blind_sha256"],
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
    protocol_sha256 = _sha256(protocol_raw)
    if protocol_sha256 != protocol_binding_active.get("sha256"):
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
    gold_binding = {
        "gold_sha256": expected_external["semantic_gold_sha256"],
        "role": "qualification_gold",
        "source": "repository_external",
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

    # Closed source-kind mapping shared with release_provenance_v7.
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
        "human_gold_isolation": [
            _record_by_kind(selected_external, "human_gold_scorer", label="human_gold_isolation")
        ],
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
        _fail("assembler Gate mapping is not the closed v7 14-Gate mapping")
    gate_results: dict[str, dict[str, Any]] = {}
    gate_paths: dict[str, tuple[str, int, str]] = {}
    for gate_id in core_ids:
        domain = (
            candidate_corpus
            if all(item.kind in _CANDIDATE_WORKFLOW_KINDS for item in gate_records[gate_id])
            else external_corpus
        )
        gate = _gate_result(
            gate_id,
            gate_records[gate_id],
            assets_root=output_root,
            qualification_run_id=qualification_run_id,
            candidate=active_candidate,
            classification_binding=classification_binding,
            protocol_binding=protocol_binding,
            threshold_binding=threshold_binding,
            gold_binding=gold_binding,
            corpus=domain,
            validator_source=validator_source,
            validator_executable=validator_executable,
        )
        relative = f"evidence/gate-results/{gate_id}.json"
        file_sha, size = _write_json(output_root / relative, gate, label=f"Gate {gate_id}")
        gate_results[gate_id] = gate
        gate_paths[gate_id] = (relative, size, file_sha)

    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_kind": "v013_provenance_bound_gate_collection",
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
        "gold_binding": gold_binding,
        "corpus": external_corpus,
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
    }
    report["report_sha256"] = _record_digest(report, field="report_sha256")
    _validate_schema(
        report,
        "commercial-evidence-report.v4.schema.json",
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
        selected = retained.path.parent / relative
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
    active_output_sha, _active_output_size = _write_json(
        active_output,
        active,
        label="active qualification",
    )
    if active_output_sha != _sha256(active_raw + b"\n"):
        # The source may omit a trailing newline; the active file's exact bytes remain bound by
        # its own supplied hash in the workflow.  This comparison is intentionally informational.
        pass
    manifest: dict[str, Any] = {
        "schema_version": RELEASE_SCHEMA_VERSION,
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
            "semantic_gold_sha256": expected_external["semantic_gold_sha256"],
            "holdout_sha256": expected_external["holdout_sha256"],
            "blind_sha256": expected_external["blind_sha256"],
            "scorer_sha256": expected_external["scorer_sha256"],
            "runner_sha256": expected_external["runner_sha256"],
            "isolation_sha256": expected_external["isolation_sha256"],
        },
        "pre_publish_artifact_gate": {
            "path": pre_relative,
            "receipt_sha256": _sha256(pre_publish_raw),
            "status": "pre_publish_passed",
        },
        "semantic_evidence": {
            "report_path": report_relative,
            "report_sha256": report_sha,
            "record_sha256": report["report_sha256"],
            "status": "passed",
            "hard_zero": True,
            "core_gates_passed": True,
        },
        "release_ready": True,
        "public_release_verified": False,
        "post_public_verification": None,
        "claim_eligible": True,
        "commercial_release_eligible": True,
        "quality_protocol_eligible": True,
        "competitive_claim_eligible": False,
    }
    manifest["record_sha256"] = _record_digest(manifest)
    _validate_schema(
        manifest,
        "commercial-release-manifest.v7.schema.json",
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
    parser.add_argument("--trusted-human-approver", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--candidate-run-id", type=int, required=True)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    parser.add_argument("--qualification-run-id", type=int, required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--candidate-tree", required=True)
    parser.add_argument("--lock-sha256", required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--sdist-sha256", required=True)
    parser.add_argument("--semantic-gold-sha256", required=True)
    parser.add_argument("--qualification-holdout-sha256", required=True)
    parser.add_argument("--final-blind-sha256", required=True)
    parser.add_argument("--scorer-sha256", required=True)
    parser.add_argument("--runner-sha256", required=True)
    parser.add_argument("--isolation-sha256", required=True)
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
            trusted_human_approver=args.trusted_human_approver,
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
                "semantic_gold_sha256": args.semantic_gold_sha256,
                "holdout_sha256": args.qualification_holdout_sha256,
                "blind_sha256": args.final_blind_sha256,
                "scorer_sha256": args.scorer_sha256,
                "runner_sha256": args.runner_sha256,
                "isolation_sha256": args.isolation_sha256,
            },
        )
    except (OSError, CommercialQualificationAssemblerError, ValueError):
        print("commercial qualification v7 assembly failed", file=sys.stderr)
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

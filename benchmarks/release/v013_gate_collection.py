"""Assemble and revalidate the complete v0.13 Core Gate collection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.release.v013_gate_validator import (
    ACTIVE_QUALIFICATION_PATH,
    CLASSIFICATION_PATH,
    GateValidationError,
    canonical_json,
    record_sha256,
    validate_gate,
)

REPOSITORY = Path(__file__).resolve().parents[2]
COLLECTION_SCHEMA = REPOSITORY / "contracts/v013-gate-collection.v1.schema.json"
RESULT_SCHEMA = REPOSITORY / "contracts/v013-gate-result.v1.schema.json"
MAX_FILE_BYTES = 64 * 1024 * 1024


class GateCollectionError(ValueError):
    """Raised when a Gate collection is incomplete or not reproducible."""


def _collection_sha256(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "report_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise GateCollectionError("Gate collection input must be a regular file")
    raw = path.read_bytes()
    if not 1 <= len(raw) <= MAX_FILE_BYTES:
        raise GateCollectionError("Gate collection input exceeds its byte bound")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise GateCollectionError("Gate collection input must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise GateCollectionError("Gate collection input must be a JSON object")
    return value, raw


def _schema(path: Path) -> dict[str, Any]:
    value, _raw = _load(path)
    Draft202012Validator.check_schema(value)
    return value


def _validate_schema(value: Mapping[str, Any], path: Path, *, label: str) -> None:
    errors = sorted(
        Draft202012Validator(_schema(path)).iter_errors(value),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        raise GateCollectionError(f"{label} schema violation at {location}: {errors[0].message}")


def _safe_file(root: Path, relative_path: str) -> Path:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise GateCollectionError("Gate collection contains an unsafe path")
    selected_root = root.expanduser().resolve(strict=True)
    selected = selected_root.joinpath(*path.parts)
    if selected.is_symlink() or not selected.is_file():
        raise GateCollectionError("Gate collection references a non-regular file")
    try:
        selected.resolve(strict=True).relative_to(selected_root)
    except ValueError:
        raise GateCollectionError("Gate collection file escapes the evidence root") from None
    return selected


def _active_candidate(active: Mapping[str, Any]) -> dict[str, Any]:
    candidate = active["candidate_binding"]
    return {
        "commit": candidate["source_commit"],
        "tree": candidate["source_tree"],
        "lock_sha256": candidate["lock_sha256"],
        "wheel_sha256": candidate["wheel_sha256"],
        "sdist_sha256": candidate["sdist_sha256"],
    }


def validate_collection(
    value: Mapping[str, Any] | Path,
    *,
    root: Path,
    active_path: Path = ACTIVE_QUALIFICATION_PATH,
    classification_path: Path = CLASSIFICATION_PATH,
    expected_evidence_run_id: int | None = None,
) -> dict[str, Any]:
    """Re-run every Core validator and derive the release Gate disposition."""

    if isinstance(value, Path):
        document, _raw = _load(value)
    elif isinstance(value, Mapping):
        document = dict(value)
    else:
        raise GateCollectionError("Gate collection must be a mapping or path")
    _validate_schema(document, COLLECTION_SCHEMA, label="Gate collection")
    if document["report_sha256"] != _collection_sha256(document):
        raise GateCollectionError("Gate collection record digest differs")
    active, active_raw = _load(active_path)
    classification, classification_raw = _load(classification_path)
    if active.get("status") != "frozen_exact_candidate":
        raise GateCollectionError("active qualification candidate is not frozen")
    if document["candidate_version"] != active["candidate_version"] or document[
        "candidate_binding"
    ] != _active_candidate(active):
        raise GateCollectionError("Gate collection candidate differs from active qualification")
    if document["active_qualification_sha256"] != hashlib.sha256(active_raw).hexdigest():
        raise GateCollectionError("Gate collection active qualification hash differs")
    if document["classification_sha256"] != hashlib.sha256(classification_raw).hexdigest():
        raise GateCollectionError("Gate collection classification hash differs")
    definitions = {gate["gate_id"]: gate for gate in classification["gates"]}
    required_core = {
        gate_id
        for gate_id, definition in definitions.items()
        if definition["category"] == "Core"
    }
    references = {item["gate_id"]: item for item in document["gate_results"]}
    if len(references) != len(document["gate_results"]) or set(references) != required_core:
        raise GateCollectionError("Gate collection does not contain exactly every Core Gate")

    statuses: dict[str, str] = {}
    hard_zero = True
    for gate_id in sorted(required_core):
        reference = references[gate_id]
        result_path = _safe_file(root, reference["relative_path"])
        result, raw = _load(result_path)
        _validate_schema(result, RESULT_SCHEMA, label=f"Gate Result {gate_id}")
        if (
            reference["byte_size"] != len(raw)
            or reference["file_sha256"] != hashlib.sha256(raw).hexdigest()
            or reference["record_sha256"] != result["record_sha256"]
            or reference["gate_id"] != result["gate_id"]
            or reference["category"] != result["category"]
            or reference["status"] != result["status"]
            or result["record_sha256"] != record_sha256(result)
        ):
            raise GateCollectionError(f"Gate Result binding differs for {gate_id}")
        raw_paths = [_safe_file(root, item["relative_path"]) for item in result["raw_inputs"]]
        try:
            reproduced = validate_gate(
                gate_id,
                raw_paths,
                root=root,
                active_path=active_path,
                classification_path=classification_path,
                expected_evidence_run_id=expected_evidence_run_id,
            )
        except GateValidationError as error:
            raise GateCollectionError(
                f"Gate Result did not reproduce for {gate_id}: {error}"
            ) from error
        if canonical_json(reproduced) != canonical_json(result):
            raise GateCollectionError(f"Gate Result bytes did not reproduce for {gate_id}")
        statuses[gate_id] = result["status"]
        hard_zero = hard_zero and all(item["count"] == 0 for item in result["hard_failures"])
    for gate_id, definition in definitions.items():
        if definition["category"] != "Core":
            statuses[gate_id] = "not_claimed"
    release_ready = all(statuses[gate_id] == "passed" for gate_id in required_core) and hard_zero
    return {
        "schema_version": "deeplaw.v013-gate-collection-validation/v1",
        "status": "passed" if release_ready else "failed",
        "hard_zero": hard_zero,
        "release_ready": release_ready,
        "claim_eligible": release_ready,
        "competitive_claim_eligible": False,
        "gate_statuses": dict(sorted(statuses.items())),
        "candidate_version": document["candidate_version"],
    }


def build_collection(
    result_paths: Sequence[Path],
    *,
    root: Path,
    report_id: str,
    active_path: Path = ACTIVE_QUALIFICATION_PATH,
    classification_path: Path = CLASSIFICATION_PATH,
    expected_evidence_run_id: int | None = None,
) -> dict[str, Any]:
    """Build a decision-free reference collection, then fully revalidate it."""

    active, active_raw = _load(active_path)
    _classification, classification_raw = _load(classification_path)
    references: list[dict[str, Any]] = []
    for path in result_paths:
        result, raw = _load(path)
        _validate_schema(result, RESULT_SCHEMA, label="Gate Result")
        relative = path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
        references.append(
            {
                "gate_id": result["gate_id"],
                "category": result["category"],
                "relative_path": relative,
                "byte_size": len(raw),
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "record_sha256": result["record_sha256"],
                "status": result["status"],
            }
        )
    document: dict[str, Any] = {
        "schema_version": "deeplaw.v013-gate-collection/v1",
        "report_kind": "v013_provenance_bound_gate_collection",
        "report_id": report_id,
        "candidate_version": active["candidate_version"],
        "candidate_binding": _active_candidate(active),
        "active_qualification_sha256": hashlib.sha256(active_raw).hexdigest(),
        "classification_sha256": hashlib.sha256(classification_raw).hexdigest(),
        "gate_results": sorted(references, key=lambda item: item["gate_id"]),
    }
    document["report_sha256"] = _collection_sha256(document)
    validate_collection(
        document,
        root=root,
        active_path=active_path,
        classification_path=classification_path,
        expected_evidence_run_id=expected_evidence_run_id,
    )
    return document


__all__ = ["GateCollectionError", "build_collection", "validate_collection"]

"""Deterministic, candidate-only validation for provenance-bound Gate Results.

This module validates one ``provenance-bound-gate-result/v2`` envelope.  It is deliberately
not a release assembler and does not consume the commercial evidence collection contract.
Every check is local and fail-closed: referenced files are reopened, their bytes and record
digests are recomputed, and all derived run/dimension/reference fields are checked against the
raw envelope.  No validator result is evidence of a real gate until a dedicated Core validator
and an enabled consumer exist.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

SCHEMA_VERSION = "deeplaw.provenance-bound-gate-result/v2"
SCHEMA_FILENAME = "provenance-bound-gate-result.v2.schema.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_SOURCE_RELATIVE_PATH = "benchmarks/release/provenance_gate_result.py"
MAX_FILE_BYTES = 64 * 1024 * 1024


class ProvenanceGateResultError(ValueError):
    """Raised when a Gate Result envelope or one of its bound files is invalid."""


def canonical_json(value: Any) -> str:
    """Return the deterministic JSON representation used by all envelope digests."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_digest(value: Mapping[str, Any], *, excluded_field: str) -> str:
    body = {key: item for key, item in value.items() if key != excluded_field}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def result_sha256(value: Mapping[str, Any]) -> str:
    """Compute the envelope digest without trusting its supplied ``result_sha256``."""

    return canonical_digest(value, excluded_field="result_sha256")


def _reject_nonfinite(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _strict_json(raw: bytes, *, field: str) -> dict[str, Any]:
    if not 1 <= len(raw) <= MAX_FILE_BYTES:
        raise ProvenanceGateResultError(f"{field} violates its bounded byte size")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ProvenanceGateResultError(f"{field} must contain strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ProvenanceGateResultError(f"{field} must contain a JSON object")
    return value


def _load_schema() -> dict[str, Any]:
    path = DEFAULT_ROOT / "contracts" / SCHEMA_FILENAME
    try:
        value = _strict_json(path.read_bytes(), field="Gate Result schema")
        Draft202012Validator.check_schema(value)
    except (OSError, ProvenanceGateResultError, SchemaError) as error:
        raise ProvenanceGateResultError("Gate Result schema is unavailable or invalid") from error
    return value


def _validate_schema(value: Mapping[str, Any]) -> None:
    schema = _load_schema()
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: list(error.path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "$"
        raise ProvenanceGateResultError(
            f"Gate Result schema violation at {location}: {first.message}"
        )


def _finite_number(value: Any, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProvenanceGateResultError(f"{field} must be numeric")
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError):
        finite = False
    if not finite:
        raise ProvenanceGateResultError(f"{field} must be finite")


def _safe_relative_path(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProvenanceGateResultError(f"{field} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ProvenanceGateResultError(f"{field} must be a relative POSIX path")
    return path


def _bound_file(
    binding: Mapping[str, Any],
    *,
    root: Path,
    field: str,
) -> Path:
    relative = _safe_relative_path(binding.get("relative_path"), field=f"{field}.relative_path")
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise ProvenanceGateResultError(f"{field} must bind a regular file")
    try:
        size = candidate.stat().st_size
    except OSError as error:
        raise ProvenanceGateResultError(f"{field} cannot be read within the root") from error
    if not 1 <= size <= MAX_FILE_BYTES:
        raise ProvenanceGateResultError(f"{field} violates its bounded byte size")
    try:
        selected = candidate.resolve(strict=True)
        selected.relative_to(root)
        raw = selected.read_bytes()
    except (OSError, ValueError) as error:
        raise ProvenanceGateResultError(f"{field} cannot be read within the root") from error
    expected_size = binding.get("byte_size")
    expected_sha = binding.get("file_sha256")
    if expected_size != len(raw) or expected_sha != hashlib.sha256(raw).hexdigest():
        raise ProvenanceGateResultError(f"{field} byte binding does not match the file")
    return selected


def _input_record_digest(value: Mapping[str, Any]) -> str:
    return canonical_digest(value, excluded_field="record_sha256")


def _validate_inputs(
    result: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Mapping[str, Any]]:
    inputs = result["inputs"]
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(inputs):
        if not isinstance(item, Mapping):  # schema validation normally catches this first
            raise ProvenanceGateResultError(f"inputs[{index}] is not an object")
        input_id = item["input_id"]
        if input_id in by_id:
            raise ProvenanceGateResultError("input_id values must be unique")
        path = _bound_file(item, root=root, field=f"inputs[{index}]")
        try:
            document = _strict_json(path.read_bytes(), field=f"input {input_id}")
        except OSError as error:
            raise ProvenanceGateResultError("input file cannot be reopened") from error
        if document.get("schema_version") != item["schema_version"]:
            raise ProvenanceGateResultError(f"input {input_id} schema version differs")
        if document.get("artifact_kind") != item["artifact_kind"]:
            raise ProvenanceGateResultError(f"input {input_id} artifact kind differs")
        declared_record_digest = document.get("record_sha256")
        if not isinstance(declared_record_digest, str):
            raise ProvenanceGateResultError(f"input {input_id} has no record digest")
        expected_record_digest = _input_record_digest(document)
        if (
            declared_record_digest != expected_record_digest
            or item["record_sha256"] != expected_record_digest
        ):
            raise ProvenanceGateResultError(f"input {input_id} record digest differs")
        by_id[input_id] = item
    return by_id


def _input_refs(result: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for collection_name in ("executions", "metrics", "hard_failures", "failures"):
        for item in result[collection_name]:
            refs.extend(item["input_refs"])
    refs.extend(result["redaction"]["input_refs"])
    return refs


def _validate_reference_closure(result: Mapping[str, Any], inputs: Mapping[str, Any]) -> None:
    refs = _input_refs(result)
    unknown = set(refs) - set(inputs)
    if unknown:
        raise ProvenanceGateResultError("an input reference is not declared")
    if set(refs) != set(inputs):
        raise ProvenanceGateResultError("every declared input must be consumed by a reference")


def _validate_runs_and_dimensions(result: Mapping[str, Any]) -> None:
    executions = result["executions"]
    execution_run_ids = [execution["run_id"] for execution in executions]
    if len(set(execution_run_ids)) != len(execution_run_ids):
        raise ProvenanceGateResultError("execution run_id values must be unique")
    if result["run_ids"] != sorted(execution_run_ids):
        raise ProvenanceGateResultError("run_ids must be the sorted execution run_id derivation")

    derived: dict[str, set[str]] = (
        {"run_id": set(execution_run_ids)} if execution_run_ids else {}
    )
    for index, execution in enumerate(executions):
        dimensions = execution["dimensions"]
        for name, value in dimensions.items():
            if name == "run_id" and value != execution["run_id"]:
                raise ProvenanceGateResultError(
                    f"executions[{index}].dimensions.run_id differs from run_id"
                )
            derived.setdefault(name, set()).add(value)
    expected_dimensions = [
        {"dimension": name, "values": sorted(values)}
        for name, values in sorted(derived.items())
    ]
    if result["unique_dimensions"] != expected_dimensions:
        raise ProvenanceGateResultError(
            "unique_dimensions must be the deterministic execution dimension derivation"
        )


def _validate_metrics(result: Mapping[str, Any]) -> None:
    for index, metric in enumerate(result["metrics"]):
        for field in ("observed", "minimum", "maximum"):
            value = metric[field]
            if value is not None:
                _finite_number(value, field=f"metrics[{index}].{field}")


def validate_gate_result(
    value: Mapping[str, Any] | str | Path,
    *,
    root: Path | str | None = None,
    expected_validator_id: str | None = None,
    expected_validator_version: str | None = None,
    expected_source_path: str | None = None,
    expected_executable_path: str | None = None,
) -> dict[str, Any]:
    """Validate and return one provenance-bound Gate Result envelope.

    ``root`` is the only filesystem authority.  All input, validator-source and
    validator-executable paths must remain regular files beneath that root.  Optional
    expected identity/path arguments let an owner bind a result to a particular validator;
    the generic candidate-only envelope validator does not infer those values from a hash.
    """

    selected_root = Path(root or DEFAULT_ROOT).expanduser().resolve()
    if isinstance(value, (str, Path)):
        result_path = Path(value)
        if result_path.is_symlink() or not result_path.is_file():
            raise ProvenanceGateResultError("Gate Result must be a regular file")
        try:
            document = _strict_json(result_path.read_bytes(), field="Gate Result")
        except OSError as error:
            raise ProvenanceGateResultError("Gate Result cannot be read") from error
    elif isinstance(value, Mapping):
        document = dict(value)
    else:
        raise ProvenanceGateResultError("Gate Result must be a mapping or JSON path")

    _validate_schema(document)
    if document["schema_version"] != SCHEMA_VERSION:
        raise ProvenanceGateResultError("Gate Result schema version is not v1")
    if expected_validator_id is not None and document["validator_id"] != expected_validator_id:
        raise ProvenanceGateResultError("validator_id does not match the expected validator")
    if (
        expected_validator_version is not None
        and document["validator_version"] != expected_validator_version
    ):
        raise ProvenanceGateResultError("validator_version does not match the expected validator")
    if document["result_sha256"] != result_sha256(document):
        raise ProvenanceGateResultError("result_sha256 does not match the canonical envelope")

    if selected_root == DEFAULT_ROOT and expected_source_path is None:
        expected_source_path = VALIDATOR_SOURCE_RELATIVE_PATH

    source_path = _bound_file(
        document["validator_source"],
        root=selected_root,
        field="validator_source",
    )
    executable_path = _bound_file(
        document["validator_executable"],
        root=selected_root,
        field="validator_executable",
    )
    if source_path == executable_path:
        raise ProvenanceGateResultError("validator source and executable must be distinct files")
    if (
        expected_source_path is not None
        and document["validator_source"]["relative_path"] != expected_source_path
    ):
        raise ProvenanceGateResultError("validator source path differs from the expected path")
    if (
        expected_executable_path is not None
        and document["validator_executable"]["relative_path"] != expected_executable_path
    ):
        raise ProvenanceGateResultError(
            "validator executable path differs from the expected path"
        )

    inputs = _validate_inputs(document, root=selected_root)
    _validate_reference_closure(document, inputs)
    _validate_runs_and_dimensions(document)
    _validate_metrics(document)
    if any(
        binding["frozen"] is not True
        for binding in (
            document["protocol_binding"],
            document["threshold_binding"],
            document["gold_binding"],
            document["corpus"],
        )
    ):
        raise ProvenanceGateResultError("protocol, threshold, gold and corpus must be frozen")
    return document


def validate_gate_result_file(
    path: Path | str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Validate a Gate Result file, using its parent as the default input root."""

    selected_path = Path(path)
    kwargs.setdefault("root", selected_path.parent)
    return validate_gate_result(selected_path, **kwargs)


# Explicit aliases keep the seam discoverable to future collection consumers without creating
# another assembler or a second implementation of provenance policy.
validate_provenance_gate_result = validate_gate_result
verify_gate_result = validate_gate_result

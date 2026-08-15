"""Derive v0.13 Gate Results from reusable raw execution evidence.

The validator never accepts a caller-authored pass flag. It reopens every raw file,
checks its byte and record identity, binds it to the active exact candidate, derives
metrics from numerator/denominator samples, derives hard-failure counts, and applies
the active Gate v6 thresholds and Host constraints. One raw execution file may be
referenced by multiple independent Gate invocations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

REPOSITORY = Path(__file__).resolve().parents[2]
CLASSIFICATION_PATH = REPOSITORY / "benchmarks/release/v013-gate-classification-v6.json"
ACTIVE_QUALIFICATION_PATH = REPOSITORY / "benchmarks/v013/active-qualification-v1.json"
RAW_SCHEMA_PATH = REPOSITORY / "contracts/v013-gate-raw-evidence.v1.schema.json"
SELECTIVE_FORGET_SCHEMA_PATH = (
    REPOSITORY / "contracts/selective-forget-qualification.v1.schema.json"
)
RESULT_SCHEMA_PATH = REPOSITORY / "contracts/v013-gate-result.v1.schema.json"
RESULT_SCHEMA_VERSION = "deeplaw.v013-gate-result/v1"
VALIDATOR_VERSION = "1.0.0"
MAX_RAW_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GateValidationError(ValueError):
    """Raised when raw evidence cannot produce a trustworthy Gate Result."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_sha256(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
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


def _load_json(path: Path, *, maximum: int = MAX_RAW_BYTES) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise GateValidationError("qualification input must be a regular file")
    raw = path.read_bytes()
    if not 1 <= len(raw) <= maximum:
        raise GateValidationError("qualification input exceeds its byte bound")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise GateValidationError("qualification input must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise GateValidationError("qualification input must be a JSON object")
    return value, raw


def _schema(path: Path) -> dict[str, Any]:
    value, _raw = _load_json(path, maximum=2 * 1024 * 1024)
    Draft202012Validator.check_schema(value)
    return value


def _validate_schema(value: Mapping[str, Any], schema_path: Path, *, label: str) -> None:
    errors = sorted(
        Draft202012Validator(_schema(schema_path)).iter_errors(value),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        raise GateValidationError(
            f"{label} schema violation at {location}: {errors[0].message}"
        )


def _relative_file(root: Path, path: Path) -> str:
    selected_root = root.expanduser().resolve(strict=True)
    selected = path.expanduser().resolve(strict=True)
    try:
        relative = selected.relative_to(selected_root)
    except ValueError:
        raise GateValidationError("raw evidence escapes the qualification root") from None
    posix = PurePosixPath(relative.as_posix())
    if posix.is_absolute() or ".." in posix.parts:
        raise GateValidationError("raw evidence path is unsafe")
    return posix.as_posix()


def _active_binding(path: Path) -> tuple[dict[str, Any], bytes]:
    active, raw = _load_json(path, maximum=512 * 1024)
    _validate_schema(
        active,
        REPOSITORY / "contracts/v013-active-qualification.v1.schema.json",
        label="active qualification",
    )
    if active["status"] != "frozen_exact_candidate":
        raise GateValidationError("active qualification candidate is not frozen")
    if active["candidate_version"] != "0.13.0":
        raise GateValidationError("active qualification candidate is not v0.13.0")
    return active, raw


def _classification(path: Path) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    value, raw = _load_json(path, maximum=2 * 1024 * 1024)
    _validate_schema(
        value,
        REPOSITORY / "contracts/v013-release-gate-classification.v6.schema.json",
        label="Gate classification",
    )
    gates = {gate["gate_id"]: gate for gate in value["gates"]}
    if len(gates) != len(value["gates"]):
        raise GateValidationError("Gate classification contains duplicate identities")
    return value, raw, gates


def _threshold_failure(observed: float, minimum: float | None, maximum: float | None) -> bool:
    return bool(
        not math.isfinite(observed)
        or (minimum is not None and observed < minimum)
        or (maximum is not None and observed > maximum)
    )


def _raw_binding(
    raw: Mapping[str, Any],
    *,
    active: Mapping[str, Any],
    active_sha256: str,
) -> None:
    candidate = active["candidate_binding"]
    expected = {
        "commit": candidate["source_commit"],
        "tree": candidate["source_tree"],
        "lock_sha256": candidate["lock_sha256"],
        "wheel_sha256": candidate["wheel_sha256"],
        "sdist_sha256": candidate["sdist_sha256"],
    }
    if raw.get("candidate_version") != active["candidate_version"]:
        raise GateValidationError("raw evidence candidate version differs from active candidate")
    if raw.get("candidate_binding") != expected:
        raise GateValidationError("raw evidence candidate bytes differ from active candidate")
    protocol = raw.get("protocol_binding")
    if not isinstance(protocol, Mapping) or (
        protocol.get("protocol_id") != active["protocol_binding"]["protocol_id"]
        or protocol.get("protocol_sha256") != active["protocol_binding"]["sha256"]
        or protocol.get("active_qualification_sha256") != active_sha256
    ):
        raise GateValidationError("raw evidence protocol differs from active candidate")


def _input_binding(
    root: Path,
    path: Path,
    document: Mapping[str, Any],
    raw: bytes,
) -> dict[str, Any]:
    return {
        "relative_path": _relative_file(root, path),
        "byte_size": len(raw),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "schema_version": document["schema_version"],
        "record_sha256": document["record_sha256"],
    }


def _evidence_source_binding(
    raw: Mapping[str, Any],
    *,
    active: Mapping[str, Any],
) -> None:
    corpus = raw["corpus"]
    gold = raw["gold_binding"]
    isolation = raw["isolation"]
    role = corpus["role"]
    if gold["role"] != role:
        raise GateValidationError("Gold and corpus roles differ")
    if role == "development":
        source_tree_sha256 = hashlib.sha256(
            active["candidate_binding"]["source_tree"].encode("ascii")
        ).hexdigest()
        if (
            corpus["source"] != "repository"
            or corpus["sha256"] != source_tree_sha256
            or gold["source"] != "repository"
            or gold["independent"] is not False
            or gold["manifest_sha256"] != active["protocol_binding"]["sha256"]
            or isolation["manifest_sha256"] != active["protocol_binding"]["sha256"]
        ):
            raise GateValidationError("development evidence differs from the active source binding")
        return
    external = active["external_inputs"]
    corpus_key = {
        "qualification_holdout": "qualification_holdout_sha256",
        "final_blind": "final_blind_holdout_sha256",
    }[role]
    if (
        corpus["source"] != "repository_external"
        or corpus["sha256"] != external[corpus_key]
        or gold["source"] != "repository_external"
        or gold["independent"] is not True
        or gold["manifest_sha256"] != external["human_gold_manifest_sha256"]
        or isolation["manifest_sha256"] != external["compiler_scorer_isolation_sha256"]
    ):
        raise GateValidationError(
            "external Gold, corpus, or isolation differs from the active candidate"
        )


def _host_constraint_binding(
    gate_id: str,
    *,
    active: Mapping[str, Any],
    definition: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    host = definition["constraints"]["host"]
    if host not in {"codex", "opencode"}:
        return None
    expected = active["host_constraints"][host]
    if (
        definition["constraints"]["tool_version"] != expected["tool_version"]
        or definition["constraints"]["model_id"] != expected["model_id"]
        or gate_id != host
    ):
        raise GateValidationError(
            "Gate classification Host constraint differs from active qualification"
        )
    return expected


def _base_result(
    *,
    gate_id: str,
    definition: Mapping[str, Any],
    active: Mapping[str, Any],
    classification: Mapping[str, Any],
    classification_raw: bytes,
    validator_raw: bytes,
    gold_binding: Mapping[str, Any],
    corpus: Mapping[str, Any],
    executions: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    hard_failures: list[dict[str, Any]],
    failures: list[str],
    redaction: dict[str, Any],
    raw_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate = active["candidate_binding"]
    status = "failed" if failures else "passed"
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "artifact_kind": "v013-derived-provenance-gate-result",
        "gate_id": gate_id,
        "category": definition["category"],
        "candidate_version": active["candidate_version"],
        "candidate_binding": {
            "commit": candidate["source_commit"],
            "tree": candidate["source_tree"],
            "lock_sha256": candidate["lock_sha256"],
            "wheel_sha256": candidate["wheel_sha256"],
            "sdist_sha256": candidate["sdist_sha256"],
        },
        "classification_binding": {
            "document_id": classification["classification_id"],
            "sha256": hashlib.sha256(classification_raw).hexdigest(),
        },
        "protocol_binding": {
            "document_id": active["protocol_binding"]["protocol_id"],
            "sha256": active["protocol_binding"]["sha256"],
        },
        "gold_binding": dict(gold_binding),
        "corpus": dict(corpus),
        "validator": {
            "validator_id": definition["validator_id"],
            "validator_version": definition["validator_version"],
            "source_sha256": hashlib.sha256(validator_raw).hexdigest(),
            "executable_sha256": candidate["wheel_sha256"],
        },
        "status": status,
        "executions": executions,
        "metrics": metrics,
        "hard_failures": hard_failures,
        "failures": sorted(set(failures)),
        "redaction": redaction,
        "raw_inputs": raw_inputs,
    }
    result["record_sha256"] = record_sha256(result)
    _validate_schema(result, RESULT_SCHEMA_PATH, label="Gate Result")
    return result


def _validate_generic(
    gate_id: str,
    raw_paths: Sequence[Path],
    *,
    root: Path,
    active: Mapping[str, Any],
    active_sha256: str,
    classification: Mapping[str, Any],
    classification_raw: bytes,
    definition: Mapping[str, Any],
    validator_raw: bytes,
) -> dict[str, Any]:
    documents: list[tuple[dict[str, Any], bytes, Path]] = []
    for path in raw_paths:
        value, raw = _load_json(path)
        _validate_schema(value, RAW_SCHEMA_PATH, label="raw Gate evidence")
        if value["record_sha256"] != record_sha256(value):
            raise GateValidationError("raw Gate evidence record digest differs")
        _raw_binding(value, active=active, active_sha256=active_sha256)
        if gate_id not in value["gate_ids"]:
            raise GateValidationError("raw Gate evidence does not declare the selected Gate")
        documents.append((value, raw, path))
    first = documents[0][0]
    for value, _raw, _path in documents[1:]:
        for field in (
            "candidate_version",
            "candidate_binding",
            "protocol_binding",
            "gold_binding",
            "corpus",
        ):
            if value[field] != first[field]:
                raise GateValidationError(f"raw Gate evidence differs for {field}")
    corpus = first["corpus"]
    gold = first["gold_binding"]
    if corpus["role"] not in definition["required_corpus_roles"]:
        raise GateValidationError("raw Gate corpus role is not admitted by the classification")
    _evidence_source_binding(first, active=active)
    expected_host = _host_constraint_binding(
        gate_id,
        active=active,
        definition=definition,
    )

    failures: list[str] = []
    executions: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    observed_dimensions: dict[str, set[str]] = {}
    for value, raw_bytes, _path in documents:
        raw_sha = hashlib.sha256(raw_bytes).hexdigest()
        if any(
            value["redaction"][field] != 0
            for field in ("secret_canary_count", "private_path_count")
        ):
            failures.append("redaction_nonzero")
        for execution in value["executions"]:
            run_id = execution["run_id"]
            if run_id in run_ids:
                raise GateValidationError("raw execution run_id is not unique")
            run_ids.add(run_id)
            if execution["exit_code"] != 0:
                failures.append("execution_nonzero_exit")
            constraints = definition["constraints"]
            if constraints["host"] is not None and execution["tool_name"] != constraints["host"]:
                failures.append("host_constraint_mismatch")
            if (
                constraints["tool_version"] is not None
                and execution["tool_version"] != constraints["tool_version"]
            ):
                failures.append("tool_version_mismatch")
            if (
                constraints["model_id"] is not None
                and execution["model_id"] != constraints["model_id"]
            ):
                failures.append("model_constraint_mismatch")
            if (
                expected_host is not None
                and execution["reasoning_effort"] != expected_host["reasoning_effort"]
            ):
                failures.append("reasoning_effort_mismatch")
            if constraints["argv_prefix"] and execution["argv"][
                : len(constraints["argv_prefix"])
            ] != constraints["argv_prefix"]:
                failures.append("argv_constraint_mismatch")
            dimensions = dict(execution["dimensions"])
            dimensions.setdefault("run_id", run_id)
            dimensions.setdefault("platform", execution["os_name"])
            for name, item in dimensions.items():
                observed_dimensions.setdefault(name, set()).add(item)
            executions.append(
                {
                    "run_id": run_id,
                    "tool_name": execution["tool_name"],
                    "tool_version": execution["tool_version"],
                    "model_id": execution["model_id"],
                    "reasoning_effort": execution["reasoning_effort"],
                    "dimensions": dimensions,
                    "raw_input_sha256": raw_sha,
                }
            )
    if len(run_ids) < definition["minimum_distinct_run_count"]:
        failures.append("minimum_distinct_run_count")
    for dimension in definition["required_unique_dimensions"]:
        if dimension not in observed_dimensions:
            failures.append(f"missing_dimension-{dimension}")
    required_platforms = {
        (item["platform"], item["python_version"])
        for item in definition["required_execution_platforms"]
    }
    observed_platforms = {
        (
            execution["os_name"],
            execution["python_version"].rsplit(".", 1)[0]
            if execution["python_version"].count(".") == 2
            else execution["python_version"],
        )
        for value, _raw, _path in documents
        for execution in value["executions"]
    }
    if not required_platforms <= observed_platforms:
        failures.append("required_execution_platform_missing")

    expected_thresholds = {item["metric"]: item for item in definition["thresholds"]}
    samples: dict[str, list[tuple[float, float]]] = {key: [] for key in expected_thresholds}
    for value, _raw, _path in documents:
        for sample in value["metric_samples"]:
            if sample["gate_id"] == gate_id:
                if sample["metric"] not in samples or sample["run_id"] not in run_ids:
                    raise GateValidationError("raw metric sample is outside the Gate contract")
                samples[sample["metric"]].append(
                    (float(sample["numerator"]), float(sample["denominator"]))
                )
    metrics: list[dict[str, Any]] = []
    for metric, threshold in expected_thresholds.items():
        if not samples[metric]:
            failures.append(f"missing_metric-{metric}")
            continue
        numerator = sum(item[0] for item in samples[metric])
        denominator = sum(item[1] for item in samples[metric])
        observed = numerator / denominator
        metrics.append(
            {
                "metric": metric,
                "observed": observed,
                "numerator": numerator,
                "denominator": denominator,
                "minimum": threshold["minimum"],
                "maximum": threshold["maximum"],
            }
        )
        if _threshold_failure(observed, threshold["minimum"], threshold["maximum"]):
            failures.append(f"threshold_failed-{metric}")

    expected_hard = set(definition["hard_zero_derivation"]["failure_ids"])
    hard_counts = {failure_id: 0 for failure_id in expected_hard}
    seen_hard: set[str] = set()
    for value, _raw, _path in documents:
        for sample in value["hard_failure_samples"]:
            if sample["gate_id"] == gate_id:
                failure_id = sample["failure_id"]
                if failure_id not in hard_counts or sample["run_id"] not in run_ids:
                    raise GateValidationError(
                        "raw hard-failure sample is outside the Gate contract"
                    )
                seen_hard.add(failure_id)
                hard_counts[failure_id] += int(sample["count"])
    if seen_hard != expected_hard:
        failures.append("hard_failure_inventory_incomplete")
    hard_failures = [
        {"failure_id": key, "count": value, "maximum_allowed": 0}
        for key, value in sorted(hard_counts.items())
    ]
    if any(item["count"] for item in hard_failures):
        failures.append("hard_failure_nonzero")

    return _base_result(
        gate_id=gate_id,
        definition=definition,
        active=active,
        classification=classification,
        classification_raw=classification_raw,
        validator_raw=validator_raw,
        gold_binding=gold,
        corpus=corpus,
        executions=sorted(executions, key=lambda item: item["run_id"]),
        metrics=sorted(metrics, key=lambda item: item["metric"]),
        hard_failures=hard_failures,
        failures=failures,
        redaction={
            "secret_canary_count": sum(
                value["redaction"]["secret_canary_count"] for value, _raw, _path in documents
            ),
            "private_path_count": sum(
                value["redaction"]["private_path_count"] for value, _raw, _path in documents
            ),
            "content_minimized": True,
        },
        raw_inputs=[
            _input_binding(root, path, value, raw)
            for value, raw, path in documents
        ],
    )


def validate_selective_forget(
    raw_path: Path,
    *,
    root: Path,
    active: Mapping[str, Any],
    active_sha256: str,
    classification: Mapping[str, Any],
    classification_raw: bytes,
    definition: Mapping[str, Any],
    validator_raw: bytes,
) -> dict[str, Any]:
    """Derive selective-forget metrics and hard failures from exact receipts."""

    value, raw = _load_json(raw_path)
    _validate_schema(value, SELECTIVE_FORGET_SCHEMA_PATH, label="selective-forget raw receipt")
    if value["record_sha256"] != record_sha256(value):
        raise GateValidationError("selective-forget raw record digest differs")
    _raw_binding(value, active=active, active_sha256=active_sha256)
    checkpoint = value["checkpoint"]
    forgotten = value["forget"]
    after = value["post_forget_resume"]
    control = value["control_resume"]
    target = checkpoint["knowledge_id"]
    forgotten_state_admission_count = int(target in after["selected_knowledge_ids"])
    unrelated_state_preservation = int(
        control["status"] == "admitted" and bool(control["selected_knowledge_ids"])
    )
    ledger_read_invariance = int(
        value["ledger"]["head_after_forget"] == value["ledger"]["head_after_reads"]
    )
    failures: list[str] = []
    if forgotten["knowledge_id"] != target:
        failures.append("forget_target_mismatch")
    if forgotten["expected_revision_id"] != checkpoint["revision_id"]:
        failures.append("forget_cas_mismatch")
    if after["status"] != "gap":
        failures.append("post_forget_resume_not_gap")
    if not after["gap_codes"]:
        failures.append("post_forget_gap_missing")
    if forgotten_state_admission_count:
        failures.append("forgotten_state_admitted")
    if not unrelated_state_preservation:
        failures.append("unrelated_state_lost")
    if not ledger_read_invariance:
        failures.append("ledger_changed_on_read")
    secret_count = after["secret_count"] + control["secret_count"]
    path_count = after["absolute_path_count"] + control["absolute_path_count"]
    if secret_count or path_count:
        failures.append("provider_disclosure")
    observed_by_metric = {
        "forgotten_state_admission_count": float(forgotten_state_admission_count),
        "unrelated_state_preservation": float(unrelated_state_preservation),
        "ledger_read_invariance": float(ledger_read_invariance),
    }
    metrics: list[dict[str, Any]] = []
    for threshold in definition["thresholds"]:
        metric = threshold["metric"]
        if metric not in observed_by_metric:
            raise GateValidationError("selective-forget threshold has no raw derivation")
        observed = observed_by_metric[metric]
        metrics.append(
            {
                "metric": metric,
                "observed": observed,
                "numerator": observed,
                "denominator": 1.0,
                "minimum": threshold["minimum"],
                "maximum": threshold["maximum"],
            }
        )
        if _threshold_failure(observed, threshold["minimum"], threshold["maximum"]):
            failures.append(f"threshold_failed-{metric}")
    hard_counts = {
        "forgotten_state_admission": forgotten_state_admission_count,
        "unrelated_state_loss": 1 - unrelated_state_preservation,
        "ledger_read_mutation": 1 - ledger_read_invariance,
        "provider_disclosure": int(bool(secret_count or path_count)),
    }
    expected_hard = set(definition["hard_zero_derivation"]["failure_ids"])
    if set(hard_counts) != expected_hard:
        raise GateValidationError("selective-forget hard-failure mapping differs")
    hard_failures = [
        {"failure_id": key, "count": count, "maximum_allowed": 0}
        for key, count in sorted(hard_counts.items())
    ]
    if any(item["count"] for item in hard_failures):
        failures.append("hard_failure_nonzero")
    candidate = active["candidate_binding"]
    return _base_result(
        gate_id="selective_forget",
        definition=definition,
        active=active,
        classification=classification,
        classification_raw=classification_raw,
        validator_raw=validator_raw,
        gold_binding={
            "manifest_sha256": active["protocol_binding"]["sha256"],
            "role": "development",
            "source": "repository",
            "independent": False,
        },
        corpus={
            "sha256": candidate["tree" if "tree" in candidate else "source_tree"],
            "role": "development",
            "source": "repository",
            "read_only": True,
        },
        executions=[
            {
                "run_id": value["run_id"],
                "tool_name": "deeplaw",
                "tool_version": active["candidate_version"],
                "model_id": None,
                "reasoning_effort": None,
                "dimensions": {"operation": "selective-forget", "platform": "local"},
                "raw_input_sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
        metrics=metrics,
        hard_failures=hard_failures,
        failures=failures,
        redaction={
            "secret_canary_count": secret_count,
            "private_path_count": path_count,
            "content_minimized": True,
        },
        raw_inputs=[_input_binding(root, raw_path, value, raw)],
    )


def validate_gate(
    gate_id: str,
    raw_paths: Sequence[Path],
    *,
    root: Path = REPOSITORY,
    active_path: Path = ACTIVE_QUALIFICATION_PATH,
    classification_path: Path = CLASSIFICATION_PATH,
) -> dict[str, Any]:
    if not raw_paths:
        raise GateValidationError("at least one raw Gate input is required")
    active, active_raw = _active_binding(active_path)
    active_sha256 = hashlib.sha256(active_raw).hexdigest()
    classification, classification_raw, gates = _classification(classification_path)
    definition = gates.get(gate_id)
    if definition is None or definition["implementation_status"] != "ready":
        raise GateValidationError("Gate has no ready validator in the active classification")
    validator_raw = Path(__file__).read_bytes()
    if gate_id == "selective_forget":
        if len(raw_paths) != 1:
            raise GateValidationError("selective-forget accepts one exact raw receipt")
        return validate_selective_forget(
            raw_paths[0],
            root=root,
            active=active,
            active_sha256=active_sha256,
            classification=classification,
            classification_raw=classification_raw,
            definition=definition,
            validator_raw=validator_raw,
        )
    return _validate_generic(
        gate_id,
        raw_paths,
        root=root,
        active=active,
        active_sha256=active_sha256,
        classification=classification,
        classification_raw=classification_raw,
        definition=definition,
        validator_raw=validator_raw,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="Derive one DeepLaw v0.13 Gate Result")
    parser.add_argument("--gate-id", required=True)
    parser.add_argument("--raw", type=Path, action="append", required=True)
    parser.add_argument("--root", type=Path, default=REPOSITORY)
    parser.add_argument("--active", type=Path, default=ACTIVE_QUALIFICATION_PATH)
    parser.add_argument("--classification", type=Path, default=CLASSIFICATION_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_gate(
            args.gate_id,
            args.raw,
            root=args.root,
            active_path=args.active,
            classification_path=args.classification,
        )
        args.output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    except (OSError, GateValidationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

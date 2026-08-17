"""Derive historical v0.13 v6 Gate Results from reusable raw execution evidence.

The validator never accepts a caller-authored pass flag. It reopens every raw file,
checks its byte and record identity, binds it to the active exact candidate, derives
metrics from numerator/denominator samples, derives hard-failure counts, and applies
the frozen Gate v6 thresholds and Host constraints. One raw execution file may be
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

from defusedxml import ElementTree as ET
from jsonschema import Draft202012Validator

from benchmarks.release.platform_gate import PlatformGateError, load_platform_manifest

REPOSITORY = Path(__file__).resolve().parents[2]
CLASSIFICATION_PATH = REPOSITORY / "benchmarks/release/v013-gate-classification-v6.json"
ACTIVE_QUALIFICATION_PATH = REPOSITORY / "benchmarks/v013/active-qualification-v1.json"
RAW_SCHEMA_PATH = REPOSITORY / "contracts/v013-gate-raw-evidence.v1.schema.json"
SOURCE_SCHEMA_PATH = REPOSITORY / "contracts/v013-gate-source-evidence.v1.schema.json"
SOURCE_OBSERVATION_SCHEMA_PATH = (
    REPOSITORY / "contracts/v013-source-observation.v1.schema.json"
)
PLATFORM_MANIFEST_PATH = (
    REPOSITORY / "benchmarks/release/platform-core-test-manifest-v2.json"
)
SELECTIVE_FORGET_SCHEMA_PATH = (
    REPOSITORY / "contracts/selective-forget-qualification.v1.schema.json"
)
SELECTIVE_FORGET_EVIDENCE_SCHEMA_PATH = (
    REPOSITORY / "contracts/v013-selective-forget-evidence.v1.schema.json"
)
RESULT_SCHEMA_PATH = REPOSITORY / "contracts/v013-gate-result.v1.schema.json"
RESULT_SCHEMA_VERSION = "deeplaw.v013-gate-result/v1"
VALIDATOR_VERSION = "1.0.0"
MAX_RAW_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_SCAN = re.compile(
    r"(?i)DEEPLAW_TEST_AMBIENT_SECRET|-----BEGIN(?: [A-Z0-9 ]+)?-----"
    r"|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}\b|\bgh[pousr]_[A-Za-z0-9]{16,}\b"
    r"|\bxox[baprs]-[A-Za-z0-9-]{12,}\b"
)
_PRIVATE_PATH_SCAN = re.compile(
    r"(?:(?<![A-Za-z0-9])/(?:Users|home|private|var|tmp|root|etc|opt|Volumes)(?:/|$))"
    r"|(?:^|[\s\"'])~/(?:[^\s\"']*)"
    r"|(?:^|[\s\"'])[A-Za-z]:[\\/]",
    re.IGNORECASE,
)
_SOURCE_BY_GATE = {
    "canonical_integrity": "ci_junit",
    "migration_recovery": "ci_junit",
    "secret_host_isolation": "ci_junit",
    "bounded_context": "host_receipt",
    "legal_evidence": "legal_exact_source",
    "source_citation_locator": "legal_exact_source",
    "scale_performance": "scale_report",
    "supported_platforms": "platform",
    "reproducible_supply_chain": "reproducible_artifact",
    "human_gold_isolation": "human_gold_scorer",
    "codex": "host_receipt",
    "opencode": "host_receipt",
    "timeline": "timeline_receipt",
}
_JUNIT_REQUIRED_CASES = {
    "canonical_integrity": {
        (
            "tests.test_knowledge_assets",
            "test_audit_chain_detects_database_tampering",
        ),
        (
            "tests.test_autonomous_knowledge",
            "test_doctor_includes_autonomous_canonical_integrity",
        ),
    },
    "migration_recovery": {
        (
            "tests.test_knowledge_control",
            "test_interrupted_migration_rolls_back_and_retains_a_verified_backup",
        ),
        (
            "tests.test_v013_pass22_continuity_closure",
            "test_partial_checkpoint_recovers_after_process_exit_and_restart",
        ),
    },
    "secret_host_isolation": {
        (
            "tests.test_v013_host_environment_isolation",
            "test_fake_mcp_child_cannot_see_ambient_or_provider_secret",
        ),
        (
            "tests.test_v013_pass21_task_routing_closure",
            "test_secret_looking_untracked_file_fails_closed_without_content_read",
        ),
    },
}


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
    try:
        canonical_raw = CLASSIFICATION_PATH.read_bytes()
    except OSError as error:
        raise GateValidationError("canonical Gate classification is unavailable") from error
    if raw != canonical_raw:
        raise GateValidationError("Gate classification differs from canonical v6 bytes")
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


def _workflow_provenance_binding(
    raw: Mapping[str, Any],
    *,
    active: Mapping[str, Any],
    expected_evidence_run_id: int | None,
) -> None:
    if (
        not isinstance(expected_evidence_run_id, int)
        or isinstance(expected_evidence_run_id, bool)
        or expected_evidence_run_id < 1
    ):
        raise GateValidationError(
            "Core source evidence requires an externally verified workflow run identity"
        )
    provenance = raw["workflow_provenance"]
    if (
        provenance["workflow_run_id"] != expected_evidence_run_id
        or provenance["head_sha"] != active["candidate_binding"]["source_commit"]
    ):
        raise GateValidationError(
            "source evidence workflow provenance differs from the verified run"
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
    if definition["category"] == "Core":
        raise GateValidationError(
            "generic raw evidence is development diagnostic only and cannot validate a Core Gate"
        )
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


def _source_path(root: Path, reference: Mapping[str, Any]) -> tuple[Path, bytes]:
    relative = reference["relative_path"]
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise GateValidationError("source evidence path is unsafe")
    selected_root = root.expanduser().resolve(strict=True)
    selected = selected_root.joinpath(*path.parts)
    if selected.is_symlink() or not selected.is_file():
        raise GateValidationError("source evidence must be a regular file")
    try:
        selected.resolve(strict=True).relative_to(selected_root)
    except ValueError:
        raise GateValidationError("source evidence escapes the qualification root") from None
    raw = selected.read_bytes()
    if not 1 <= len(raw) <= MAX_RAW_BYTES:
        raise GateValidationError("source evidence exceeds its byte bound")
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise GateValidationError("retained source evidence digest differs")
    return selected, raw


def _scan_source(raw: bytes) -> tuple[int, int]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise GateValidationError("source evidence must be UTF-8") from error
    return len(_SECRET_SCAN.findall(text)), len(_PRIVATE_PATH_SCAN.findall(text))


def _platform_cases(*, os_name: str) -> set[tuple[str, str]]:
    try:
        manifest = load_platform_manifest(PLATFORM_MANIFEST_PATH)
    except PlatformGateError as error:
        raise GateValidationError("frozen Platform Core manifest is invalid") from error
    cases = list(manifest["inventories"]["common"]["cases"])
    if os_name == "windows":
        cases.extend(manifest["inventories"]["windows"]["additional_cases"])
    return {
        (str(case["junit"]["classname"]), str(case["junit"]["name"]))
        for case in cases
    }


def _junit_observation(
    raw: bytes,
    *,
    gate_id: str,
    os_name: str,
) -> tuple[bool, int, int, int]:
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise GateValidationError("JUnit evidence contains a forbidden XML declaration")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise GateValidationError("JUnit evidence is invalid XML") from error
    if root.tag not in {"testsuite", "testsuites"}:
        raise GateValidationError("JUnit evidence has no testsuite root")
    cases = list(root.iter("testcase"))
    if not cases:
        raise GateValidationError("JUnit evidence contains no test cases")
    failed = sum(
        any(child.tag in {"failure", "error", "skipped"} for child in case)
        for case in cases
    )
    identities = [
        (case.attrib.get("classname", ""), case.attrib.get("name", ""))
        for case in cases
    ]
    if any(not classname or not name for classname, name in identities):
        raise GateValidationError("JUnit testcase identity is incomplete")
    if len(identities) != len(set(identities)):
        raise GateValidationError("JUnit testcase identity is duplicated")
    observed = set(identities)
    if gate_id == "supported_platforms":
        expected = _platform_cases(os_name=os_name)
        if observed != expected:
            raise GateValidationError(
                "Platform JUnit inventory differs from the frozen Platform Core manifest"
            )
    else:
        required = _JUNIT_REQUIRED_CASES.get(gate_id)
        if required is None or not required <= observed:
            raise GateValidationError(
                "CI JUnit omits the Gate-specific public seam inventory"
            )
    scan_parts: list[str] = []
    for element in root.iter():
        for key, item in element.attrib.items():
            if element.tag == "testcase" and key in {"classname", "name"}:
                continue
            scan_parts.append(item)
        if element.text:
            scan_parts.append(element.text)
        if element.tail:
            scan_parts.append(element.tail)
    scan_raw = "\n".join(scan_parts).encode("utf-8")
    secret_matches, path_matches = _scan_source(scan_raw)
    return failed == 0, failed, secret_matches, path_matches


def _fact_bool(facts: Mapping[str, Any], key: str) -> bool:
    value = facts[key]
    if not isinstance(value, bool):
        raise GateValidationError(f"source observation fact {key} must be boolean")
    return value


def _fact_int(facts: Mapping[str, Any], key: str) -> int:
    value = facts[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise GateValidationError(f"source observation fact {key} must be integer")
    return value


def _fact_text(facts: Mapping[str, Any], key: str) -> str:
    value = facts[key]
    if not isinstance(value, str) or not value:
        raise GateValidationError(f"source observation fact {key} must be text")
    return value


def _exact_facts(facts: Mapping[str, Any], expected: set[str]) -> None:
    if set(facts) != expected:
        raise GateValidationError("source observation fact inventory differs for its Gate")


def _derive_observation_facts(
    gate_id: str,
    facts: Mapping[str, Any],
    *,
    active: Mapping[str, Any],
) -> tuple[bool, dict[str, int]]:
    if gate_id == "bounded_context":
        _exact_facts(
            facts,
            {
                "provider_bytes",
                "provider_hard_limit_bytes",
                "payload_admitted",
                "secret_matches",
                "private_path_matches",
            },
        )
        provider_bytes = _fact_int(facts, "provider_bytes")
        hard_limit = _fact_int(facts, "provider_hard_limit_bytes")
        bound_failure = int(provider_bytes > hard_limit or hard_limit > 65_536)
        passed = (
            _fact_bool(facts, "payload_admitted")
            and bound_failure == 0
            and _fact_int(facts, "secret_matches") == 0
            and _fact_int(facts, "private_path_matches") == 0
        )
        return passed, {"provider_payload_bound_exceeded": bound_failure}
    if gate_id == "legal_evidence":
        _exact_facts(
            facts,
            {
                "source_count",
                "exact_source_count",
                "false_authority_count",
                "wrong_version_primary_count",
                "invalid_quote_count",
                "invalid_locator_count",
                "protected_source_mutation_count",
                "cross_boundary_disclosure_count",
                "secret_matches",
            },
        )
        hard = {
            "false_authority": _fact_int(facts, "false_authority_count"),
            "wrong_version_primary": _fact_int(facts, "wrong_version_primary_count"),
            "invalid_quote": _fact_int(facts, "invalid_quote_count"),
            "invalid_locator": _fact_int(facts, "invalid_locator_count"),
            "protected_source_mutation": _fact_int(
                facts, "protected_source_mutation_count"
            ),
            "cross_boundary_disclosure": _fact_int(
                facts, "cross_boundary_disclosure_count"
            ),
            "secret_leak": _fact_int(facts, "secret_matches"),
        }
        complete = (
            _fact_int(facts, "source_count") == 28
            and _fact_int(facts, "exact_source_count") == 28
        )
        return complete and not any(hard.values()), hard
    if gate_id == "source_citation_locator":
        _exact_facts(
            facts,
            {
                "citation_count",
                "invalid_source_count",
                "invalid_quote_count",
                "invalid_locator_count",
            },
        )
        hard = {
            "invalid_source": _fact_int(facts, "invalid_source_count"),
            "invalid_quote": _fact_int(facts, "invalid_quote_count"),
            "invalid_locator": _fact_int(facts, "invalid_locator_count"),
        }
        return _fact_int(facts, "citation_count") > 0 and not any(hard.values()), hard
    if gate_id == "scale_performance":
        _exact_facts(
            facts,
            {
                "scale_1k_public_completed",
                "scale_10k_public_completed",
                "scale_100k_public_completed",
                "private_bulk_api_used",
                "second_store_used",
            },
        )
        incomplete = sum(
            not _fact_bool(facts, key)
            for key in (
                "scale_1k_public_completed",
                "scale_10k_public_completed",
                "scale_100k_public_completed",
            )
        ) + int(_fact_bool(facts, "private_bulk_api_used")) + int(
            _fact_bool(facts, "second_store_used")
        )
        return incomplete == 0, {"required_scale_not_executed": incomplete}
    if gate_id == "reproducible_supply_chain":
        _exact_facts(
            facts,
            {
                "first_wheel_sha256",
                "second_wheel_sha256",
                "candidate_wheel_sha256",
                "first_sdist_sha256",
                "second_sdist_sha256",
                "candidate_sdist_sha256",
                "sbom_verified",
                "openvex_verified",
                "licenses_verified",
                "provenance_verified",
                "signature_verified",
                "public_redownload_verified",
                "secret_matches",
                "private_path_matches",
            },
        )
        candidate = active["candidate_binding"]
        wheel_hashes = {
            _fact_text(facts, "first_wheel_sha256"),
            _fact_text(facts, "second_wheel_sha256"),
            _fact_text(facts, "candidate_wheel_sha256"),
            candidate["wheel_sha256"],
        }
        sdist_hashes = {
            _fact_text(facts, "first_sdist_sha256"),
            _fact_text(facts, "second_sdist_sha256"),
            _fact_text(facts, "candidate_sdist_sha256"),
            candidate["sdist_sha256"],
        }
        hash_mismatch = int(
            len(wheel_hashes) != 1
            or len(sdist_hashes) != 1
            or any(_SHA256.fullmatch(value) is None for value in wheel_hashes | sdist_hashes)
        )
        supporting = all(
            _fact_bool(facts, key)
            for key in (
                "sbom_verified",
                "openvex_verified",
                "licenses_verified",
                "provenance_verified",
                "signature_verified",
                "public_redownload_verified",
            )
        )
        hard = {
            "artifact_hash_mismatch": hash_mismatch + int(not supporting),
            "secret_leak": _fact_int(facts, "secret_matches"),
            "private_path_disclosure": _fact_int(facts, "private_path_matches"),
        }
        return not any(hard.values()), hard
    if gate_id == "human_gold_isolation":
        _exact_facts(
            facts,
            {
                "source_mount_read_only",
                "compiler_gold_visible",
                "compiler_scorer_visible",
                "scorer_process_separate",
                "repository_source_visible",
                "ambient_credentials_visible",
                "evaluator_output_mutations",
                "blind_contaminations",
            },
        )
        compiler_access = int(
            _fact_bool(facts, "compiler_gold_visible")
            or _fact_bool(facts, "compiler_scorer_visible")
            or _fact_bool(facts, "repository_source_visible")
            or _fact_bool(facts, "ambient_credentials_visible")
            or not _fact_bool(facts, "source_mount_read_only")
            or not _fact_bool(facts, "scorer_process_separate")
        )
        hard = {
            "compiler_gold_access": compiler_access,
            "evaluator_output_mutation": _fact_int(facts, "evaluator_output_mutations"),
            "blind_contamination": _fact_int(facts, "blind_contaminations"),
        }
        return not any(hard.values()), hard
    if gate_id in {"codex", "opencode"}:
        _exact_facts(
            facts,
            {
                "binary_sha256",
                "native_receipt_sha256",
                "response_model_id",
                "corpus_role",
                "corpus_sha256",
                "first_correct_action",
                "decision_preservation",
                "wrong_state_admission",
                "stale_state_rejected",
                "wrong_version_rejected",
                "provider_bytes",
                "provider_hard_limit_bytes",
                "secret_matches",
                "wrong_tool_or_parameter",
                "actual_provider_tokens",
                "ledger_write_boundary_valid",
            },
        )
        expected = active["host_constraints"][gate_id]
        model_substitution = int(_fact_text(facts, "response_model_id") != expected["model_id"])
        binary = _fact_text(facts, "binary_sha256")
        lifecycle = _fact_text(facts, "native_receipt_sha256")
        if _SHA256.fullmatch(binary) is None or _SHA256.fullmatch(lifecycle) is None:
            raise GateValidationError(
                "Host observation omits an exact binary or native receipt hash"
            )
        role = _fact_text(facts, "corpus_role")
        if role not in {"qualification_holdout", "final_blind"}:
            raise GateValidationError("Host observation corpus role is not qualification evidence")
        corpus_key = (
            "qualification_holdout_sha256"
            if role == "qualification_holdout"
            else "final_blind_holdout_sha256"
        )
        if _fact_text(facts, "corpus_sha256") != active["external_inputs"][corpus_key]:
            raise GateValidationError("Host observation corpus differs from the frozen input")
        provider_within_bound = _fact_int(facts, "provider_bytes") <= _fact_int(
            facts, "provider_hard_limit_bytes"
        ) <= 65_536
        hard = {
            "model_substitution": model_substitution,
            "secret_exposure": _fact_int(facts, "secret_matches"),
            "wrong_tool_or_parameter": _fact_int(facts, "wrong_tool_or_parameter"),
        }
        passed = (
            _fact_bool(facts, "first_correct_action")
            and _fact_bool(facts, "decision_preservation")
            and _fact_int(facts, "wrong_state_admission") == 0
            and _fact_bool(facts, "stale_state_rejected")
            and _fact_bool(facts, "wrong_version_rejected")
            and provider_within_bound
            and _fact_int(facts, "actual_provider_tokens") > 0
            and _fact_bool(facts, "ledger_write_boundary_valid")
            and not any(hard.values())
        )
        return passed, hard
    if gate_id == "timeline":
        _exact_facts(
            facts,
            {
                "stable_route_bound",
                "expected_identity_count",
                "observed_identity_count",
                "wrong_run_inclusion",
                "private_path_matches",
                "content_field_count",
                "bounded",
                "gap_on_diverged",
                "gap_on_forgotten",
            },
        )
        hard = {
            "wrong_run_inclusion": _fact_int(facts, "wrong_run_inclusion"),
            "private_path_disclosure": _fact_int(facts, "private_path_matches"),
        }
        passed = (
            _fact_bool(facts, "stable_route_bound")
            and _fact_int(facts, "expected_identity_count")
            == _fact_int(facts, "observed_identity_count")
            and _fact_int(facts, "content_field_count") == 0
            and _fact_bool(facts, "bounded")
            and _fact_bool(facts, "gap_on_diverged")
            and _fact_bool(facts, "gap_on_forgotten")
            and not any(hard.values())
        )
        return passed, hard
    raise GateValidationError("Gate has no low-level source observation derivation")


def _source_observation(
    raw: bytes,
    *,
    gate_id: str,
    evidence_source: str,
    run_id: str,
    active: Mapping[str, Any],
) -> tuple[bool, dict[str, int], Mapping[str, Any]]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise GateValidationError("source observation must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise GateValidationError("source observation must be a JSON object")
    _validate_schema(
        value,
        SOURCE_OBSERVATION_SCHEMA_PATH,
        label="source observation",
    )
    if value["record_sha256"] != record_sha256(value):
        raise GateValidationError("source observation record digest differs")
    if (
        value["gate_id"] != gate_id
        or value["evidence_source"] != evidence_source
        or value["run_id"] != run_id
    ):
        raise GateValidationError("source observation identity differs from its envelope")
    candidate = active["candidate_binding"]
    expected_candidate = {
        "commit": candidate["source_commit"],
        "tree": candidate["source_tree"],
        "lock_sha256": candidate["lock_sha256"],
        "wheel_sha256": candidate["wheel_sha256"],
        "sdist_sha256": candidate["sdist_sha256"],
    }
    if value["candidate_binding"] != expected_candidate:
        raise GateValidationError("source observation differs from the exact candidate")
    passed, hard = _derive_observation_facts(
        gate_id,
        value["facts"],
        active=active,
    )
    return passed, hard, value["facts"]


def _validate_source_specific(
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
    expected_evidence_run_id: int | None,
) -> dict[str, Any]:
    """Derive a Core result from retained source observations, not supplied outcomes."""

    expected_source = _SOURCE_BY_GATE.get(gate_id)
    if expected_source is None:
        raise GateValidationError("Gate has no source-specific evidence validator")
    documents: list[tuple[dict[str, Any], bytes, Path]] = []
    for path in raw_paths:
        value, raw = _load_json(path)
        _validate_schema(value, SOURCE_SCHEMA_PATH, label="source-specific Gate evidence")
        if value["record_sha256"] != record_sha256(value):
            raise GateValidationError("source-specific Gate evidence record digest differs")
        _raw_binding(value, active=active, active_sha256=active_sha256)
        if value["gate_id"] != gate_id:
            raise GateValidationError("source-specific evidence belongs to another Gate")
        if value["evidence_source"] != expected_source:
            raise GateValidationError("Gate evidence source does not match its validator")
        if value["artifact_kind"] not in definition["artifact_kinds"]:
            raise GateValidationError("Gate evidence artifact kind differs from classification")
        _workflow_provenance_binding(
            value,
            active=active,
            expected_evidence_run_id=expected_evidence_run_id,
        )
        documents.append((value, raw, path))
    first = documents[0][0]
    for value, _raw, _path in documents[1:]:
        for field in (
            "candidate_version",
            "candidate_binding",
            "protocol_binding",
            "gold_binding",
            "corpus",
            "isolation",
            "evidence_source",
            "gate_id",
        ):
            if value[field] != first[field]:
                raise GateValidationError(f"source-specific evidence differs for {field}")
    corpus = first["corpus"]
    gold = first["gold_binding"]
    if corpus["role"] not in definition["required_corpus_roles"]:
        raise GateValidationError("source-specific corpus role is not admitted")
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
    failed_execution_count = 0
    expected_hard = set(definition["hard_zero_derivation"]["failure_ids"])
    hard_counts = {failure_id: 0 for failure_id in expected_hard}
    secret_matches = 0
    path_matches = 0
    host_roles: list[str] = []
    for value, _raw_bytes, _path in documents:
        for execution in value["executions"]:
            run_id = execution["run_id"]
            if run_id in run_ids:
                raise GateValidationError("source-specific run_id is not unique")
            run_ids.add(run_id)
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
            python_version = execution["python_version"]
            dimensions.setdefault(
                "python_version",
                python_version.rsplit(".", 1)[0]
                if python_version.count(".") == 2
                else python_version,
            )
            for name, item in dimensions.items():
                observed_dimensions.setdefault(name, set()).add(item)
            _source_file, source_raw = _source_path(root, execution["source"])
            source_format = execution["source"]["format"]
            if expected_source in {"ci_junit", "platform"}:
                if source_format != "junit_xml":
                    raise GateValidationError("CI and Platform evidence must retain JUnit XML")
                (
                    execution_passed,
                    source_failure_count,
                    source_secret,
                    source_paths,
                ) = _junit_observation(
                    source_raw,
                    gate_id=gate_id,
                    os_name=execution["os_name"],
                )
                for failure_id in hard_counts:
                    hard_counts[failure_id] += source_failure_count
            else:
                source_secret, source_paths = _scan_source(source_raw)
                if source_format != "source_observation_v1":
                    raise GateValidationError(
                        "Gate evidence must retain its versioned source observation"
                    )
                execution_passed, source_hard, facts = _source_observation(
                    source_raw,
                    gate_id=gate_id,
                    evidence_source=expected_source,
                    run_id=run_id,
                    active=active,
                )
                if set(source_hard) != expected_hard:
                    raise GateValidationError("source hard-failure derivation differs from Gate")
                for failure_id, count in source_hard.items():
                    hard_counts[failure_id] += count
                if gate_id in {"codex", "opencode"}:
                    role = _fact_text(facts, "corpus_role")
                    host_roles.append(role)
                    dimensions.setdefault("corpus_role", role)
            secret_matches += source_secret
            path_matches += source_paths
            if not execution_passed:
                failed_execution_count += 1
                failures.append("source_observation_mismatch")
            executions.append(
                {
                    "run_id": run_id,
                    "tool_name": execution["tool_name"],
                    "tool_version": execution["tool_version"],
                    "model_id": execution["model_id"],
                    "reasoning_effort": execution["reasoning_effort"],
                    "dimensions": dimensions,
                    "raw_input_sha256": hashlib.sha256(source_raw).hexdigest(),
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
    if gate_id in {"codex", "opencode"}:
        if host_roles.count("qualification_holdout") < 2 or host_roles.count(
            "final_blind"
        ) < 1:
            failures.append("host_holdout_blind_coverage_missing")
        if len(observed_dimensions.get("task_case", set())) != len(run_ids):
            failures.append("host_task_case_not_distinct")

    passing_execution_count = len(executions) - failed_execution_count
    metrics: list[dict[str, Any]] = []
    for threshold in definition["thresholds"]:
        if threshold["metric"] == "platform_matrix_rows":
            numerator = float(passing_execution_count)
            denominator = 1.0
            observed = numerator
        else:
            numerator = float(passing_execution_count)
            denominator = float(len(executions))
            observed = numerator / denominator
        metrics.append(
            {
                "metric": threshold["metric"],
                "observed": observed,
                "numerator": numerator,
                "denominator": denominator,
                "minimum": threshold["minimum"],
                "maximum": threshold["maximum"],
            }
        )
        if _threshold_failure(observed, threshold["minimum"], threshold["maximum"]):
            failures.append(f"threshold_failed-{threshold['metric']}")

    if secret_matches or path_matches:
        failures.append("redaction_nonzero")
    for failure_id in hard_counts:
        if "secret" in failure_id:
            hard_counts[failure_id] += secret_matches
        if "path" in failure_id or "disclosure" in failure_id:
            hard_counts[failure_id] += path_matches
    hard_failures = [
        {"failure_id": failure_id, "count": count, "maximum_allowed": 0}
        for failure_id, count in sorted(hard_counts.items())
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
            "secret_canary_count": secret_matches,
            "private_path_count": path_matches,
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
    expected_evidence_run_id: int | None,
) -> dict[str, Any]:
    """Derive selective-forget metrics and hard failures from exact receipts."""

    envelope, raw = _load_json(raw_path)
    _validate_schema(
        envelope,
        SELECTIVE_FORGET_EVIDENCE_SCHEMA_PATH,
        label="selective-forget workflow evidence",
    )
    if envelope["record_sha256"] != record_sha256(envelope):
        raise GateValidationError("selective-forget workflow evidence digest differs")
    _workflow_provenance_binding(
        envelope,
        active=active,
        expected_evidence_run_id=expected_evidence_run_id,
    )
    value = envelope["receipt"]
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
    ledger = value["ledger"]
    write_events = ledger["write_event_types"]
    if checkpoint["run_id"] != value["run_id"]:
        failures.append("checkpoint_run_mismatch")
    if forgotten["knowledge_id"] != target:
        failures.append("forget_target_mismatch")
    if forgotten["expected_revision_id"] != checkpoint["revision_id"]:
        failures.append("forget_cas_mismatch")
    if after["status"] != "gap":
        failures.append("post_forget_resume_not_gap")
    if not after["gap_codes"]:
        failures.append("post_forget_gap_missing")
    if target in control["selected_knowledge_ids"]:
        failures.append("control_contains_forgotten_target")
    if forgotten["tombstone_revision_id"] == checkpoint["revision_id"]:
        failures.append("forget_tombstone_not_new")
    if (
        write_events.count("knowledge_run_recorded") != 1
        or write_events.count("knowledge_revision_committed") < 2
        or ledger["head_after_checkpoint"] == ledger["head_after_forget"]
        or ledger["head_before_checkpoint"] == ledger["head_after_checkpoint"]
    ):
        failures.append("ledger_write_trajectory_invalid")
    if forgotten_state_admission_count:
        failures.append("forgotten_state_admitted")
    if not unrelated_state_preservation:
        failures.append("unrelated_state_lost")
    if not ledger_read_invariance:
        failures.append("ledger_changed_on_read")
    scanned_secret_count, scanned_path_count = _scan_source(raw)
    secret_count = (
        after["secret_count"] + control["secret_count"] + scanned_secret_count
    )
    path_count = (
        after["absolute_path_count"] + control["absolute_path_count"] + scanned_path_count
    )
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
            "sha256": hashlib.sha256(candidate["source_tree"].encode("ascii")).hexdigest(),
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
        raw_inputs=[_input_binding(root, raw_path, envelope, raw)],
    )


def validate_gate(
    gate_id: str,
    raw_paths: Sequence[Path],
    *,
    root: Path = REPOSITORY,
    active_path: Path = ACTIVE_QUALIFICATION_PATH,
    classification_path: Path = CLASSIFICATION_PATH,
    expected_evidence_run_id: int | None = None,
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
            expected_evidence_run_id=expected_evidence_run_id,
        )
    first, _first_raw = _load_json(raw_paths[0])
    if first.get("schema_version") == "deeplaw.v013-gate-source-evidence/v1":
        return _validate_source_specific(
            gate_id,
            raw_paths,
            root=root,
            active=active,
            active_sha256=active_sha256,
            classification=classification,
            classification_raw=classification_raw,
            definition=definition,
            validator_raw=validator_raw,
            expected_evidence_run_id=expected_evidence_run_id,
        )
    if first.get("schema_version") != "deeplaw.v013-gate-raw-evidence/v1":
        raise GateValidationError("Gate evidence schema has no source-specific validator")
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
    parser.add_argument("--evidence-run-id", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_gate(
            args.gate_id,
            args.raw,
            root=args.root,
            active_path=args.active,
            classification_path=args.classification,
            expected_evidence_run_id=args.evidence_run_id,
        )
        args.output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    except (OSError, GateValidationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

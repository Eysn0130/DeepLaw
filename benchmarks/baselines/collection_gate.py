from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.baselines.manual_adapter import (
    MANUAL_PLAN_SCHEMA,
    _validate_manual_record,
    validate_manual_execution_plan,
    validate_manual_execution_receipt,
)
from benchmarks.baselines.manual_adapter import (
    _verify_live_plan as _verify_live_manual_plan,
)
from benchmarks.baselines.official_adapter import (
    _checkout_binding,
    _file_binding,
    _input_binding,
    _queries_binding,
    _read_query_case_ids,
    _reserve_binary,
    _validate_evaluation_environment,
    _validate_output,
    _validate_resource_record,
    _write_reserved,
    validate_execution_plan,
    validate_execution_receipt,
)
from benchmarks.baselines.registry import (
    load_registry,
    registry_sha256,
)
from benchmarks.external.benchlib import strict_json_loads
from deeplaw.util import canonical_json, sha256_bytes, sha256_file

COLLECTION_SCHEMA = "deeplaw.baseline-evidence-collection/v1"
COLLECTION_REPORT_SCHEMA = "deeplaw.baseline-evidence-collection-report/v1"
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_RUNS = 64
_CLAIM_INELIGIBILITY_REASON = (
    "collection completeness is necessary but is not statistical or independent "
    "claim evidence"
)


class EvidenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidRun:
    system_id: str
    plan: dict[str, Any]
    receipt: dict[str, Any]
    environment: dict[str, Any]
    resource: dict[str, Any] | None


def _registry_system(registry: dict[str, Any], system_id: str) -> dict[str, Any]:
    selected = next(
        (item for item in registry["systems"] if item["system_id"] == system_id),
        None,
    )
    if selected is None:
        raise ValueError(f"baseline system is not registered: {system_id}")
    return selected


def _bounded_string(value: Any, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be a bounded canonical string")
    return value


def _absolute_path(value: Any, *, field: str) -> Path:
    path = Path(_bounded_string(value, field=field, maximum=4_096))
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    return path


def _read_json(path: Path, *, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field} must be a regular non-symlink file")
    size = path.stat().st_size
    if not 1 <= size <= _MAX_JSON_BYTES:
        raise ValueError(f"{field} violates its byte bound")
    try:
        value = strict_json_loads(path.read_bytes())
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{field} must contain strict JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return value


def _record_digest(record: dict[str, Any], *, digest_field: str, field: str) -> str:
    digest = record.get(digest_field)
    body = {key: value for key, value in record.items() if key != digest_field}
    expected = sha256_bytes(canonical_json(body).encode("utf-8"))
    if digest != expected:
        raise ValueError(f"{field} digest is invalid")
    return expected


def validate_collection_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "collection_id",
        "registry_sha256",
        "runs",
        "record_sha256",
    }:
        raise ValueError("baseline evidence collection has an invalid closed contract")
    if value.get("schema_version") != COLLECTION_SCHEMA:
        raise ValueError("baseline evidence collection schema is invalid")
    _bounded_string(value.get("collection_id"), field="collection_id", maximum=200)
    registry_digest = value.get("registry_sha256")
    if (
        not isinstance(registry_digest, str)
        or len(registry_digest) != 64
        or any(character not in "0123456789abcdef" for character in registry_digest)
    ):
        raise ValueError("baseline evidence collection registry digest is invalid")
    runs = value.get("runs")
    if not isinstance(runs, list) or len(runs) > _MAX_RUNS:
        raise ValueError("baseline evidence collection run inventory is invalid")
    for index, run in enumerate(runs):
        if not isinstance(run, dict) or set(run) != {
            "system_id",
            "plan_path",
            "receipt_path",
        }:
            raise ValueError(f"collection runs[{index}] has an invalid closed contract")
        _bounded_string(
            run.get("system_id"),
            field=f"collection runs[{index}].system_id",
            maximum=100,
        )
        _absolute_path(run.get("plan_path"), field=f"collection runs[{index}].plan_path")
        _absolute_path(
            run.get("receipt_path"),
            field=f"collection runs[{index}].receipt_path",
        )
    _record_digest(
        value,
        digest_field="record_sha256",
        field="baseline evidence collection",
    )
    return value


def _verify_artifact(
    artifact: dict[str, Any],
    *,
    field: str,
    maximum_bytes: int,
) -> Path:
    path = _absolute_path(artifact.get("path_hint"), field=f"{field}.path_hint")
    if path.is_symlink() or not path.is_file():
        raise EvidenceError("artifact_missing", f"{field} is missing or unsafe")
    size = path.stat().st_size
    if (
        not 0 <= size <= maximum_bytes
        or artifact.get("byte_size") != size
        or artifact.get("sha256") != sha256_file(path)
    ):
        raise EvidenceError("artifact_drift", f"{field} bytes differ from the receipt")
    return path


def _verify_run(
    run: dict[str, Any],
    *,
    registry: dict[str, Any],
    registry_path: Path,
    canonical_registry_sha256: str,
) -> ValidRun:
    system_id = run["system_id"]
    try:
        plan_path = _absolute_path(run["plan_path"], field="run plan_path")
        receipt_path = _absolute_path(run["receipt_path"], field="run receipt_path")
        raw_plan = _read_json(plan_path, field="execution plan")
        raw_receipt = _read_json(receipt_path, field="execution receipt")
        manual = raw_plan.get("schema_version") == MANUAL_PLAN_SCHEMA
        if manual:
            plan = validate_manual_execution_plan(raw_plan)
            receipt = validate_manual_execution_receipt(raw_receipt, plan=plan)
        else:
            plan = validate_execution_plan(raw_plan)
            receipt = validate_execution_receipt(raw_receipt, plan=plan)
    except (OSError, RuntimeError, ValueError) as error:
        raise EvidenceError("plan_or_receipt_invalid", str(error)) from error
    if (
        system_id != plan["system"]["system_id"]
        or system_id != receipt["system_id"]
    ):
        raise EvidenceError("system_identity_mismatch", "run system identities differ")
    expected_system = _registry_system(registry, system_id)
    if canonical_json(plan["system"]) != canonical_json(expected_system):
        raise EvidenceError("registry_system_mismatch", "plan system differs from registry")
    if plan["registry"] != {
        "path_hint": str(registry_path),
        "file_sha256": sha256_file(registry_path),
        "canonical_sha256": canonical_registry_sha256,
    }:
        raise EvidenceError("registry_binding_mismatch", "plan registry binding is stale")
    if receipt_path != Path(plan["artifacts"]["receipt_path_hint"]):
        raise EvidenceError("receipt_path_mismatch", "receipt path differs from the plan")
    try:
        if manual:
            _verify_live_manual_plan(plan)
        elif _checkout_binding(Path(plan["checkout"]["path_hint"])) != plan["checkout"]:
            raise EvidenceError("checkout_drift", "checkout differs from the plan")
        if _input_binding(
            Path(plan["corpus"]["path_hint"]),
            field="baseline corpus",
        ) != plan["corpus"]:
            raise EvidenceError("corpus_drift", "corpus differs from the plan")
        if _queries_binding(Path(plan["queries"]["path_hint"])) != plan["queries"]:
            raise EvidenceError("queries_drift", "queries differ from the plan")
        if not manual and _file_binding(
            Path(plan["executable"]["path_hint"]),
            field="baseline command executable",
            allow_symlink=True,
            require_executable=True,
        ) != plan["executable"]:
            raise EvidenceError("executable_drift", "executable differs from the plan")
        if not manual and _file_binding(
            Path(plan["wrapper"]["path_hint"]),
            field="baseline wrapper",
            allow_symlink=False,
        ) != plan["wrapper"]:
            raise EvidenceError("wrapper_drift", "wrapper differs from the plan")
        environment_path = Path(plan["evaluation_environment"]["path_hint"])
        if _validate_evaluation_environment(
            environment_path,
            registry=registry,
            system=expected_system,
        ) != plan["evaluation_environment"]:
            raise EvidenceError(
                "environment_drift",
                "evaluation environment differs from the plan",
            )
        environment = _read_json(
            environment_path,
            field="evaluation environment",
        )
    except EvidenceError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise EvidenceError("frozen_input_drift", str(error)) from error
    if not manual:
        for field, maximum in (
            ("stdout", 4 * 1024 * 1024),
            ("stderr", 4 * 1024 * 1024),
        ):
            _verify_artifact(receipt[field], field=field, maximum_bytes=maximum)
    raw_artifact = receipt["raw_output"]
    if raw_artifact is not None:
        raw_path = _verify_artifact(
            raw_artifact,
            field="raw output",
            maximum_bytes=_MAX_ARTIFACT_BYTES,
        )
        if receipt["output_validation"] == "passed":
            output_report = _validate_output(
                raw_path,
                expected_case_ids=_read_query_case_ids(
                    Path(plan["queries"]["path_hint"])
                ),
            )
            if (
                output_report["raw_output"] != raw_artifact
                or output_report["case_count"] != receipt["output_case_count"]
                or output_report["case_ids_sha256"]
                != receipt["output_case_ids_sha256"]
            ):
                raise EvidenceError(
                    "output_receipt_mismatch",
                    "validated output differs from the receipt",
                )
    resource: dict[str, Any] | None = None
    resource_report: dict[str, Any] | None = None
    resource_artifact = receipt["resource_record"]
    if resource_artifact is not None:
        resource_path = _verify_artifact(
            resource_artifact,
            field="resource record",
            maximum_bytes=4 * 1024 * 1024,
        )
        if receipt["resource_validation"] == "passed":
            resource_report = _validate_resource_record(resource_path, plan=plan)
            if (
                resource_report["artifact"] != resource_artifact
                or resource_report["record_sha256"]
                != receipt["resource_record_sha256"]
            ):
                raise EvidenceError(
                    "resource_receipt_mismatch",
                    "validated resource record differs from the receipt",
                )
            resource = _read_json(resource_path, field="resource record")
    if (
        receipt["output_validation"] == "passed"
        and receipt["resource_validation"] == "passed"
        and raw_artifact is not None
        and resource_report is not None
    ):
        output_report = _validate_output(
            Path(raw_artifact["path_hint"]),
            expected_case_ids=_read_query_case_ids(
                Path(plan["queries"]["path_hint"])
            ),
        )
        if not set(output_report["failed_case_ids"]) <= set(
            resource_report["failure_case_ids"]
        ):
            raise EvidenceError(
                "failure_inventory_mismatch",
                "raw task failures are absent from the resource record",
            )
    if manual:
        manual_artifact = receipt["manual_record"]
        if manual_artifact is not None:
            manual_path = _verify_artifact(
                manual_artifact,
                field="manual workflow record",
                maximum_bytes=4 * 1024 * 1024,
            )
            if receipt["manual_validation"] == "passed":
                manual_report = _validate_manual_record(manual_path, plan=plan)
                if (
                    manual_report["artifact"] != manual_artifact
                    or manual_report["record_sha256"]
                    != receipt["manual_record_sha256"]
                ):
                    raise EvidenceError(
                        "manual_receipt_mismatch",
                        "manual workflow record differs from the receipt",
                    )
    return ValidRun(
        system_id=system_id,
        plan=plan,
        receipt=receipt,
        environment=environment,
        resource=resource,
    )


def _hash_value(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _common(values: list[Any]) -> tuple[Any | None, int]:
    encoded = {canonical_json(value): value for value in values}
    if len(encoded) == 1:
        return next(iter(encoded.values())), 1
    return None, len(encoded)


def _fairness_check(
    name: str,
    *,
    observed_count: int,
    expected_count: int,
    passed: bool,
) -> dict[str, Any]:
    return {
        "check": name,
        "passed": passed,
        "observed_count": observed_count,
        "expected_count": expected_count,
    }


def build_collection_report(
    *,
    registry_path: Path,
    collection_path: Path,
) -> dict[str, Any]:
    selected_registry = registry_path.expanduser().absolute()
    if selected_registry.is_symlink() or not selected_registry.is_file():
        raise ValueError("baseline registry must be a regular non-symlink file")
    registry = load_registry(selected_registry)
    canonical_registry_sha256 = registry_sha256(registry)
    selected_collection = collection_path.expanduser().absolute()
    collection = validate_collection_manifest(
        _read_json(selected_collection, field="baseline evidence collection")
    )
    if collection["registry_sha256"] != canonical_registry_sha256:
        raise ValueError("baseline evidence collection targets a different registry")
    expected_system_ids = sorted(item["system_id"] for item in registry["systems"])
    expected_set = set(expected_system_ids)
    submitted_ids = [run["system_id"] for run in collection["runs"]]
    counts = Counter(submitted_ids)
    duplicate_system_ids = sorted(
        system_id for system_id, count in counts.items() if count > 1
    )
    unexpected_system_ids = sorted(set(submitted_ids) - expected_set)
    invalid_runs: list[dict[str, str]] = []
    valid_runs: list[ValidRun] = []
    for run in collection["runs"]:
        system_id = run["system_id"]
        if counts[system_id] > 1:
            invalid_runs.append(
                {
                    "system_id": system_id,
                    "code": "duplicate_system_id",
                    "reason": "the collection contains more than one run for this system",
                }
            )
            continue
        if system_id not in expected_set:
            invalid_runs.append(
                {
                    "system_id": system_id,
                    "code": "unexpected_system_id",
                    "reason": "the system is not present in the bound registry",
                }
            )
            continue
        try:
            valid_runs.append(
                _verify_run(
                    run,
                    registry=registry,
                    registry_path=selected_registry,
                    canonical_registry_sha256=canonical_registry_sha256,
                )
            )
        except EvidenceError as error:
            invalid_runs.append(
                {
                    "system_id": system_id,
                    "code": error.code,
                    "reason": str(error)[:1_000],
                }
            )
    valid_runs.sort(key=lambda item: item.system_id)
    invalid_runs.sort(key=lambda item: (item["system_id"], item["code"]))
    valid_system_ids = [run.system_id for run in valid_runs]
    successful_runs = [
        run for run in valid_runs if run.receipt["execution_status"] == "succeeded"
    ]
    successful_system_ids = [run.system_id for run in successful_runs]
    missing_system_ids = sorted(expected_set - set(valid_system_ids))
    expected_count = len(expected_system_ids)
    full_population = len(valid_runs) == expected_count and not invalid_runs

    dimensions: dict[str, list[Any]] = {
        "evaluator_run_id": [run.environment["evaluator_run_id"] for run in valid_runs],
        "corpus_sha256": [run.plan["corpus"]["sha256"] for run in valid_runs],
        "queries_sha256": [run.plan["queries"]["sha256"] for run in valid_runs],
        "query_case_ids_sha256": [
            run.plan["queries"]["case_ids_sha256"] for run in valid_runs
        ],
        "hardware": [run.environment["hardware"] for run in valid_runs],
        "reader": [run.environment["reader"] for run in valid_runs],
        "measurement": [run.environment["measurement"] for run in valid_runs],
        "context_token_budget": [
            run.plan["system"]["configuration"]["context_token_budget"]
            for run in valid_runs
        ],
        "top_k": [
            run.plan["system"]["configuration"]["top_k"] for run in valid_runs
        ],
    }
    common_values: dict[str, Any | None] = {}
    distinct_counts: dict[str, int] = {}
    for name, values in dimensions.items():
        common_values[name], distinct_counts[name] = _common(values) if values else (None, 0)
    common_bindings = {
        "evaluator_run_id_sha256": (
            _hash_value(common_values["evaluator_run_id"])
            if common_values["evaluator_run_id"] is not None
            else None
        ),
        "corpus_sha256": common_values["corpus_sha256"],
        "queries_sha256": common_values["queries_sha256"],
        "query_case_ids_sha256": common_values["query_case_ids_sha256"],
        "hardware_sha256": (
            _hash_value(common_values["hardware"])
            if common_values["hardware"] is not None
            else None
        ),
        "reader_sha256": (
            _hash_value(common_values["reader"])
            if common_values["reader"] is not None
            else None
        ),
        "measurement_sha256": (
            _hash_value(common_values["measurement"])
            if common_values["measurement"] is not None
            else None
        ),
        "context_token_budget": common_values["context_token_budget"],
        "top_k": common_values["top_k"],
    }
    fairness_checks: list[dict[str, Any]] = [
        _fairness_check(
            "complete_population",
            observed_count=len(valid_runs),
            expected_count=expected_count,
            passed=full_population,
        )
    ]
    for check, dimension in (
        ("same_evaluator_run", "evaluator_run_id"),
        ("same_corpus", "corpus_sha256"),
        ("same_queries", "queries_sha256"),
        ("same_query_case_inventory", "query_case_ids_sha256"),
        ("same_hardware", "hardware"),
        ("same_reader", "reader"),
        ("same_measurement_protocol", "measurement"),
        ("same_context_token_budget", "context_token_budget"),
        ("same_top_k", "top_k"),
    ):
        fairness_checks.append(
            _fairness_check(
                check,
                observed_count=distinct_counts[dimension],
                expected_count=1,
                passed=full_population and distinct_counts[dimension] == 1,
            )
        )
    query_network_count = sum(
        run.environment["network"]["query_network_disabled"] is True
        and run.environment["network"]["loopback_only_model_services"] is True
        for run in valid_runs
    )
    raw_output_count = sum(run.receipt["raw_output"] is not None for run in valid_runs)
    resource_count = sum(
        run.receipt["resource_validation"] == "passed" and run.resource is not None
        for run in valid_runs
    )
    failure_inventory_count = sum(
        run.resource is not None
        and run.resource["failure_count"] == len(run.resource["failures"])
        for run in valid_runs
    )
    expected_manual_count = sum(
        system["adapter"]["kind"] == "scripted-human"
        for system in registry["systems"]
    )
    retained_manual_count = sum(
        run.plan["system"]["adapter"]["kind"] == "scripted-human"
        and run.receipt.get("manual_validation") == "passed"
        and run.receipt.get("manual_record") is not None
        for run in valid_runs
    )
    for check, observed in (
        ("query_network_control_attested", query_network_count),
        ("raw_outputs_retained", raw_output_count),
        ("resource_records_retained", resource_count),
        ("failure_inventories_retained", failure_inventory_count),
        ("successful_executions", len(successful_runs)),
    ):
        fairness_checks.append(
            _fairness_check(
                check,
                observed_count=observed,
                expected_count=expected_count,
                passed=full_population and observed == expected_count,
            )
        )
    fairness_checks.append(
        _fairness_check(
            "scripted_human_evidence_retained",
            observed_count=retained_manual_count,
            expected_count=max(1, expected_manual_count),
            passed=full_population and retained_manual_count == expected_manual_count,
        )
    )
    collection_complete = all(check["passed"] for check in fairness_checks)
    run_evidence = [
        {
            "system_id": run.system_id,
            "plan_sha256": run.plan["plan_sha256"],
            "receipt_sha256": run.receipt["receipt_sha256"],
            "execution_status": run.receipt["execution_status"],
            "raw_output_sha256": (
                run.receipt["raw_output"]["sha256"]
                if run.receipt["raw_output"] is not None
                else None
            ),
            "resource_record_sha256": run.receipt["resource_record_sha256"],
            "manual_record_sha256": run.receipt.get("manual_record_sha256"),
            "evaluation_environment_record_sha256": run.receipt[
                "evaluation_environment_record_sha256"
            ],
        }
        for run in valid_runs
    ]
    body = {
        "schema_version": COLLECTION_REPORT_SCHEMA,
        "collection_id": collection["collection_id"],
        "collection_manifest_sha256": collection["record_sha256"],
        "registry_file_sha256": sha256_file(selected_registry),
        "registry_sha256": canonical_registry_sha256,
        "expected_system_count": expected_count,
        "submitted_run_count": len(collection["runs"]),
        "valid_run_count": len(valid_runs),
        "successful_run_count": len(successful_runs),
        "required_system_ids": expected_system_ids,
        "valid_system_ids": valid_system_ids,
        "successful_system_ids": successful_system_ids,
        "missing_system_ids": missing_system_ids,
        "unexpected_system_ids": unexpected_system_ids,
        "duplicate_system_ids": duplicate_system_ids,
        "invalid_runs": invalid_runs,
        "run_evidence": run_evidence,
        "common_bindings": common_bindings,
        "fairness_checks": fairness_checks,
        "collection_complete": collection_complete,
        "claim_eligible": False,
        "claim_ineligibility_reason": _CLAIM_INELIGIBILITY_REASON,
    }
    return {
        **body,
        "report_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    selected = path.expanduser().absolute()
    if selected.exists() or selected.is_symlink():
        raise FileExistsError("collection report path must be new")
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_reserved(_reserve_binary(selected), payload)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a fixed-hardware official-baseline evidence collection"
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = build_collection_report(
        registry_path=args.registry,
        collection_path=args.collection,
    )
    _write_json_exclusive(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return int(not report["collection_complete"])


if __name__ == "__main__":
    raise SystemExit(main())

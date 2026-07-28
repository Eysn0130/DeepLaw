from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from benchmarks.baselines.official_adapter import (
    _absolute,
    _closed_dict,
    _file_binding,
    _input_binding,
    _path_hint,
    _queries_binding,
    _read_evidence_json,
    _read_query_case_ids,
    _record_sha256,
    _registry_state,
    _reserve_binary,
    _safe_raw_output_artifact,
    _safe_resource_record_artifact,
    _validate_evaluation_environment,
    _validate_file_binding,
    _validate_input_binding,
    _validate_output,
    _validate_receipt_artifact,
    _validate_record_binding,
    _validate_resource_record,
    _write_json_exclusive,
    _write_reserved,
)
from benchmarks.baselines.registry import default_registry_path, load_registry
from benchmarks.external.benchlib import SCHEMA_RUN, strict_json_loads
from deeplaw.util import canonical_json, sha256_bytes, sha256_file

MANUAL_PLAN_SCHEMA = "deeplaw.manual-baseline-execution-plan/v1"
MANUAL_RECEIPT_SCHEMA = "deeplaw.manual-baseline-execution-receipt/v1"
MANUAL_RUN_SCHEMA = "deeplaw.obsidian-manual-run/v1"
_MAX_MANUAL_RECORD_BYTES = 4 * 1024 * 1024
_MAX_CAPTURE_BYTES = 2**63 - 1
_MAX_CASES = 1_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_INELIGIBILITY_REASON = (
    "a local manual-workflow receipt is not an independently attested held-out comparison"
)


def _registry_system(registry: dict[str, Any], system_id: str) -> dict[str, Any]:
    selected = next(
        (item for item in registry["systems"] if item["system_id"] == system_id),
        None,
    )
    if selected is None:
        raise ValueError(f"baseline system is not registered: {system_id}")
    if selected["adapter"]["kind"] != "scripted-human":
        raise ValueError("manual runner accepts only a scripted-human baseline")
    return selected


def _artifact_paths(
    *,
    output: Path,
    resource_record: Path,
    manual_record: Path,
    receipt: Path,
) -> dict[str, str]:
    values = {
        "raw_output_path_hint": str(_absolute(output, field="manual raw output")),
        "resource_record_path_hint": str(
            _absolute(resource_record, field="manual resource record")
        ),
        "manual_record_path_hint": str(
            _absolute(manual_record, field="manual workflow record")
        ),
        "receipt_path_hint": str(_absolute(receipt, field="manual receipt")),
    }
    paths = [Path(value) for value in values.values()]
    if len(set(paths)) != len(paths):
        raise ValueError("manual baseline artifacts must use distinct paths")
    for path in paths:
        if path.exists() or path.is_symlink():
            raise FileExistsError("manual baseline artifacts must use new paths")
        if path.parent == Path(path.anchor):
            raise ValueError("manual artifacts must not be written at a filesystem root")
    return values


def build_manual_execution_plan(
    *,
    registry: dict[str, Any] | None = None,
    registry_path: Path | None = None,
    system_id: str,
    corpus: Path,
    queries: Path,
    evaluation_environment: Path,
    workflow: Path,
    output: Path,
    resource_record: Path,
    manual_record: Path,
    receipt: Path,
) -> dict[str, Any]:
    selected_registry_path = _absolute(
        registry_path or default_registry_path(),
        field="baseline registry",
    )
    observed_registry, registry_binding = _registry_state(selected_registry_path)
    if registry is not None and canonical_json(registry) != canonical_json(
        observed_registry
    ):
        raise RuntimeError("provided baseline registry differs from its exact path")
    system = _registry_system(observed_registry, system_id)
    corpus_binding = _input_binding(corpus, field="baseline corpus")
    queries_binding = _queries_binding(queries)
    environment_binding = _validate_evaluation_environment(
        evaluation_environment,
        registry=observed_registry,
        system=system,
    )
    workflow_binding = _file_binding(
        workflow,
        field="manual workflow",
        allow_symlink=False,
    )
    expected_workflow = Path(__file__).with_name("obsidian-workflow-v1.md").resolve()
    if Path(workflow_binding["resolved_path"]) != expected_workflow:
        raise ValueError("manual baseline must use the shipped frozen workflow")
    artifacts = _artifact_paths(
        output=output,
        resource_record=resource_record,
        manual_record=manual_record,
        receipt=receipt,
    )
    protected = {
        Path(registry_binding["path_hint"]),
        Path(corpus_binding["path_hint"]),
        Path(queries_binding["path_hint"]),
        Path(environment_binding["path_hint"]),
        Path(workflow_binding["path_hint"]),
    }
    if protected.intersection(Path(value) for value in artifacts.values()):
        raise ValueError("manual artifacts must not overlap frozen inputs")
    body = {
        "schema_version": MANUAL_PLAN_SCHEMA,
        "registry": registry_binding,
        "system": system,
        "implementation_revision": system["implementation"]["revision"],
        "corpus": corpus_binding,
        "queries": queries_binding,
        "evaluation_environment": environment_binding,
        "workflow": workflow_binding,
        "artifacts": artifacts,
        "result_status": "planned_not_executed",
    }
    return {
        **body,
        "plan_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def validate_manual_execution_plan(plan: Any) -> dict[str, Any]:
    value = _closed_dict(
        plan,
        field="manual baseline execution plan",
        keys={
            "schema_version",
            "registry",
            "system",
            "implementation_revision",
            "corpus",
            "queries",
            "evaluation_environment",
            "workflow",
            "artifacts",
            "result_status",
            "plan_sha256",
        },
    )
    if (
        value.get("schema_version") != MANUAL_PLAN_SCHEMA
        or value.get("result_status") != "planned_not_executed"
    ):
        raise ValueError("manual baseline execution plan identity is invalid")
    body = {key: item for key, item in value.items() if key != "plan_sha256"}
    if value.get("plan_sha256") != sha256_bytes(
        canonical_json(body).encode("utf-8")
    ):
        raise ValueError("manual baseline execution plan digest is invalid")
    registry = _closed_dict(
        value.get("registry"),
        field="manual plan registry",
        keys={"path_hint", "file_sha256", "canonical_sha256"},
    )
    _path_hint(registry.get("path_hint"), field="manual plan registry.path_hint")
    if not _SHA256.fullmatch(str(registry.get("file_sha256"))) or not _SHA256.fullmatch(
        str(registry.get("canonical_sha256"))
    ):
        raise ValueError("manual plan registry digests are invalid")
    system = value.get("system")
    if (
        not isinstance(system, dict)
        or system.get("adapter", {}).get("kind") != "scripted-human"
        or value.get("implementation_revision")
        != system.get("implementation", {}).get("revision")
        or not re.fullmatch(
            r"[0-9a-f]{40,64}",
            str(value.get("implementation_revision")),
        )
    ):
        raise ValueError("manual plan system binding is invalid")
    _validate_input_binding(value.get("corpus"), field="manual plan corpus", queries=False)
    _validate_input_binding(value.get("queries"), field="manual plan queries", queries=True)
    _validate_record_binding(
        value.get("evaluation_environment"),
        field="manual plan evaluation environment",
    )
    _validate_file_binding(value.get("workflow"), field="manual plan workflow")
    artifacts = _closed_dict(
        value.get("artifacts"),
        field="manual plan artifacts",
        keys={
            "raw_output_path_hint",
            "resource_record_path_hint",
            "manual_record_path_hint",
            "receipt_path_hint",
        },
    )
    paths = [
        _path_hint(item, field=f"manual plan artifacts.{field}")
        for field, item in artifacts.items()
    ]
    if len(set(paths)) != len(paths):
        raise ValueError("manual plan artifact paths are not distinct")
    return value


def _verify_live_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    registry_path = Path(plan["registry"]["path_hint"])
    registry, binding = _registry_state(registry_path)
    if binding != plan["registry"]:
        raise RuntimeError("manual baseline registry changed after planning")
    system = _registry_system(registry, plan["system"]["system_id"])
    if canonical_json(system) != canonical_json(plan["system"]):
        raise RuntimeError("manual baseline registry entry changed after planning")
    if _input_binding(
        Path(plan["corpus"]["path_hint"]), field="baseline corpus"
    ) != plan["corpus"]:
        raise RuntimeError("manual baseline corpus changed after planning")
    if _queries_binding(Path(plan["queries"]["path_hint"])) != plan["queries"]:
        raise RuntimeError("manual baseline queries changed after planning")
    if _validate_evaluation_environment(
        Path(plan["evaluation_environment"]["path_hint"]),
        registry=registry,
        system=system,
    ) != plan["evaluation_environment"]:
        raise RuntimeError("manual baseline environment changed after planning")
    if _file_binding(
        Path(plan["workflow"]["path_hint"]),
        field="manual workflow",
        allow_symlink=False,
    ) != plan["workflow"]:
        raise RuntimeError("manual baseline workflow changed after planning")
    return registry, system


def _finite_fraction(value: Any, *, field: str) -> float | int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{field} must be a finite fraction")
    return value


def _finite_nonnegative(value: Any, *, field: str) -> float | int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be a finite non-negative number")
    return value


def _capture_artifact(value: Any, *, field: str) -> dict[str, Any]:
    artifact = _closed_dict(
        value,
        field=field,
        keys={"path_hint", "sha256", "byte_size"},
    )
    path = _path_hint(artifact.get("path_hint"), field=f"{field}.path_hint")
    if not _SHA256.fullmatch(str(artifact.get("sha256"))):
        raise ValueError(f"{field}.sha256 is invalid")
    size = artifact.get("byte_size")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 1 <= size <= _MAX_CAPTURE_BYTES
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != size
        or sha256_file(path) != artifact["sha256"]
    ):
        raise ValueError(f"{field} bytes are missing, unsafe, or changed")
    return artifact


def _validate_manual_record(path: Path, *, plan: dict[str, Any]) -> dict[str, Any]:
    selected, record = _read_evidence_json(path, field="manual workflow record")
    _closed_dict(
        record,
        field="manual workflow record",
        keys={
            "schema_version",
            "system_id",
            "implementation_revision",
            "registry_sha256",
            "corpus_sha256",
            "queries_sha256",
            "query_case_ids_sha256",
            "evaluation_environment_record_sha256",
            "workflow_sha256",
            "screen_recording",
            "vault_before_archive",
            "vault_after_archive",
            "case_count",
            "cases",
            "record_sha256",
        },
    )
    expected = {
        "schema_version": MANUAL_RUN_SCHEMA,
        "system_id": plan["system"]["system_id"],
        "implementation_revision": plan["implementation_revision"],
        "registry_sha256": plan["registry"]["canonical_sha256"],
        "corpus_sha256": plan["corpus"]["sha256"],
        "queries_sha256": plan["queries"]["sha256"],
        "query_case_ids_sha256": plan["queries"]["case_ids_sha256"],
        "evaluation_environment_record_sha256": plan["evaluation_environment"][
            "record_sha256"
        ],
        "workflow_sha256": plan["workflow"]["sha256"],
        "case_count": plan["queries"]["case_count"],
    }
    if any(record.get(field) != value for field, value in expected.items()):
        raise ValueError("manual workflow record binding is invalid")
    for field in ("screen_recording", "vault_before_archive", "vault_after_archive"):
        _capture_artifact(record.get(field), field=f"manual record {field}")
    cases = record.get("cases")
    if not isinstance(cases, list) or len(cases) != plan["queries"]["case_count"]:
        raise ValueError("manual workflow case inventory is invalid")
    expected_case_ids = set(_read_query_case_ids(Path(plan["queries"]["path_hint"])))
    observed: set[str] = set()
    normalized_cases: dict[str, dict[str, Any]] = {}
    for index, case in enumerate(cases):
        _closed_dict(
            case,
            field=f"manual cases[{index}]",
            keys={
                "case_id",
                "task_success",
                "useful_context_recall",
                "irrelevant_context_rate",
                "source_provenance_coverage",
                "span_provenance_coverage",
                "stale_leakage",
                "context_tokens",
                "indexing_seconds",
                "query_seconds",
                "operator_seconds",
                "source_locator_sha256",
                "failure_kind",
                "failure_detail_sha256",
            },
        )
        case_id = case.get("case_id")
        if (
            not isinstance(case_id, str)
            or not case_id
            or len(case_id) > 500
            or case_id in observed
            or case_id not in expected_case_ids
        ):
            raise ValueError(f"manual cases[{index}].case_id is invalid")
        if not isinstance(case.get("task_success"), bool) or not isinstance(
            case.get("stale_leakage"), bool
        ):
            raise ValueError(f"manual cases[{index}] outcome fields are invalid")
        for field in (
            "useful_context_recall",
            "irrelevant_context_rate",
            "source_provenance_coverage",
            "span_provenance_coverage",
        ):
            _finite_fraction(case.get(field), field=f"manual cases[{index}].{field}")
        for field in ("indexing_seconds", "query_seconds", "operator_seconds"):
            _finite_nonnegative(case.get(field), field=f"manual cases[{index}].{field}")
        tokens = case.get("context_tokens")
        if (
            isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or not 0 <= tokens <= plan["system"]["configuration"][
                "context_token_budget"
            ]
        ):
            raise ValueError(f"manual cases[{index}].context_tokens is invalid")
        locator_digest = case.get("source_locator_sha256")
        if locator_digest is not None and not _SHA256.fullmatch(str(locator_digest)):
            raise ValueError(f"manual cases[{index}] locator digest is invalid")
        failure_kind = case.get("failure_kind")
        failure_digest = case.get("failure_detail_sha256")
        if case["task_success"]:
            if failure_kind is not None or failure_digest is not None:
                raise ValueError("successful manual case must not report a failure")
        elif failure_kind not in {"error", "timeout", "abstention"} or not (
            _SHA256.fullmatch(str(failure_digest))
        ):
            raise ValueError("failed manual case must bind its failure detail")
        observed.add(case_id)
        normalized_cases[case_id] = case
    if observed != expected_case_ids:
        raise ValueError("manual workflow cases do not cover the frozen query inventory")
    return {
        "artifact": {
            "path_hint": str(selected),
            "sha256": sha256_file(selected),
            "byte_size": selected.stat().st_size,
        },
        "record_sha256": _record_sha256(record, field="manual workflow record"),
        "cases": normalized_cases,
    }


def _output_outcomes(path: Path) -> dict[str, bool | None]:
    outcomes: dict[str, bool | None] = {}
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            try:
                record = strict_json_loads(raw_line)
            except (UnicodeDecodeError, ValueError) as error:
                raise ValueError(
                    f"manual raw output line {line_number} is invalid"
                ) from error
            if (
                not isinstance(record, dict)
                or record.get("schema_version") != SCHEMA_RUN
                or not isinstance(record.get("case_id"), str)
            ):
                raise ValueError("manual raw output outcome record is invalid")
            outcomes[record["case_id"]] = record.get("task_success")
    return outcomes


def _manual_receipt(
    *,
    plan: dict[str, Any],
    status: str,
    raw_output: dict[str, Any] | None,
    resource_record: dict[str, Any] | None,
    resource_record_sha256: str | None,
    manual_record: dict[str, Any] | None,
    manual_record_sha256: str | None,
    output_case_count: int | None,
    output_case_ids_sha256: str | None,
    output_validation: str,
    resource_validation: str,
    manual_validation: str,
    failure_reason: str | None,
) -> dict[str, Any]:
    body = {
        "schema_version": MANUAL_RECEIPT_SCHEMA,
        "plan_sha256": plan["plan_sha256"],
        "registry_sha256": plan["registry"]["canonical_sha256"],
        "system_id": plan["system"]["system_id"],
        "implementation_revision": plan["implementation_revision"],
        "evaluation_environment_record_sha256": plan["evaluation_environment"][
            "record_sha256"
        ],
        "execution_status": status,
        "raw_output": raw_output,
        "resource_record": resource_record,
        "resource_record_sha256": resource_record_sha256,
        "manual_record": manual_record,
        "manual_record_sha256": manual_record_sha256,
        "query_case_count": plan["queries"]["case_count"],
        "query_case_ids_sha256": plan["queries"]["case_ids_sha256"],
        "output_case_count": output_case_count,
        "output_case_ids_sha256": output_case_ids_sha256,
        "output_validation": output_validation,
        "resource_validation": resource_validation,
        "manual_validation": manual_validation,
        "failure_reason": failure_reason,
        "claim_eligible": False,
        "claim_ineligibility_reason": _CLAIM_INELIGIBILITY_REASON,
    }
    return {
        **body,
        "receipt_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def validate_manual_execution_receipt(
    receipt: Any,
    *,
    plan: dict[str, Any],
) -> dict[str, Any]:
    value = _closed_dict(
        receipt,
        field="manual baseline execution receipt",
        keys={
            "schema_version",
            "plan_sha256",
            "registry_sha256",
            "system_id",
            "implementation_revision",
            "evaluation_environment_record_sha256",
            "execution_status",
            "raw_output",
            "resource_record",
            "resource_record_sha256",
            "manual_record",
            "manual_record_sha256",
            "query_case_count",
            "query_case_ids_sha256",
            "output_case_count",
            "output_case_ids_sha256",
            "output_validation",
            "resource_validation",
            "manual_validation",
            "failure_reason",
            "claim_eligible",
            "claim_ineligibility_reason",
            "receipt_sha256",
        },
    )
    validated_plan = validate_manual_execution_plan(plan)
    if value.get("schema_version") != MANUAL_RECEIPT_SCHEMA:
        raise ValueError("manual receipt schema is invalid")
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value.get("receipt_sha256") != sha256_bytes(
        canonical_json(body).encode("utf-8")
    ):
        raise ValueError("manual receipt digest is invalid")
    expected = {
        "plan_sha256": validated_plan["plan_sha256"],
        "registry_sha256": validated_plan["registry"]["canonical_sha256"],
        "system_id": validated_plan["system"]["system_id"],
        "implementation_revision": validated_plan["implementation_revision"],
        "evaluation_environment_record_sha256": validated_plan[
            "evaluation_environment"
        ]["record_sha256"],
        "query_case_count": validated_plan["queries"]["case_count"],
        "query_case_ids_sha256": validated_plan["queries"]["case_ids_sha256"],
    }
    if any(value.get(field) != expected_value for field, expected_value in expected.items()):
        raise ValueError("manual receipt does not bind its plan")
    artifacts = validated_plan["artifacts"]
    raw_output = _validate_receipt_artifact(
        value.get("raw_output"),
        field="manual receipt raw_output",
        maximum_bytes=512 * 1024 * 1024,
        nullable=True,
    )
    resource_record = _validate_receipt_artifact(
        value.get("resource_record"),
        field="manual receipt resource_record",
        maximum_bytes=4 * 1024 * 1024,
        nullable=True,
    )
    manual_record = _validate_receipt_artifact(
        value.get("manual_record"),
        field="manual receipt manual_record",
        maximum_bytes=_MAX_MANUAL_RECORD_BYTES,
        nullable=True,
    )
    for field, artifact, path_key in (
        ("raw_output", raw_output, "raw_output_path_hint"),
        ("resource_record", resource_record, "resource_record_path_hint"),
        ("manual_record", manual_record, "manual_record_path_hint"),
    ):
        if artifact is not None and artifact["path_hint"] != artifacts[path_key]:
            raise ValueError(f"manual receipt {field} path differs from the plan")
    status = value.get("execution_status")
    states = {
        "output_invalid": ("failed", {"passed", "failed"}, {"passed", "failed"}),
        "resource_invalid": ("passed", {"failed"}, {"passed", "failed"}),
        "manual_record_invalid": ("passed", {"passed"}, {"failed"}),
        "succeeded": ("passed", {"passed"}, {"passed"}),
    }
    if status not in states:
        raise ValueError("manual receipt status is invalid")
    output_state, resource_states, manual_states = states[status]
    if (
        value.get("output_validation") != output_state
        or value.get("resource_validation") not in resource_states
        or value.get("manual_validation") not in manual_states
    ):
        raise ValueError("manual receipt validation state is inconsistent")
    if status == "succeeded" and value.get("failure_reason") is not None:
        raise ValueError("successful manual receipt has a failure reason")
    if status != "succeeded" and (
        not isinstance(value.get("failure_reason"), str)
        or not 1 <= len(value["failure_reason"]) <= 1_000
    ):
        raise ValueError("failed manual receipt lacks a bounded reason")
    if value.get("output_validation") == "passed":
        if (
            raw_output is None
            or value.get("output_case_count") != value["query_case_count"]
            or value.get("output_case_ids_sha256")
            != value["query_case_ids_sha256"]
        ):
            raise ValueError("manual receipt output coverage is inconsistent")
    elif value.get("output_case_count") is not None or value.get(
        "output_case_ids_sha256"
    ) is not None:
        raise ValueError("invalid manual output must not report validated coverage")
    for state_field, artifact, digest_field in (
        ("resource_validation", resource_record, "resource_record_sha256"),
        ("manual_validation", manual_record, "manual_record_sha256"),
    ):
        if value[state_field] == "passed":
            if artifact is None or not _SHA256.fullmatch(str(value.get(digest_field))):
                raise ValueError(f"passed {state_field} lacks its record binding")
        elif value.get(digest_field) is not None:
            raise ValueError(f"failed {state_field} exposes a canonical record digest")
    if (
        value.get("claim_eligible") is not False
        or value.get("claim_ineligibility_reason") != _CLAIM_INELIGIBILITY_REASON
    ):
        raise ValueError("manual receipt overstates claim eligibility")
    return value


def seal_manual_execution(plan: dict[str, Any]) -> dict[str, Any]:
    validated = validate_manual_execution_plan(plan)
    _verify_live_plan(validated)
    artifacts = validated["artifacts"]
    output_path = Path(artifacts["raw_output_path_hint"])
    resource_path = Path(artifacts["resource_record_path_hint"])
    manual_path = Path(artifacts["manual_record_path_hint"])
    receipt_path = Path(artifacts["receipt_path_hint"])
    if receipt_path.exists() or receipt_path.is_symlink():
        raise FileExistsError("manual receipt path must still be new")
    output_report: dict[str, Any] | None = None
    resource_report: dict[str, Any] | None = None
    manual_report: dict[str, Any] | None = None
    errors: dict[str, str] = {}
    try:
        output_report = _validate_output(
            output_path,
            expected_case_ids=_read_query_case_ids(
                Path(validated["queries"]["path_hint"])
            ),
        )
    except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as error:
        errors["output"] = str(error)[:1_000]
    try:
        resource_report = _validate_resource_record(resource_path, plan=validated)
    except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as error:
        errors["resource"] = str(error)[:1_000]
    try:
        manual_report = _validate_manual_record(manual_path, plan=validated)
    except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as error:
        errors["manual"] = str(error)[:1_000]
    if output_report is not None and manual_report is not None:
        outcomes = _output_outcomes(output_path)
        expected_outcomes = {
            case_id: case["task_success"]
            for case_id, case in manual_report["cases"].items()
        }
        if outcomes != expected_outcomes:
            errors["manual"] = "manual case outcomes differ from the raw output"
            manual_report = None
    if resource_report is not None and manual_report is not None:
        resource = strict_json_loads(resource_path.read_bytes())
        failed_cases = {
            case_id
            for case_id, case in manual_report["cases"].items()
            if not case["task_success"]
        }
        retained_failures = {
            failure["case_id"]
            for failure in resource["failures"]
            if failure["case_id"] is not None
        }
        if not failed_cases <= retained_failures:
            errors["manual"] = "manual task failures are absent from the resource record"
            manual_report = None
    if output_report is None:
        status = "output_invalid"
    elif resource_report is None:
        status = "resource_invalid"
    elif manual_report is None:
        status = "manual_record_invalid"
    else:
        status = "succeeded"
    failure_reason = None
    if errors:
        failure_reason = "; ".join(
            f"{field}: {message}" for field, message in sorted(errors.items())
        )[:1_000]
    receipt = _manual_receipt(
        plan=validated,
        status=status,
        raw_output=(
            output_report["raw_output"]
            if output_report is not None
            else _safe_raw_output_artifact(output_path)
        ),
        resource_record=(
            resource_report["artifact"]
            if resource_report is not None
            else _safe_resource_record_artifact(resource_path)
        ),
        resource_record_sha256=(
            resource_report["record_sha256"] if resource_report is not None else None
        ),
        manual_record=(
            manual_report["artifact"]
            if manual_report is not None
            else _safe_resource_record_artifact(manual_path)
        ),
        manual_record_sha256=(
            manual_report["record_sha256"] if manual_report is not None else None
        ),
        output_case_count=(
            output_report["case_count"] if output_report is not None else None
        ),
        output_case_ids_sha256=(
            output_report["case_ids_sha256"] if output_report is not None else None
        ),
        output_validation="passed" if output_report is not None else "failed",
        resource_validation="passed" if resource_report is not None else "failed",
        manual_validation="passed" if manual_report is not None else "failed",
        failure_reason=failure_reason,
    )
    validate_manual_execution_receipt(receipt, plan=validated)
    _write_reserved(
        _reserve_binary(receipt_path),
        (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return receipt


def _plan_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("plan", help="write a pre-run manual evidence plan")
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    parser.add_argument("--system-id", default="obsidian-native")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--evaluation-environment", type=Path, required=True)
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(__file__).with_name("obsidian-workflow-v1.md"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resource-record", type=Path, required=True)
    parser.add_argument("--manual-record", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan or seal the pinned Obsidian scripted-human baseline"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _plan_parser(subparsers)
    seal_parser = subparsers.add_parser("seal", help="validate and seal retained evidence")
    seal_parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        registry_path = args.registry.expanduser().absolute()
        result = build_manual_execution_plan(
            registry=load_registry(registry_path),
            registry_path=registry_path,
            system_id=args.system_id,
            corpus=args.corpus.expanduser().absolute(),
            queries=args.queries.expanduser().absolute(),
            evaluation_environment=args.evaluation_environment.expanduser().absolute(),
            workflow=args.workflow.expanduser().absolute(),
            output=args.output.expanduser().absolute(),
            resource_record=args.resource_record.expanduser().absolute(),
            manual_record=args.manual_record.expanduser().absolute(),
            receipt=args.receipt.expanduser().absolute(),
        )
        _write_json_exclusive(args.plan, result)
        exit_code = 0
    else:
        result = seal_manual_execution(
            validate_manual_execution_plan(
                strict_json_loads(args.plan.expanduser().absolute().read_bytes())
            )
        )
        exit_code = int(result["execution_status"] != "succeeded")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

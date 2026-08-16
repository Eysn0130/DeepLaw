"""Additive provenance contracts for the fail-closed v0.13 release path."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release.provenance_gate_result import (
    ProvenanceGateResultError,
    canonical_json,
    result_sha256,
    validate_gate_result,
)
from benchmarks.release.semantic_evidence import SemanticEvidenceError, validate_report

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACTS = REPOSITORY / "contracts"
CLASSIFICATION_PATH = (
    REPOSITORY / "benchmarks/release/v013-gate-classification-v3.json"
)
GATE_RESULT_SCHEMA_VERSION = "deeplaw.provenance-bound-gate-result/v2"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_provenance_contracts_and_development_classification_are_closed() -> None:
    schemas = [
        _load(CONTRACTS / "provenance-bound-gate-result.v2.schema.json"),
        _load(CONTRACTS / "commercial-evidence-report.v3.schema.json"),
        _load(CONTRACTS / "v013-release-gate-classification.v3.schema.json"),
    ]
    for schema in schemas:
        Draft202012Validator.check_schema(schema)

    classification = _load(CLASSIFICATION_PATH)
    Draft202012Validator(schemas[2]).validate(classification)
    assert classification["assembly_policy"] == {
        "assembly_enabled": False,
        "reason_code": "blocked_missing_validator",
    }


def test_every_core_gate_maps_raw_inputs_but_remains_not_executed() -> None:
    classification = _load(CLASSIFICATION_PATH)
    gates = {
        row["gate_id"]: row
        for row in classification["gates"]  # type: ignore[index]
    }
    core_gate_ids = {
        row["gate_id"]
        for row in classification["gates"]  # type: ignore[index]
        if row["category"] == "Core"
    }
    capability_gate_ids = {
        row["gate_id"]
        for row in classification["gates"]  # type: ignore[index]
        if row["category"] == "Capability"
    }
    competitive_gate_ids = {
        row["gate_id"]
        for row in classification["gates"]  # type: ignore[index]
        if row["category"] == "Competitive Claim"
    }
    assert set(gates) == core_gate_ids | capability_gate_ids | competitive_gate_ids
    assert "timeline" in capability_gate_ids
    for gate_id in core_gate_ids:
        gate = gates[gate_id]
        assert gate["category"] == "Core"
        assert gate["required"] is True
        assert gate["assembly_enabled"] is False
        assert gate["implementation_status"] in {
            "blocked_missing_raw_contract",
            "blocked_missing_validator",
        }
        assert gate["accepted_input_schema_versions"]
        assert GATE_RESULT_SCHEMA_VERSION not in gate["accepted_input_schema_versions"]
        assert gate["validator_id"]
        assert gate["validator_version"]
        assert gate["artifact_kinds"]
        assert gate["hard_zero_derivation"]["source_field"] == (
            "gate_result.hard_failures"
        )


def test_codex_gate_freezes_exact_host_model_runner_and_distinct_runs() -> None:
    classification = _load(CLASSIFICATION_PATH)
    codex = next(
        row
        for row in classification["gates"]  # type: ignore[index]
        if row["gate_id"] == "codex"
    )
    assert codex["minimum_distinct_run_count"] == 3
    assert codex["required_unique_dimensions"] == [
        "run_id",
        "host",
        "model",
        "platform",
        "task_case",
    ]
    assert codex["constraints"] == {
        "host": "codex",
        "tool_version": "0.147.0-alpha.1.2",
        "model_id": "gpt-5.6-luna",
        "argv_prefix": ["codex", "app-server", "--stdio"],
    }
    assert "deeplaw.host-continuity-qualification/v1" in (
        codex["accepted_input_schema_versions"]
    )


def test_deferred_and_competitive_gates_are_not_claimed_not_passed() -> None:
    classification = _load(CLASSIFICATION_PATH)
    for gate in classification["gates"]:  # type: ignore[index]
        if gate["category"] == "Core":
            continue
        assert gate["not_claimed_only"] is True
        assert gate["implementation_status"] == "not_claimed_only"
        assert gate["assembly_enabled"] is False
        assert gate["accepted_input_schema_versions"] == []


def test_classification_contract_cannot_enable_assembly() -> None:
    schema = _load(CONTRACTS / "v013-release-gate-classification.v3.schema.json")
    classification = _load(CLASSIFICATION_PATH)

    enabled = deepcopy(classification)
    enabled["assembly_policy"]["assembly_enabled"] = True  # type: ignore[index]
    assert list(Draft202012Validator(schema).iter_errors(enabled))


def test_v3_report_references_gate_results_instead_of_embedding_observations() -> None:
    schema = _load(CONTRACTS / "commercial-evidence-report.v3.schema.json")
    gate_reference = schema["$defs"]["gateResultReference"]  # type: ignore[index]
    assert set(gate_reference["required"]) == {"gate_id", "category", "result"}
    report_properties = set(schema["properties"])  # type: ignore[arg-type]
    assert {
        "observations",
        "argv",
        "exit_code",
        "run_count",
        "model_id",
        "redaction",
        "hard_zero",
        "passed",
        "release_ready",
        "claim_eligible",
    }.isdisjoint(report_properties)


def test_v3_aggregation_remains_disabled_until_every_core_validator_is_ready() -> None:
    with pytest.raises(SemanticEvidenceError, match="aggregation is disabled"):
        validate_report({"schema_version": "deeplaw.commercial-evidence-report/v3"})


def test_gate_result_requires_file_and_record_provenance_for_every_raw_input() -> None:
    schema = _load(CONTRACTS / "provenance-bound-gate-result.v2.schema.json")
    input_provenance = schema["$defs"]["inputProvenance"]  # type: ignore[index]
    assert set(input_provenance["required"]) == {
        "input_id",
        "relative_path",
        "byte_size",
        "file_sha256",
        "schema_version",
        "record_sha256",
        "artifact_kind",
    }
    assert schema["additionalProperties"] is False
    assert input_provenance["additionalProperties"] is False
    assert set(schema["$defs"]["validatorFileBinding"]["required"]) == {
        "relative_path",
        "byte_size",
        "file_sha256",
    }
    assert input_provenance["properties"]["byte_size"]["maximum"] == 64 * 1024 * 1024
    assert (
        schema["$defs"]["validatorFileBinding"]["properties"]["byte_size"]["maximum"]
        == 64 * 1024 * 1024
    )
    assert {"executions", "run_ids", "metrics", "hard_failures", "redaction"} <= set(
        schema["required"]
    )
    execution = schema["$defs"]["execution"]  # type: ignore[index]
    assert {
        "run_id",
        "input_refs",
        "argv",
        "exit_code",
        "os_name",
        "os_version",
        "python_version",
        "tool_name",
        "tool_version",
        "model_id",
        "dimensions",
    } == set(execution["required"])
    redaction = schema["$defs"]["redaction"]  # type: ignore[index]
    assert "input_refs" in redaction["required"]


def _write_input(root: Path, name: str, schema_version: str) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": schema_version,
        "artifact_kind": "test-input",
        "payload": name,
    }
    body["record_sha256"] = hashlib.sha256(
        canonical_json(body).encode("utf-8")
    ).hexdigest()
    path = root / name
    raw = canonical_json(body).encode("utf-8")
    path.write_bytes(raw)
    return {
        "input_id": f"input-{name[:-5]}",
        "relative_path": name,
        "byte_size": len(raw),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "schema_version": schema_version,
        "record_sha256": body["record_sha256"],
        "artifact_kind": "test-input",
    }


def _valid_gate_result(root: Path) -> dict[str, object]:
    source = root / "validator.py"
    executable = root / "validator.bin"
    source.write_bytes(b"validator source bytes\n")
    executable.write_bytes(b"validator executable bytes\n")
    input_record = _write_input(root, "run.json", "test.raw.v1")
    result: dict[str, object] = {
        "schema_version": GATE_RESULT_SCHEMA_VERSION,
        "gate_id": "test_gate",
        "category": "Core",
        "validator_id": "test.validator",
        "validator_version": "0.1.0",
        "validator_source": {
            "relative_path": "validator.py",
            "byte_size": source.stat().st_size,
            "file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "validator_executable": {
            "relative_path": "validator.bin",
            "byte_size": executable.stat().st_size,
            "file_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        },
        "classification_binding": {
            "classification_id": "test.classification",
            "classification_schema_version": "deeplaw.v013-release-gate-classification/v3",
            "classification_sha256": "a" * 64,
        },
        "candidate_binding": {
            "candidate_commit": "b" * 40,
            "candidate_tree": "c" * 40,
            "candidate_wheel_sha256": "d" * 64,
            "candidate_sdist_sha256": "e" * 64,
        },
        "protocol_binding": {
            "protocol_id": "test.protocol",
            "protocol_sha256": "f" * 64,
            "frozen": True,
        },
        "threshold_binding": {
            "threshold_id": "test.threshold",
            "threshold_sha256": "1" * 64,
            "frozen": True,
        },
        "gold_binding": {
            "gold_sha256": "2" * 64,
            "role": "development_gold",
            "source": "repository",
            "frozen": True,
        },
        "corpus": {
            "role": "development",
            "source": "repository",
            "sha256": "3" * 64,
            "frozen": True,
        },
        "status": "passed",
        "executions": [
            {
                "run_id": "run-1",
                "input_refs": [input_record["input_id"]],
                "argv": ["validator", "run"],
                "exit_code": 0,
                "os_name": "linux",
                "os_version": "test",
                "python_version": "3.12",
                "tool_name": "test-tool",
                "tool_version": "1.0",
                "model_id": None,
                "dimensions": {"host": "test", "platform": "linux"},
            }
        ],
        "run_ids": ["run-1"],
        "unique_dimensions": [
            {"dimension": "host", "values": ["test"]},
            {"dimension": "platform", "values": ["linux"]},
            {"dimension": "run_id", "values": ["run-1"]},
        ],
        "metrics": [
            {
                "metric": "accuracy",
                "observed": 1.0,
                "minimum": 0.0,
                "maximum": 1.0,
                "input_refs": [input_record["input_id"]],
            }
        ],
        "hard_failures": [
            {
                "failure_id": "none",
                "count": 0,
                "maximum_allowed": 0,
                "input_refs": [input_record["input_id"]],
            }
        ],
        "failures": [
            {
                "failure_id": "none-soft",
                "severity": "soft",
                "reason_code": "none",
                "input_refs": [input_record["input_id"]],
            }
        ],
        "redaction": {
            "secret_canary_count": 0,
            "private_path_count": 0,
            "output_redacted": True,
            "input_refs": [input_record["input_id"]],
        },
        "inputs": [input_record],
    }
    result["result_sha256"] = result_sha256(result)
    return result


def test_candidate_gate_result_validator_rechecks_envelope_and_bound_files(tmp_path: Path) -> None:
    result = _valid_gate_result(tmp_path)

    assert validate_gate_result(result, root=tmp_path) == result


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value["protocol_binding"].update(frozen=False),
        lambda value: value["metrics"][0].update(observed=float("nan")),
        lambda value: value["run_ids"].append("run-2"),
        lambda value: value["unique_dimensions"][0].update(values=["tampered"]),
        lambda value: value["executions"][0]["input_refs"].clear(),
    ),
)
def test_candidate_gate_result_validator_rejects_derived_or_frozen_tampering(
    tmp_path: Path,
    mutate,
) -> None:
    result = deepcopy(_valid_gate_result(tmp_path))
    mutate(result)
    result["result_sha256"] = result_sha256(result)

    with pytest.raises(ProvenanceGateResultError):
        validate_gate_result(result, root=tmp_path)


def test_candidate_gate_result_validator_rejects_unconsumed_or_drifted_input(
    tmp_path: Path,
) -> None:
    result = _valid_gate_result(tmp_path)
    extra = _write_input(tmp_path, "extra.json", "test.raw.v1")
    result["inputs"].append(extra)
    result["result_sha256"] = result_sha256(result)
    with pytest.raises(ProvenanceGateResultError, match="every declared input"):
        validate_gate_result(result, root=tmp_path)

    result = _valid_gate_result(tmp_path)
    Path(tmp_path / "validator.py").write_bytes(b"drifted\n")
    with pytest.raises(ProvenanceGateResultError, match="byte binding"):
        validate_gate_result(result, root=tmp_path)


def test_candidate_gate_result_validator_rejects_input_artifact_kind_mismatch(
    tmp_path: Path,
) -> None:
    result = _valid_gate_result(tmp_path)
    result["inputs"][0]["artifact_kind"] = "different-kind"
    result["result_sha256"] = result_sha256(result)

    with pytest.raises(ProvenanceGateResultError, match="artifact kind differs"):
        validate_gate_result(result, root=tmp_path)

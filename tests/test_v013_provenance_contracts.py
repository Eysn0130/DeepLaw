"""Additive provenance contracts for the fail-closed v0.13 release path."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release import release_policy
from benchmarks.release.semantic_evidence import SemanticEvidenceError, validate_report

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACTS = REPOSITORY / "contracts"
CLASSIFICATION_PATH = (
    REPOSITORY / "benchmarks/release/v013-gate-classification-v2.json"
)
GATE_RESULT_SCHEMA_VERSION = "deeplaw.provenance-bound-gate-result/v1"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_provenance_contracts_and_development_classification_are_closed() -> None:
    schemas = [
        _load(CONTRACTS / "provenance-bound-gate-result.v1.schema.json"),
        _load(CONTRACTS / "commercial-evidence-report.v2.schema.json"),
        _load(CONTRACTS / "v013-release-gate-classification.v2.schema.json"),
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
    assert set(gates) == (
        release_policy.V013_CORE_GATE_IDS
        | release_policy.V013_CAPABILITY_GATE_IDS
        | release_policy.V013_COMPETITIVE_GATE_IDS
    )
    for gate_id in release_policy.V013_CORE_GATE_IDS:
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
        "tool_version": "0.145.0",
        "model_id": "gpt-5.6-luna",
        "argv_prefix": ["codex", "exec", "--ephemeral"],
    }
    assert "deeplaw.real-semantic-host-report/v2" in (
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
    schema = _load(CONTRACTS / "v013-release-gate-classification.v2.schema.json")
    classification = _load(CLASSIFICATION_PATH)

    enabled = deepcopy(classification)
    enabled["assembly_policy"]["assembly_enabled"] = True  # type: ignore[index]
    assert list(Draft202012Validator(schema).iter_errors(enabled))


def test_v2_report_references_gate_results_instead_of_embedding_observations() -> None:
    schema = _load(CONTRACTS / "commercial-evidence-report.v2.schema.json")
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


def test_v2_aggregation_remains_disabled_until_every_core_validator_is_ready() -> None:
    with pytest.raises(SemanticEvidenceError, match="aggregation is disabled"):
        validate_report({"schema_version": "deeplaw.commercial-evidence-report/v2"})


def test_gate_result_requires_file_and_record_provenance_for_every_raw_input() -> None:
    schema = _load(CONTRACTS / "provenance-bound-gate-result.v1.schema.json")
    input_provenance = schema["$defs"]["inputProvenance"]  # type: ignore[index]
    assert set(input_provenance["required"]) == {
        "relative_path",
        "byte_size",
        "file_sha256",
        "schema_version",
        "record_sha256",
        "artifact_kind",
    }
    assert schema["additionalProperties"] is False
    assert input_provenance["additionalProperties"] is False
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
    } == set(execution["required"])
    redaction = schema["$defs"]["redaction"]  # type: ignore[index]
    assert "input_refs" in redaction["required"]

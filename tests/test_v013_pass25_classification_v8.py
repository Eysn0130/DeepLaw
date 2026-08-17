from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

REPOSITORY = Path(__file__).resolve().parents[1]
CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v8.json"
PROTOCOL = REPOSITORY / "benchmarks/v013/qualification-protocol-v2.json"
SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v8.schema.json"
HISTORICAL_CLASSIFICATION = (
    REPOSITORY / "benchmarks/release/v013-gate-classification-v7.json"
)

CORE_GATE_IDS = {
    "canonical_integrity",
    "migration_recovery",
    "secret_host_isolation",
    "bounded_context",
    "legal_evidence",
    "source_citation_locator",
    "scale_performance",
    "supported_platforms",
    "reproducible_supply_chain",
    "machine_reference_isolation",
    "codex",
    "opencode",
    "selective_forget",
    "timeline",
}
NON_CORE_GATE_IDS = {
    "semantic_restore",
    "claude",
    "comparative_incremental_benefit",
    "superiority",
    "sota",
}

EXPECTED_ARTIFACTS = {
    "canonical_integrity": "exact_wheel_execution",
    "migration_recovery": "candidate_full_junit",
    "secret_host_isolation": "host_event_sequence",
    "bounded_context": "context_capsule_selection_usage",
    "legal_evidence": "legal_rows",
    "source_citation_locator": "legal_rows",
    "scale_performance": "scale_report",
    "supported_platforms": "candidate_platform_receipt",
    "reproducible_supply_chain": "retained_supply_chain",
    "machine_reference_isolation": "machine_reference_scorer",
    "codex": "host_event_sequence",
    "opencode": "host_event_sequence",
    "selective_forget": "wiki_journey_rows",
    "timeline": "host_event_sequence",
}

CODEX_CONSTRAINTS = {
    "host": "codex",
    "tool_version": "codex-cli 0.148.0-alpha.9",
    "binary_sha256": "6170ff5578170ee9b74ad92bfcff96e6186f41d02b60815a7c2b01ad424c754f",
    "source_commit": None,
    "config_selector": None,
    "model_id": "gpt-5.6-luna",
    "expected_response_model_id": "gpt-5.6-luna",
    "reasoning_effort": "max",
    "argv_prefix": ["codex", "app-server", "--stdio"],
}
OPENCODE_CONSTRAINTS = {
    "host": "opencode",
    "tool_version": "1.18.16",
    "binary_sha256": "a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f",
    "source_commit": "a3647eb025c7615159d417dcc49fc39fdaeba65b",
    "config_selector": "deepseek/deepseek-v4-flash",
    "model_id": "deepseek-v4-flash",
    "expected_response_model_id": "deepseek-v4-flash",
    "reasoning_effort": None,
    "argv_prefix": ["opencode", "--pure", "run", "--format", "json"],
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _gates(classification: dict[str, Any]) -> dict[str, dict[str, Any]]:
    gates = classification["gates"]
    assert isinstance(gates, list)
    return {gate["gate_id"]: gate for gate in gates}


def test_v8_schema_and_profile_are_closed() -> None:
    schema = _load(SCHEMA)
    classification = _load(CLASSIFICATION)

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(classification)

    assert schema["$id"] == (
        "https://deeplaw.dev/contracts/v013-release-gate-classification.v8.schema.json"
    )
    assert classification["schema_version"] == (
        "deeplaw.v013-release-gate-classification/v8"
    )
    assert classification["classification_id"] == "deeplaw-v013-commercial-gates-v8"
    assert classification["profile"] == "machine_evaluated_no_human_attestation"
    assert classification["assembly_policy"] == {
        "assembly_enabled": False,
        "reason_code": "awaiting_all_core_gate_pass",
    }
    assert len(classification["gates"]) == 19


def test_v8_keeps_fourteen_core_gates_but_replaces_human_gate() -> None:
    gates = _gates(_load(CLASSIFICATION))
    assert set(gates) == CORE_GATE_IDS | NON_CORE_GATE_IDS
    assert {
        gate_id for gate_id, gate in gates.items() if gate["category"] == "Core"
    } == CORE_GATE_IDS
    assert len(CORE_GATE_IDS) == 14
    assert "human_gold_isolation" not in gates
    assert "human_gold_scorer" not in json.dumps(_load(CLASSIFICATION), sort_keys=True)


def test_protocol_v2_and_classification_v8_use_one_canonical_core_gate_set() -> None:
    protocol = _load(PROTOCOL)
    protocol_core = {row["gate_id"] for row in protocol["gates"]}
    classification = _load(CLASSIFICATION)
    classification_core = set(classification["categories"][0]["gate_ids"])

    assert protocol_core == classification_core == CORE_GATE_IDS


def test_v8_core_uses_typed_v2_and_gate_result_v4() -> None:
    gates = _gates(_load(CLASSIFICATION))
    for gate_id, artifact_kind in EXPECTED_ARTIFACTS.items():
        gate = gates[gate_id]
        assert gate["validator_id"] == "deeplaw-typed-qualification-v2"
        assert gate["validator_version"] == "2"
        assert gate["accepted_input_schema_versions"] == [
            "deeplaw.typed-qualification-evidence/v2"
        ]
        assert gate["artifact_kinds"] == [artifact_kind]
        assert gate["output_schema_versions"] == [
            "deeplaw.provenance-bound-gate-result/v4"
        ]


def test_machine_reference_gate_has_no_human_semantics() -> None:
    gate = _gates(_load(CLASSIFICATION))["machine_reference_isolation"]
    assert gate["artifact_kinds"] == ["machine_reference_scorer"]
    assert gate["required_corpus_roles"] == [
        "qualification_holdout",
        "final_blind",
    ]
    assert gate["thresholds"] == [
        {"metric": "reference_isolation_pass_rate", "minimum": 1, "maximum": 1}
    ]
    assert gate["hard_zero_derivation"]["failure_ids"] == [
        "compiler_reference_access",
        "evaluator_output_mutation",
        "blind_contamination",
    ]


def test_only_machine_reference_gate_consumes_final_blind() -> None:
    gates = _gates(_load(CLASSIFICATION))
    for gate_id in CORE_GATE_IDS:
        roles = gates[gate_id]["required_corpus_roles"]
        if gate_id == "machine_reference_isolation":
            assert roles == ["qualification_holdout", "final_blind"]
        elif gate_id in {
            "canonical_integrity",
            "migration_recovery",
            "supported_platforms",
            "reproducible_supply_chain",
        }:
            assert roles == ["candidate_full"]
        else:
            assert roles == ["qualification_holdout"]


def test_exact_wheel_execution_is_candidate_full_identity_not_holdout_execution() -> None:
    classification = _load(CLASSIFICATION)
    canonical = _gates(classification)["canonical_integrity"]
    receipt_schema = _load(
        REPOSITORY / "contracts/exact-wheel-execution-receipt.v2.schema.json"
    )

    assert canonical["required_corpus_roles"] == ["candidate_full"]
    assert receipt_schema["$defs"]["corpusBinding"]["properties"]["role"] == {
        "const": "candidate_full"
    }


def test_v8_freezes_exact_host_pins() -> None:
    gates = _gates(_load(CLASSIFICATION))
    assert gates["codex"]["constraints"] == CODEX_CONSTRAINTS
    assert gates["opencode"]["constraints"] == OPENCODE_CONSTRAINTS


def test_v8_keeps_capability_and_competitive_claims_not_claimed() -> None:
    gates = _gates(_load(CLASSIFICATION))
    for gate_id in NON_CORE_GATE_IDS:
        gate = gates[gate_id]
        assert gate["required"] is False
        assert gate["not_claimed_only"] is True
        assert gate["implementation_status"] == "not_claimed_only"
    assert all(
        gate["category"] != "Core"
        for gate_id in {
            "semantic_restore",
            "claude",
            "comparative_incremental_benefit",
            "superiority",
            "sota",
        }
    )


def test_v8_contains_no_gate_result_or_pass_fact() -> None:
    classification = _load(CLASSIFICATION)
    forbidden = {
        "gate_result",
        "result",
        "status",
        "outcome",
        "passed",
        "pass",
        "observed",
    }
    for gate in classification["gates"]:
        assert forbidden.isdisjoint(gate)
        assert gate["assembly_enabled"] is False


def test_v8_schema_rejects_forged_machine_or_human_artifact() -> None:
    schema = _load(SCHEMA)
    validator = Draft202012Validator(schema)
    classification = _load(CLASSIFICATION)

    forged_machine = copy.deepcopy(classification)
    machine_gate = next(
        gate for gate in forged_machine["gates"]
        if gate["gate_id"] == "machine_reference_isolation"
    )
    machine_gate["artifact_kinds"] = ["human_gold_scorer"]
    with pytest.raises(ValidationError):
        validator.validate(forged_machine)

    forged_result = copy.deepcopy(classification)
    forged_result["gates"][0]["passed"] = True
    with pytest.raises(ValidationError):
        validator.validate(forged_result)


def test_v8_is_versioned_without_mutating_v7() -> None:
    historical = _load(HISTORICAL_CLASSIFICATION)
    current = _load(CLASSIFICATION)
    assert historical["schema_version"] == "deeplaw.v013-release-gate-classification/v7"
    assert historical["classification_id"] == "deeplaw-v013-commercial-gates-v7"
    assert current["schema_version"] != historical["schema_version"]
    assert current["classification_id"] != historical["classification_id"]

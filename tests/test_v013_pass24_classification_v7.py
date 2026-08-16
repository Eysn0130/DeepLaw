from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

REPOSITORY = Path(__file__).resolve().parents[1]
CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v7.json"
SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v7.schema.json"
HISTORICAL_CLASSIFICATION = (
    REPOSITORY / "benchmarks/release/v013-gate-classification-v6.json"
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
    "human_gold_isolation",
    "codex",
    "opencode",
    "selective_forget",
    "timeline",
}
CANDIDATE_GATE_IDS = {
    "migration_recovery",
    "supported_platforms",
    "reproducible_supply_chain",
}
EXTERNAL_GATE_IDS = CORE_GATE_IDS - CANDIDATE_GATE_IDS
CANONICAL_GATE_ID = "canonical_integrity"
CORE_TYPED_MAPPING = {
    "canonical_integrity": "exact_wheel_execution",
    "migration_recovery": "candidate_full_junit",
    "secret_host_isolation": "host_event_sequence",
    "bounded_context": "context_capsule_selection_usage",
    "legal_evidence": "legal_rows",
    "source_citation_locator": "legal_rows",
    "scale_performance": "scale_report",
    "supported_platforms": "candidate_platform_receipt",
    "reproducible_supply_chain": "retained_supply_chain",
    "human_gold_isolation": "human_gold_scorer",
    "codex": "host_event_sequence",
    "opencode": "host_event_sequence",
    "selective_forget": "wiki_journey_rows",
    "timeline": "host_event_sequence",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _gates(classification: dict[str, Any]) -> dict[str, dict[str, Any]]:
    gates = classification["gates"]
    assert isinstance(gates, list)
    return {gate["gate_id"]: gate for gate in gates}


def test_v7_schema_and_canonical_document_are_closed() -> None:
    schema = _load(SCHEMA)
    classification = _load(CLASSIFICATION)

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(classification)

    assert schema["$id"] == (
        "https://deeplaw.dev/contracts/v013-release-gate-classification.v7.schema.json"
    )
    assert classification["schema_version"] == (
        "deeplaw.v013-release-gate-classification/v7"
    )
    assert classification["classification_id"] == "deeplaw-v013-commercial-gates-v7"
    assert classification["assembly_policy"] == {
        "assembly_enabled": False,
        "reason_code": "awaiting_all_core_gate_pass",
    }
    assert classification["gates"] and len(classification["gates"]) == 19


def test_v7_has_exactly_fourteen_core_gates_and_separate_corpus_domains() -> None:
    gates = _gates(_load(CLASSIFICATION))
    assert set(gates) == CORE_GATE_IDS | {
        "semantic_restore",
        "claude",
        "comparative_incremental_benefit",
        "superiority",
        "sota",
    }
    assert {
        gate_id for gate_id, gate in gates.items() if gate["category"] == "Core"
    } == CORE_GATE_IDS
    assert len(CORE_GATE_IDS) == 14

    for gate_id in CANDIDATE_GATE_IDS:
        assert gates[gate_id]["required_corpus_roles"] == ["candidate_full"]
    assert gates[CANONICAL_GATE_ID]["required_corpus_roles"] == [
        "qualification_holdout"
    ]
    for gate_id in EXTERNAL_GATE_IDS - {CANONICAL_GATE_ID}:
        assert gates[gate_id]["required_corpus_roles"] == [
            "qualification_holdout",
            "final_blind",
        ]
        assert "candidate_full" not in gates[gate_id]["required_corpus_roles"]
        assert "development" not in gates[gate_id]["required_corpus_roles"]


def test_v7_keeps_non_core_capabilities_and_claims_not_claimed() -> None:
    gates = _gates(_load(CLASSIFICATION))
    for gate_id in ("semantic_restore", "claude"):
        gate = gates[gate_id]
        assert gate["category"] == "Capability"
        assert gate["required"] is False
        assert gate["not_claimed_only"] is True
        assert gate["implementation_status"] == "not_claimed_only"

    for gate_id in (
        "comparative_incremental_benefit",
        "superiority",
        "sota",
    ):
        gate = gates[gate_id]
        assert gate["category"] == "Competitive Claim"
        assert gate["required"] is False
        assert gate["not_claimed_only"] is True
        assert gate["implementation_status"] == "not_claimed_only"

    assert all(
        gate["category"] != "Core"
        for gate_id, gate in gates.items()
        if gate_id in {"semantic_restore", "claude", "sota"}
    )


def test_v7_freezes_real_host_identity_without_a_version_range() -> None:
    gates = _gates(_load(CLASSIFICATION))
    assert gates["codex"]["constraints"] == {
        "host": "codex",
        "tool_version": "codex-cli 0.148.0-alpha.9",
        "model_id": "gpt-5.6-luna",
        "argv_prefix": ["codex", "app-server", "--stdio"],
    }
    assert gates["opencode"]["constraints"] == {
        "host": "opencode",
        "tool_version": "1.18.16",
        "model_id": "deepseek-v4-flash",
        "argv_prefix": ["opencode", "--pure", "run", "--format", "json"],
    }


def test_v7_cross_host_gates_require_all_six_distinct_composite_runs() -> None:
    gates = _gates(_load(CLASSIFICATION))
    for gate_id in ("secret_host_isolation", "timeline"):
        assert gates[gate_id]["minimum_distinct_run_count"] == 6
        assert gates[gate_id]["required_unique_dimensions"] == [
            "run_id",
            "host",
            "task_case",
        ]


def test_v7_core_uses_only_the_current_typed_evidence_and_gate_result_surfaces() -> None:
    gates = _gates(_load(CLASSIFICATION))
    for gate_id, artifact_kind in CORE_TYPED_MAPPING.items():
        gate = gates[gate_id]
        assert gate["validator_id"] == "deeplaw-typed-qualification-v1"
        assert gate["validator_version"] == "1"
        assert gate["accepted_input_schema_versions"] == [
            "deeplaw.typed-qualification-evidence/v1"
        ]
        assert gate["artifact_kinds"] == [artifact_kind]
        assert gate["output_schema_versions"] == [
            "deeplaw.provenance-bound-gate-result/v3"
        ]
        assert "deeplaw.v013-gate-source-evidence/v1" not in gate[
            "accepted_input_schema_versions"
        ]
        assert "deeplaw.v013-gate-result/v1" not in gate["output_schema_versions"]


def test_v7_classification_declares_no_gate_result_or_pass_fact() -> None:
    classification = _load(CLASSIFICATION)
    forbidden_keys = {
        "gate_result",
        "result",
        "status",
        "outcome",
        "passed",
        "pass",
        "observed",
    }
    for gate in classification["gates"]:
        assert forbidden_keys.isdisjoint(gate)
        assert gate["assembly_enabled"] is False


def test_v7_schema_rejects_cross_domain_candidate_role_and_extra_result_field() -> None:
    schema = _load(SCHEMA)
    validator = Draft202012Validator(schema)
    classification = _load(CLASSIFICATION)

    wrong_role = copy.deepcopy(classification)
    next(
        gate
        for gate in wrong_role["gates"]
        if gate["gate_id"] == "canonical_integrity"
    )["required_corpus_roles"] = ["final_blind"]
    with pytest.raises(ValidationError):
        validator.validate(wrong_role)

    forged_result = copy.deepcopy(classification)
    forged_result["gates"][0]["passed"] = True
    with pytest.raises(ValidationError):
        validator.validate(forged_result)


def test_v7_is_a_new_current_contract_and_leaves_v6_historical_contract_intact() -> None:
    historical = _load(HISTORICAL_CLASSIFICATION)
    current = _load(CLASSIFICATION)
    assert historical["schema_version"] == "deeplaw.v013-release-gate-classification/v6"
    assert historical["classification_id"] == "deeplaw-v013-commercial-gates-v6"
    assert current["schema_version"] != historical["schema_version"]
    assert current["classification_id"] != historical["classification_id"]
    assert historical["assembly_policy"] == current["assembly_policy"]

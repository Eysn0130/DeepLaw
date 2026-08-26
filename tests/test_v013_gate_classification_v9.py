from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v9.schema.json"
CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v9.json"
V8_SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v8.schema.json"
V8_FIXTURE = REPOSITORY / "benchmarks/release/v013-gate-classification-v8.json"
V2_SCHEMA = REPOSITORY / "contracts/v013-qualification-protocol.v2.schema.json"
V2_FIXTURE = REPOSITORY / "benchmarks/v013/qualification-protocol-v2.json"

CORE = {
    "canonical_integrity",
    "migration_recovery",
    "secret_host_isolation",
    "bounded_context",
    "source_citation_locator",
    "living_wiki",
    "scale_performance",
    "supported_platforms",
    "reproducible_supply_chain",
    "codex",
    "opencode",
    "selective_forget",
    "timeline",
}
CAPABILITIES = {
    "official_legal_pack",
    "semantic_restore",
    "claude",
    "gui_desktop_interoperability",
}
RESEARCH = {
    "machine_reference_isolation",
    "qualification_comparative_holdout",
    "final_blind_comparative_holdout",
    "agent_review_panel",
    "scorer_a",
    "scorer_b",
    "arbiter",
    "comparative_incremental_benefit",
    "superiority",
    "sota",
}
TASKS = ["continuity", "living_wiki", "professional_evidence"]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _gates() -> dict[str, dict[str, Any]]:
    return {row["gate_id"]: row for row in _load(CLASSIFICATION)["gates"]}


def test_v9_schema_and_fixture_are_closed_and_valid() -> None:
    schema = _load(SCHEMA)
    classification = _load(CLASSIFICATION)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(classification)

    assert schema["$id"].endswith("v013-release-gate-classification.v9.schema.json")
    assert classification["schema_version"] == (
        "deeplaw.v013-release-gate-classification/v9"
    )
    assert classification["profile"] == "kernel_release_core"
    assert classification["construction_package_version"] == "0.12.0"
    assert classification["release_target"] == "0.13.0"
    assert classification["assembly_policy"] == {
        "assembly_enabled": False,
        "reason_code": "awaiting_all_core_gate_pass",
    }


def test_v9_has_exact_core_and_capability_inventory_and_research_is_nonblocking() -> None:
    classification = _load(CLASSIFICATION)
    by_category = {
        row["category_id"]: set(row["gate_ids"])
        for row in classification["categories"]
    }
    assert by_category["core"] == CORE
    assert by_category["capability"] == CAPABILITIES
    assert by_category["competitive_research"] >= RESEARCH

    gates = _gates()
    assert {gate_id for gate_id, row in gates.items() if row["category"] == "Core"} == CORE
    assert len(CORE) == 13
    assert len(CAPABILITIES) == 4
    assert len(gates) == len(CORE) + len(CAPABILITIES) + len(RESEARCH)
    for gate_id in CAPABILITIES | RESEARCH:
        row = gates[gate_id]
        assert row["required"] is False
        assert row["not_claimed_only"] is True
        assert row["status"] == "not_executed"
        assert row["passed"] is False
        assert row["claim"] is False
        assert row["category"] != "Core"
        assert row["minimum_distinct_run_count"] == 0
        assert row["accepted_input_schema_versions"] == []
        assert row["required_corpus_roles"] == []
        assert row["artifact_kinds"] == []
        assert row["required_execution_platforms"] == []
        assert row["output_schema_versions"] == [
            "deeplaw.provenance-bound-gate-result/v5"
        ]

    expected_core_roles = {
        "canonical_integrity": ["candidate_full"],
        "migration_recovery": ["candidate_full"],
        "secret_host_isolation": ["host_qualification"],
        "bounded_context": ["host_qualification"],
        "source_citation_locator": ["professional_evidence"],
        "living_wiki": ["living_wiki"],
        "scale_performance": ["scale_10000"],
        "supported_platforms": ["candidate_platform"],
        "reproducible_supply_chain": ["supply_chain"],
        "codex": ["host_qualification"],
        "opencode": ["host_qualification"],
        "selective_forget": ["host_qualification"],
        "timeline": ["host_qualification"],
    }
    for gate_id, roles in expected_core_roles.items():
        assert gates[gate_id]["validator_id"] == "deeplaw-typed-qualification-v3"
        assert gates[gate_id]["accepted_input_schema_versions"] == [
            "deeplaw.typed-qualification-evidence/v3"
        ]
        assert gates[gate_id]["required_corpus_roles"] == roles
        assert gates[gate_id]["output_schema_versions"] == [
            "deeplaw.provenance-bound-gate-result/v5"
        ]


def test_source_and_wiki_duties_use_current_typed_artifacts() -> None:
    gates = _gates()
    source = gates["source_citation_locator"]
    assert source["artifact_kinds"] == ["professional_evidence_rows"]
    duties = set(source["source_duties"])
    assert duties == {
        "original_bytes",
        "original_hash",
        "document",
        "version",
        "fragment",
        "locator",
        "wrong_version_rejection",
        "effective_date",
        "exception",
        "proviso",
        "cross_reference",
        "false_authority",
        "ocr_critical_token_gap",
        "wiki_exact_source_drill_down",
    }
    measurement_contract = {
        *[item["metric"] for item in source["thresholds"]],
        *source["hard_zero_derivation"]["failure_ids"],
    }
    for duty in duties:
        assert any(duty in item for item in measurement_contract)
    assert "false_authority_admission" in source["hard_zero_derivation"]["failure_ids"]
    assert "ocr_critical_token_gap_missing" in source["hard_zero_derivation"]["failure_ids"]
    wiki = gates["living_wiki"]
    assert wiki["artifact_kinds"] == ["wiki_journey_rows"]
    assert wiki["required_task_cases"] == [
        "alias_same_name_identity",
        "rename_move",
        "external_edit_reconcile",
        "backlink_outlink",
        "source_successor",
        "wrong_merge_rejection",
        "user_file_protection",
        "full_incremental_noop_equivalence",
    ]
    selective_forget = gates["selective_forget"]
    assert selective_forget["artifact_kinds"] == ["host_event_sequence"]
    assert selective_forget["required_task_cases"] == ["continuity"]
    assert selective_forget["minimum_distinct_run_count"] == 2


def test_scale_boundary_is_exact_and_has_required_measurements() -> None:
    scale = _gates()["scale_performance"]
    contract = scale["scale_contract"]
    assert contract == {
        "active_governed_objects": 10000,
        "vault": "Vault",
        "above_10000": "experimental_unqualified",
        "deferred_100000": "v0.14",
        "warm_samples": 30,
        "required_metrics": [
            "p50",
            "p95",
            "max",
            "rss",
            "storage",
            "file_count",
            "build",
            "rebuild",
            "full_incremental_noop_equivalence",
            "user_bytes",
            "provider_bound",
        ],
        "hard_failure_ids": [
            "active_governed_object_count_mismatch",
            "experimental_over_10000_claimed_qualified",
            "100000_not_deferred_to_v0.14",
            "warm_samples_below_30",
            "missing_p50",
            "missing_p95",
            "missing_max",
            "rss_missing",
            "storage_missing",
            "file_count_missing",
            "build_duration_missing",
            "rebuild_duration_missing",
            "full_incremental_noop_mismatch",
            "user_bytes_unbounded",
            "provider_bound_exceeded",
        ],
    }
    assert scale["status"] == "not_executed"
    assert scale["passed"] is False


def test_host_pins_and_cross_host_task_matrix_are_frozen() -> None:
    gates = _gates()
    for gate_id in ("secret_host_isolation", "timeline"):
        row = gates[gate_id]
        assert row["minimum_distinct_run_count"] == 6
        assert row["required_task_cases"] == TASKS
        assert row["required_unique_dimensions"] == ["run_id", "host", "task_case"]
    for gate_id in ("codex", "opencode"):
        row = gates[gate_id]
        assert row["minimum_distinct_run_count"] == 3
        assert row["required_task_cases"] == TASKS
        assert row["required_unique_dimensions"] == [
            "run_id",
            "host",
            "model",
            "platform",
            "task_case",
        ]
    assert gates["codex"]["constraints"] == {
        "host": "codex",
        "tool_version": None,
        "binary_sha256": None,
        "source_commit": None,
        "config_selector": None,
        "model_id": "gpt-5.6-luna",
        "expected_response_model_id": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "argv_prefix": ["codex", "app-server", "--stdio"],
    }
    assert gates["opencode"]["constraints"] == {
        "host": "opencode",
        "tool_version": None,
        "binary_sha256": None,
        "source_commit": None,
        "config_selector": "deepseek/deepseek-v4-flash",
        "model_id": "deepseek-v4-flash",
        "expected_response_model_id": "deepseek-v4-flash",
        "reasoning_effort": None,
        "argv_prefix": ["opencode", "--pure", "run", "--format", "json"],
    }


def test_not_executed_cannot_be_forged_as_pass_or_claim() -> None:
    validator = Draft202012Validator(_load(SCHEMA))
    classification = _load(CLASSIFICATION)

    forged_pass = copy.deepcopy(classification)
    forged_pass["gates"][0]["passed"] = True
    with pytest.raises(ValidationError):
        validator.validate(forged_pass)

    forged_claim = copy.deepcopy(classification)
    forged_claim["gates"][-1]["claim"] = True
    with pytest.raises(ValidationError):
        validator.validate(forged_claim)

    forged_required = copy.deepcopy(classification)
    forged_required["gates"][-1]["required"] = True
    with pytest.raises(ValidationError):
        validator.validate(forged_required)


def test_v8_and_v2_remain_historical_and_semantically_intact() -> None:
    v8_schema = _load(V8_SCHEMA)
    v8 = _load(V8_FIXTURE)
    v2_schema = _load(V2_SCHEMA)
    v2 = _load(V2_FIXTURE)
    Draft202012Validator.check_schema(v8_schema)
    Draft202012Validator(v8_schema).validate(v8)
    Draft202012Validator.check_schema(v2_schema)
    Draft202012Validator(v2_schema).validate(v2)
    assert v8["schema_version"] == "deeplaw.v013-release-gate-classification/v8"
    assert v2["schema_version"] == "deeplaw.v013-qualification-protocol/v2"
    assert "legal_evidence" in {row["gate_id"] for row in v8["gates"]}
    assert "machine_reference_isolation" in {row["gate_id"] for row in v8["gates"]}
    assert v8["profile"] == "machine_evaluated_no_human_attestation"
    assert v2["candidate_binding"]["package_version"] == "0.12.0"

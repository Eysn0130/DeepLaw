from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

REPOSITORY = Path(__file__).resolve().parents[1]
PROTOCOL_SCHEMA = REPOSITORY / "contracts/v013-qualification-protocol.v3.schema.json"
PROTOCOL = REPOSITORY / "benchmarks/v013/qualification-protocol-v3.json"
PROTOCOL_HASH = REPOSITORY / "benchmarks/v013/qualification-protocol-v3.sha256"
ACTIVE_SCHEMA = REPOSITORY / "contracts/v013-active-qualification.v3.schema.json"
ACTIVE = REPOSITORY / "benchmarks/v013/active-qualification-v3.json"
CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v9.json"
V8_SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v8.schema.json"
V8 = REPOSITORY / "benchmarks/release/v013-gate-classification-v8.json"
V2_SCHEMA = REPOSITORY / "contracts/v013-qualification-protocol.v2.schema.json"
V2 = REPOSITORY / "benchmarks/v013/qualification-protocol-v2.json"

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
HOST_TASKS = ["continuity", "living_wiki", "professional_evidence"]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _validate(schema_path: Path, fixture_path: Path) -> dict[str, Any]:
    schema = _load(schema_path)
    value = _load(fixture_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    return value


def test_protocol_v3_is_closed_exactly_bound_and_not_executed() -> None:
    protocol = _validate(PROTOCOL_SCHEMA, PROTOCOL)
    classification = _load(CLASSIFICATION)
    classification_digest = hashlib.sha256(CLASSIFICATION.read_bytes()).hexdigest()

    assert protocol["schema_version"] == "deeplaw.v013-qualification-protocol/v3"
    assert protocol["profile"] == "kernel_release_core"
    assert protocol["protocol_version"] == 3
    assert protocol["no_human_attestation"] is True
    assert protocol["construction_package_version"] == "0.12.0"
    assert protocol["release_target"] == "0.13.0"
    assert protocol["classification_binding"]["sha256"] == classification_digest
    assert protocol["classification_binding"]["classification_id"] == classification[
        "classification_id"
    ]

    by_category = {
        row["category_id"]: set(row["gate_ids"])
        for row in protocol["categories"]
    }
    assert by_category["core"] == CORE
    assert by_category["capability"] == CAPABILITIES
    assert by_category["competitive_research"] >= RESEARCH
    assert len(protocol["gates"]) == 27

    core_rows = [row for row in protocol["gates"] if row["category_id"] == "core"]
    optional_rows = [row for row in protocol["gates"] if row["category_id"] != "core"]
    assert {row["gate_id"] for row in core_rows} == CORE
    assert all(row["required"] is True for row in core_rows)
    assert all(
        row["status"] == "not_executed"
        and row["passed"] is False
        and row["claim"] is False
        for row in protocol["gates"]
    )
    assert all(
        row["required"] is False and row["not_claimed_only"] is True
        for row in optional_rows
    )
    assert all(
        row["evidence_kind"] == "not_executed"
        and row["artifact_kind"] == "not_executed"
        and row["required_corpus_roles"] == []
        for row in optional_rows
    )


def test_protocol_v3_hash_sidecar_and_history_are_preserved() -> None:
    sidecar = PROTOCOL_HASH.read_text(encoding="utf-8")
    match = re.fullmatch(r"([0-9a-f]{64})  qualification-protocol-v3\.json\n", sidecar)
    assert match is not None
    assert match.group(1) == hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()

    v8 = _validate(V8_SCHEMA, V8)
    v2 = _validate(V2_SCHEMA, V2)
    assert v8["schema_version"] == "deeplaw.v013-release-gate-classification/v8"
    assert v2["schema_version"] == "deeplaw.v013-qualification-protocol/v2"
    assert "legal_evidence" in {row["gate_id"] for row in v8["gates"]}
    assert "machine_reference_isolation" in {row["gate_id"] for row in v8["gates"]}
    assert v2["candidate_binding"]["package_version"] == "0.12.0"


def test_protocol_v3_source_scale_library_and_host_contracts() -> None:
    protocol = _load(PROTOCOL)
    source = protocol["source_contract"]
    duties = set(source["duties"])
    assert source["artifact_kind"] == "professional_evidence_rows"
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
    assert protocol["scale_contract"]["active_governed_objects"] == 10000
    assert protocol["scale_contract"]["vault"] == "Vault"
    assert protocol["scale_contract"]["above_10000"] == "experimental_unqualified"
    assert protocol["scale_contract"]["deferred_100000"] == "v0.14"
    assert protocol["scale_contract"]["warm_samples"] == 30
    assert set(protocol["scale_contract"]["required_metrics"]) == {
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
    }
    library = protocol["evidence_library"]
    assert library["accepted_materials"] == [
        "owner_provided_professional",
        "owner_provided_legal",
    ]
    assert library["produces_official_authority"] is False
    assert library["produces_legal_authority"] is False
    assert library["legal_expert_required"] is False
    assert library["signed_pack_required"] is False

    assert protocol["gates"]
    for gate_id in ("secret_host_isolation", "timeline", "codex", "opencode"):
        row = next(row for row in protocol["gates"] if row["gate_id"] == gate_id)
        assert row["required_task_cases"] == HOST_TASKS
    assert protocol["host_constraints"]["codex"] == {
        "binary_version": None,
        "binary_sha256": None,
        "request_model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "auth_status_command": "codex login status",
        "auth_material_access": "forbidden",
    }
    assert protocol["host_constraints"]["opencode"]["version"] is None
    assert protocol["host_constraints"]["opencode"]["source_commit"] is None


def test_active_v3_pending_template_is_separate_and_release_closed() -> None:
    active = _validate(ACTIVE_SCHEMA, ACTIVE)
    protocol_digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    classification_digest = hashlib.sha256(CLASSIFICATION.read_bytes()).hexdigest()

    current_stages = {
        "machine_evaluation_pending": (
            "0.12.0",
            "machine_evaluation_not_executed",
        ),
        "construction_candidate_machine_evaluation_pending": (
            "0.13.0",
            "candidate_artifact_not_built",
        ),
    }
    assert active["status"] in current_stages
    expected_version, expected_blocker = current_stages[active["status"]]
    assert active["candidate_version"] == expected_version
    assert active["construction_package_version"] == "0.12.0"
    assert active["release_target"] == "0.13.0"
    assert active["blocker"] == expected_blocker
    assert active["protocol_binding"]["sha256"] == protocol_digest
    assert active["classification_binding"]["sha256"] == classification_digest
    candidate = active["candidate_binding"]
    assert candidate["package_version"] == expected_version
    assert candidate["lock_sha256"] == hashlib.sha256(
        (REPOSITORY / "uv.lock").read_bytes()
    ).hexdigest()
    candidate_fields = (
        "source_commit",
        "source_tree",
        "wheel_filename",
        "wheel_sha256",
        "sdist_filename",
        "sdist_sha256",
        "artifact_manifest_sha256",
    )
    assert all(candidate[field] is None for field in candidate_fields)
    assert all(
        value is None
        for key, value in active["external_inputs"].items()
        if key.endswith("_sha256")
    )
    assert active["external_inputs"]["null_is_non_blocking"] is True
    assert active["external_inputs"]["required_for_candidate_binding"] is False

    assert {row["gate_id"] for row in active["core_statuses"]} == CORE
    assert {row["gate_id"] for row in active["capability_claims"]} == CAPABILITIES
    assert {row["gate_id"] for row in active["competitive_claims"]} >= RESEARCH
    claim_rows = (
        active["core_statuses"],
        active["capability_claims"],
        active["competitive_claims"],
    )
    assert all(
        row["status"] == "not_executed"
        and row["passed"] is False
        and row["claim"] is False
        for rows in claim_rows
        for row in rows
    )
    assert active["release_ready"] is False
    assert active["claim_eligible"] is False
    assert active["kernel_release_claim_eligible"] is False
    assert active["competitive_claim_eligible"] is False
    assert active["owner_release_confirmation"] == "required_at_release_decision"

    legal_pack_doc = (REPOSITORY / "docs/DEEPLAW_2.md").read_text(encoding="utf-8")
    adapters_doc = (REPOSITORY / "docs/AGENT_ADAPTERS.md").read_text(encoding="utf-8")
    security_doc = (REPOSITORY / "SECURITY.md").read_text(encoding="utf-8")
    assert "active qualification protocol v3" in legal_pack_doc
    assert "profile 为 `kernel_release_core`" in legal_pack_doc
    assert "Gate classification 为 v9" in legal_pack_doc
    assert "`release_ready=false`" in legal_pack_doc
    assert "not_executed" in legal_pack_doc
    assert "Real model/session tasks on Codex/OpenCode" in adapters_doc
    assert "Active profile is `kernel_release_core`" in adapters_doc
    assert "not_executed" in adapters_doc
    assert "host-preflight-receipt/v1" in security_doc
    assert "host-process-receipt/v2" in security_doc
    assert "host-process-receipt-set/v1" in security_doc
    assert "host-process-receipt/v1" in security_doc
    assert "historical/invalidated" in security_doc


def test_active_v3_stage_transitions_do_not_require_optional_external_hashes() -> None:
    schema = _load(ACTIVE_SCHEMA)
    validator = Draft202012Validator(schema)
    active = _load(ACTIVE)

    construction = copy.deepcopy(active)
    construction["status"] = "construction_candidate_machine_evaluation_pending"
    construction["candidate_version"] = "0.13.0"
    construction["blocker"] = "candidate_artifact_not_built"
    construction["candidate_binding"]["package_version"] = "0.13.0"
    validator.validate(construction)

    frozen = copy.deepcopy(construction)
    frozen["status"] = "frozen_exact_candidate_machine_evaluation_pending"
    frozen["blocker"] = None
    frozen["candidate_binding"].update(
        {
            "source_commit": "1" * 40,
            "source_tree": "2" * 40,
            "wheel_filename": "deeplaw-0.13.0-py3-none-any.whl",
            "wheel_sha256": "3" * 64,
            "sdist_filename": "deeplaw-0.13.0.tar.gz",
            "sdist_sha256": "4" * 64,
            "artifact_manifest_sha256": "5" * 64,
        }
    )
    validator.validate(frozen)

    forged_pass = copy.deepcopy(active)
    forged_pass["core_statuses"][0]["passed"] = True
    with pytest.raises(ValidationError):
        validator.validate(forged_pass)
    forged_claim = copy.deepcopy(active)
    forged_claim["capability_claims"][0]["claim"] = True
    with pytest.raises(ValidationError):
        validator.validate(forged_claim)

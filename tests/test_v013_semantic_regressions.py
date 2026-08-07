from __future__ import annotations

from pathlib import Path

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.compilation.models import SEMANTIC_COMPILER_GRANT_OPERATIONS
from deeplaw.compilation.semantic import SemanticCompilationService
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes


def _source_reference(packet: dict, fragment: dict) -> dict[str, str]:
    return {
        "source_revision_id": packet["source_revision_id"],
        "fragment_id": fragment["fragment_id"],
        "locator": fragment["locator"],
        "quote_sha256": fragment["text_sha256"],
    }


def _observation_plan(
    service: SemanticCompilationService,
    packet: dict,
    *,
    observation: dict | None,
) -> dict:
    observations = [] if observation is None else [observation]
    fragment_ids = [fragment["fragment_id"] for fragment in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-observation-plan/v2",
        "compilation_run_id": packet["compilation_run_id"],
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "observations": observations,
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": fragment_ids,
            "omitted_fragments": [],
            "ratio": 1.0,
        },
        "warnings": [],
    }


def _packet_plan(packet: dict) -> dict:
    fragment_ids = [fragment["fragment_id"] for fragment in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "object_actions": [],
        "relation_actions": [],
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": [],
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": fragment_ids,
            "omitted_fragment_ids": [],
            "ratio": 1.0,
            "completeness": "complete",
        },
        "skipped_fragments": [],
        "warnings": [],
    }


def _semantic_fixture(tmp_path: Path) -> dict:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-semantic-regression", scope="project")
    initialize_autonomous_core(root)
    source = tmp_path / "source.md"
    source.write_text(
        "# Applicable concept\nThe source contains one bounded semantic statement.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v013-semantic-regression-agent",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]

    profile = KnowledgeOS.open(root).compilations.profile(version="2")
    coordinator = CompilationCoordinator(root)
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version=profile["compiler_profile_version"],
        host_identity="v013-semantic-regression-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        packet_max_fragments=128,
        confirm_no_case_data=True,
    )
    run_id = begun["compilation_run_id"]
    service = SemanticCompilationService(root)
    packets = []
    concept_observation = None
    while packet := service.next_observation_packet(run_id):
        packets.append(packet)
        if concept_observation is None:
            fragment = packet["fragments"][0]
            concept_observation = {
                "packet_id": packet["packet_id"],
                "semantic_key_candidate": "concept:v013-regression",
                "kind": "concept",
                "title_candidate": "V013 regression concept",
                "body_candidate": "A structurally applicable concept observation.",
                "aliases": ["V013 regression concept"],
                "source_refs": [_source_reference(packet, fragment)],
                "assertion": None,
                "applicability": {
                    "description": "The concept is applicable to this source revision.",
                    "scopes": [],
                    "conditions": [],
                    "exclusions": [],
                },
                "tags": ["v013-regression"],
                "reason": "Create a real concept observation for the duty regression.",
            }
            concept_observation["observation_id"] = service.observation_id(
                compilation_run_id=run_id,
                packet_id=packet["packet_id"],
                observation=concept_observation,
            )
        observation = (
            concept_observation if packet["packet_id"] == concept_observation["packet_id"] else None
        )
        service.stage_observations(
            grant_id=grant_id,
            compilation_run_id=run_id,
            plan=_observation_plan(service, packet, observation=observation),
            confirm_no_case_data=True,
        )
    assert packets
    assert concept_observation is not None
    inventory = service.inventory(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    finalization_packet = service.finalization_packet(run_id)
    source_refs = [
        _source_reference(packet, fragment)
        for packet in packets
        for fragment in packet["fragments"]
    ]
    return {
        "grant_id": grant_id,
        "run_id": run_id,
        "service": service,
        "packets": packets,
        "inventory": inventory,
        "finalization_packet": finalization_packet,
        "source_refs": source_refs,
        "concept_observation": concept_observation,
    }


def _publication_plan(
    fixture: dict,
    *,
    concept_status: str,
    include_concept_refs: bool = False,
) -> dict:
    run_id = fixture["run_id"]
    packets = fixture["packets"]
    source_revision_id = packets[0]["source_revision_id"]
    source_refs = fixture["source_refs"]
    concept_observation = fixture["concept_observation"]
    summary_inputs = {
        "source_revision_ids": [source_revision_id],
        "knowledge_revision_ids": [],
        "relation_revision_ids": [],
        "compilation_run_ids": [run_id],
    }
    summary_action = {
        "action": "create",
        "kind": "synthesis",
        "semantic_key": f"source-summary:{source_revision_id}",
        "knowledge_id": None,
        "expected_revision_id": None,
        "title": "Source summary",
        "body": "A source-bound summary used to exercise semantic finalization.",
        "aliases": [],
        "epistemic_state": "supported",
        "source_refs": source_refs,
        "assertion": None,
        "tags": ["v013-regression"],
        "valid_from": None,
        "valid_to": None,
        "applicability": {
            "description": "This source revision.",
            "scopes": [],
            "conditions": [],
            "exclusions": [],
        },
        "synthesis_inputs": {
            **summary_inputs,
            "input_set_sha256": sha256_bytes(canonical_json(summary_inputs).encode("utf-8")),
        },
        "reason": "Provide the canonical Source Summary required for a complete run.",
    }
    packet_plans = [_packet_plan(packet) for packet in packets]
    packet_plans[0]["object_actions"] = [summary_action]
    duty_reports = []
    for duty in fixture["finalization_packet"]["duties"]:
        duty_type = duty["duty_type"]
        status = "satisfied" if duty_type == "source_summary" else "not_applicable"
        output_refs = []
        evidence_refs = []
        unresolved_items = []
        if duty_type == "source_summary":
            evidence_refs = source_refs
        elif duty_type == "concepts":
            status = concept_status
            if status == "unresolved":
                output_refs = [concept_observation["observation_id"]]
                evidence_refs = concept_observation["source_refs"]
                unresolved_items = ["The applicable concept observation remains unresolved."]
            elif status == "satisfied" and include_concept_refs:
                output_refs = [concept_observation["observation_id"]]
                evidence_refs = concept_observation["source_refs"]
        duty_reports.append(
            {
                "duty_id": duty["duty_id"],
                "duty_type": duty_type,
                "required": duty["required"],
                "status": status,
                "output_refs": output_refs,
                "evidence_refs": evidence_refs,
                "reason": "Deterministic regression fixture duty decision.",
                "unresolved_items": unresolved_items,
                "omission_reason": None,
            }
        )
    return {
        "schema_version": "deeplaw.semantic-publication-plan/v2",
        "compilation_run_id": run_id,
        "source_revision_id": source_revision_id,
        "expected_audit_head": packets[0]["input_audit_head"],
        "inventory_sha256": fixture["inventory"]["inventory_sha256"],
        "observation_dispositions": [
            {
                "observation_id": concept_observation["observation_id"],
                "disposition": "proposed_only",
                "target_ref": None,
                "reason": "Keep the observed concept available for the duty decision.",
            }
        ],
        "packet_plans": packet_plans,
        "duty_reports": duty_reports,
        "semantic_status": "complete",
        "warnings": [],
    }


def _assert_not_complete(fixture: dict, plan: dict) -> None:
    service = fixture["service"]
    try:
        staged = service.stage_publication(
            grant_id=fixture["grant_id"],
            compilation_run_id=fixture["run_id"],
            plan=plan,
            confirm_no_case_data=True,
        )
    except ValueError:
        return
    assert staged["semantic_status"] != "complete"


def test_semantic_v2_applicable_optional_duty_cannot_complete_when_unresolved(
    tmp_path: Path,
) -> None:
    fixture = _semantic_fixture(tmp_path)
    _assert_not_complete(fixture, _publication_plan(fixture, concept_status="unresolved"))


def test_semantic_v2_satisfied_duty_requires_statement_evidence(
    tmp_path: Path,
) -> None:
    fixture = _semantic_fixture(tmp_path)
    _assert_not_complete(fixture, _publication_plan(fixture, concept_status="satisfied"))


def test_semantic_v2_not_applicable_duty_cannot_claim_output_or_evidence(
    tmp_path: Path,
) -> None:
    fixture = _semantic_fixture(tmp_path)
    plan = _publication_plan(
        fixture,
        concept_status="satisfied",
        include_concept_refs=True,
    )
    entities = next(item for item in plan["duty_reports"] if item["duty_type"] == "entities")
    entities["output_refs"] = [fixture["concept_observation"]["observation_id"]]
    entities["evidence_refs"] = fixture["concept_observation"]["source_refs"]
    _assert_not_complete(fixture, plan)


def test_semantic_v2_real_satisfied_concept_can_complete(
    tmp_path: Path,
) -> None:
    fixture = _semantic_fixture(tmp_path)
    plan = _publication_plan(
        fixture,
        concept_status="satisfied",
        include_concept_refs=True,
    )
    staged = fixture["service"].stage_publication(
        grant_id=fixture["grant_id"],
        compilation_run_id=fixture["run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    assert staged["semantic_status"] == "complete"


def test_semantic_v2_control_scan_satisfied_without_refs_is_valid(
    tmp_path: Path,
) -> None:
    fixture = _semantic_fixture(tmp_path)
    plan = _publication_plan(
        fixture,
        concept_status="satisfied",
        include_concept_refs=True,
    )
    scan = next(
        item for item in plan["duty_reports"] if item["duty_type"] == "contradiction_scan"
    )
    scan["status"] = "satisfied"
    scan["output_refs"] = []
    scan["evidence_refs"] = []
    staged = fixture["service"].stage_publication(
        grant_id=fixture["grant_id"],
        compilation_run_id=fixture["run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    assert staged["semantic_status"] == "complete"


def test_semantic_v2_satisfied_typed_relations_requires_relation_action(
    tmp_path: Path,
) -> None:
    fixture = _semantic_fixture(tmp_path)
    plan = _publication_plan(
        fixture,
        concept_status="satisfied",
        include_concept_refs=True,
    )
    relations = next(
        item for item in plan["duty_reports"] if item["duty_type"] == "typed_relations"
    )
    relations["status"] = "satisfied"
    relations["output_refs"] = []
    relations["evidence_refs"] = fixture["source_refs"]
    _assert_not_complete(fixture, plan)

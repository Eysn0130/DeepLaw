from __future__ import annotations

from pathlib import Path

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.applicability import (
    applicability_digest,
    derive_applicability,
)
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.compilation.models import SEMANTIC_COMPILER_GRANT_OPERATIONS
from deeplaw.compilation.semantic import SemanticCompilationService
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    _validate_contract,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes


def _facts(
    *, observations: dict[str, int] | None = None, signals: dict[str, bool] | None = None
) -> dict:
    return {
        "source": {
            "source_revision_id": "sourcerev_000000000000000000000001",
            "present": True,
            "admitted": True,
            "nonempty": True,
            "media_type": "text/markdown",
            "kind": "document",
            "byte_size": 128,
            "content_sha256": "0" * 64,
            "lifecycle": "active",
            "scope": "project",
            "sensitivity": "public",
        },
        "source_ir": {
            "compilation_id": "compilation_000000000000000000000001",
            "node_count": 1,
            "node_types": {"section": 1},
            "signals": {
                "code": False,
                "table": False,
                "list": False,
                "timeline": False,
                "question": False,
                "procedure": False,
                **(signals or {}),
            },
            "node_ids": [],
        },
        "fragments": {"count": 1, "fragment_ids": [], "locators": []},
        "observations": {
            "count": sum((observations or {}).values()),
            "kinds": observations or {},
            "observation_ids": ["observation_000000000000000000000001"],
            "source_ref_count": 1,
            "truncated": False,
        },
        "existing": {
            "count": 0,
            "kinds": {},
            "knowledge_ids": [],
            "relation_count": 0,
            "truncated": False,
        },
        "previous_outputs": {"count": 0, "kinds": {}},
        "affected_syntheses": {"count": 0, "ids": []},
        "grant": {"writer_id": "agent", "scope": "project", "max_sensitivity": "public"},
        "truncated": False,
    }


def test_v3_profile_schema_and_hash_is_additive() -> None:
    from deeplaw.compilation.profiles import compiler_profile

    v1 = compiler_profile(version="1")
    v2 = compiler_profile(version="2")
    v3 = compiler_profile(version="3")
    assert v1["compiler_profile_version"] == "1"
    assert v2["compiler_profile_version"] == "2"
    assert v3["compiler_profile_version"] == "3"
    assert v3["observation_plan_contract"].endswith("/v2")
    assert v3["publication_plan_contract"].endswith("/v3")
    _validate_contract("semantic-compilation-profile.v3.schema.json", v3)
    assert v3["prompt_config_sha256"] == sha256_bytes(
        canonical_json(
            {
                "prompt_template_id": v3["prompt_template_id"],
                "observation_plan_contract": v3["observation_plan_contract"],
                "publication_plan_contract": v3["publication_plan_contract"],
                "duty_report_contract": v3["duty_report_contract"],
                "finalization_packet_contract": v3["finalization_packet_contract"],
                "applicability_policy": v3["applicability_policy"],
                "applicability_policy_sha256": v3["applicability_policy_sha256"],
                "semantic_duties": v3["semantic_duties"],
                "authority": "agent_derived",
                "legal_authority": False,
                "source_instruction_policy": "untrusted-data",
            }
        ).encode()
    )


def test_v3_status_exposes_exact_freshness_and_verification(tmp_path: Path) -> None:
    service, _grant_id, run_id, _begun = _v3_fixture(tmp_path)

    status = service.status(run_id)

    assert status["schema_version"] == "deeplaw.source-compilation-run/v3"
    assert status["transaction"]["status"] == "planned"
    assert status["semantic_status"] == "unknown"
    assert status["freshness"] == {
        "state": "unknown",
        "source_status": "pending",
        "dependency_counts": {
            "fresh": 0,
            "unknown": 0,
            "stale": 0,
            "invalidated": 0,
        },
    }
    assert status["verification"]["status"] == "verified"
    assert status["verification"]["valid"] is True
    assert status["verification"]["failure_count"] == 0
    assert status["verification"]["audit_head"] == status["transaction"][
        "input_audit_head"
    ]


def test_v3_applicability_matrix_positive_and_unknown() -> None:
    facts = _facts(
        observations={
            "entity": 1,
            "concept": 1,
            "event": 1,
            "procedure": 1,
            "comparison": 1,
            "relation": 1,
            "question": 1,
        },
        signals={"timeline": True, "procedure": True, "table": True, "question": True},
    )
    facts["existing"] = {
        "count": 2,
        "kinds": {"entity": 1, "concept": 1},
        "knowledge_ids": [],
        "relation_count": 1,
        "truncated": False,
    }
    facts["facts_sha256"] = sha256_bytes(canonical_json(facts).encode())
    decisions = derive_applicability(facts)
    assert len(decisions) == 15
    assert all(
        item["applicability"] == "applicable"
        for duty, item in decisions.items()
        if duty != "overview_impact"
    )
    assert decisions["overview_impact"]["applicability"] == "unknown"
    assert applicability_digest(decisions)

    unknown = _facts()
    unknown["source"]["admitted"] = False
    unknown["truncated"] = False
    decisions = derive_applicability(unknown)
    assert decisions["source_summary"]["applicability"] == "unknown"
    assert decisions["entities"]["applicability"] == "unknown"
    assert decisions["overview_impact"]["applicability"] == "unknown"


def _v3_fixture(tmp_path: Path) -> tuple[SemanticCompilationService, str, str, dict]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-semantic-v3", scope="project")
    initialize_autonomous_core(root)
    source = tmp_path / "source.md"
    source.write_text(
        "# A bounded semantic source\n"
        "This source contains enough text for a deterministic Source Revision "
        "and applicability checks.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(vault, source, source_kind="document", confirm_no_case_data=True)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v013-v3-agent", operations=SEMANTIC_COMPILER_GRANT_OPERATIONS
        )["grant_id"]
    profile = KnowledgeOS.open(root).compilations.profile(version="3")
    begun = CompilationCoordinator(root).begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-agent",
        compiler_profile_version="3",
        host_identity="v013-v3-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        packet_max_fragments=128,
        confirm_no_case_data=True,
    )
    return SemanticCompilationService(root), grant_id, begun["compilation_run_id"], begun


def test_v3_observation_v2_inventory_and_finalization_packet(tmp_path: Path) -> None:
    service, grant_id, run_id, begun = _v3_fixture(tmp_path)
    while packet := service.next_observation_packet(run_id):
        fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
        service.stage_observations(
            grant_id=grant_id,
            compilation_run_id=run_id,
            plan={
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": run_id,
                "source_revision_id": packet["source_revision_id"],
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": [],
                "coverage": {
                    "packet_fragment_count": len(fragment_ids),
                    "covered_fragment_ids": fragment_ids,
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            },
            confirm_no_case_data=True,
        )
    inventory = service.inventory(
        grant_id=grant_id, compilation_run_id=run_id, confirm_no_case_data=True
    )
    packet = service.finalization_packet(run_id)
    assert inventory["coverage"]["applicability_policy_sha256"]
    assert packet["schema_version"] == "deeplaw.semantic-finalization-packet/v2"
    assert packet["inventory_sha256"] == inventory["inventory_sha256"]
    assert len(packet["duties"]) == 15
    _validate_contract("semantic-finalization-packet.v2.schema.json", packet)
    assert begun["compiler_profile_version"] == "3"


def test_v3_finalization_packet_uses_closed_coverage_projection(tmp_path: Path) -> None:
    service, grant_id, run_id, _ = _v3_fixture(tmp_path)
    while packet := service.next_observation_packet(run_id):
        fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
        service.stage_observations(
            grant_id=grant_id,
            compilation_run_id=run_id,
            plan={
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": run_id,
                "source_revision_id": packet["source_revision_id"],
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": [],
                "coverage": {
                    "packet_fragment_count": len(fragment_ids),
                    "covered_fragment_ids": fragment_ids,
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            },
            confirm_no_case_data=True,
        )
    inventory = service.inventory(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    packet = service.finalization_packet(run_id)
    assert "applicability" not in packet["inventory"]["coverage"]
    assert packet["applicability_digest"] == inventory["coverage"]["applicability_digest"]
    assert len(canonical_json(packet).encode("utf-8")) <= 65536
    _validate_contract("semantic-finalization-packet.v2.schema.json", packet)


def test_v3_inventory_admits_current_relations_without_knowledge_expiry(
    tmp_path: Path,
) -> None:
    _service, compiler_grant_id, _first_run_id, begun = _v3_fixture(tmp_path)
    root = tmp_path / "vault"
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        relation_grant_id = store.enable_grant(
            writer_id="v013-v3-relation-agent",
            max_sensitivity="restricted",
            operations=("add_relation", "upsert_concept"),
        )["grant_id"]
        concepts = {}
        for key, sensitivity in (
            ("current", "private"),
            ("current-object", "private"),
            ("expired", "private"),
            ("future", "private"),
            ("restricted", "restricted"),
            ("restricted-object", "restricted"),
        ):
            concepts[key] = store.remember(
                grant_id=relation_grant_id,
                idempotency_key=f"v3-relation-{key}",
                title=f"V3 relation {key}",
                body=f"A semantic relation endpoint for {key}.",
                kind="concept",
                operation="upsert_concept",
                sensitivity=sensitivity,
                semantic_key=f"concept:v3-relation-{key}",
                confirm_no_case_data=True,
            )
        fragment = store.connection.execute(
            """
            SELECT source_fragments.fragment_id, source_fragments.locator,
                   source_fragments.text_sha256
            FROM source_revision_bindings_v2
            JOIN source_fragments
              ON source_fragments.source_id = source_revision_bindings_v2.legacy_source_id
            WHERE source_revision_bindings_v2.source_revision_id = ?
            ORDER BY source_fragments.ordinal
            LIMIT 1
            """,
            (begun["source_revision_id"],),
        ).fetchone()
        assert fragment is not None
        evidence_ref = {
            "source_revision_id": begun["source_revision_id"],
            "fragment_id": fragment["fragment_id"],
            "locator": fragment["locator"],
            "quote_sha256": fragment["text_sha256"],
        }
        for key, subject, object_key, valid_from, valid_to in (
            ("current", "current", "current-object", None, None),
            ("expired", "expired", "future", None, "2000-01-01T00:00:00Z"),
            ("future", "current-object", "expired", "2999-01-01T00:00:00Z", None),
            ("restricted", "restricted", "restricted-object", None, None),
        ):
            store.add_relation(
                grant_id=relation_grant_id,
                idempotency_key=f"v3-relation-edge-{key}",
                subject_knowledge_id=concepts[subject]["knowledge_id"],
                predicate="related_to",
                object_knowledge_id=concepts[object_key]["knowledge_id"],
                evidence_refs=[evidence_ref],
                valid_from=valid_from,
                valid_to=valid_to,
                confirm_no_case_data=True,
            )

    profile = KnowledgeOS.open(root).compilations.profile(version="3")
    with KnowledgeOS.open(root) as knowledge_os:
        second_run = knowledge_os.compilations.begin(
            grant_id=compiler_grant_id,
            source_revision_id=begun["source_revision_id"],
            compiler_profile=profile["compiler_profile"],
            compiler_profile_version="3",
            host_identity="v013-v3-agent-second-run",
            prompt_template_id=profile["prompt_template_id"],
            prompt_config_sha256=profile["prompt_config_sha256"],
            plan_configuration_sha256=profile["plan_configuration_sha256"],
            packet_max_fragments=64,
            confirm_no_case_data=True,
        )
    service = SemanticCompilationService(root)
    while packet := service.next_observation_packet(second_run.compilation_run_id):
        fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
        service.stage_observations(
            grant_id=compiler_grant_id,
            compilation_run_id=second_run.compilation_run_id,
            plan={
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": second_run.compilation_run_id,
                "source_revision_id": packet["source_revision_id"],
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": [],
                "coverage": {
                    "packet_fragment_count": len(fragment_ids),
                    "covered_fragment_ids": fragment_ids,
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            },
            confirm_no_case_data=True,
        )
    inventory = service.inventory(
        grant_id=compiler_grant_id,
        compilation_run_id=second_run.compilation_run_id,
        confirm_no_case_data=True,
    )
    assert inventory["coverage"]["runtime_facts"]["existing"]["relation_count"] == 1


def test_v3_packet_filters_existing_knowledge_by_grant_boundary(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-semantic-v3-boundary", scope="project")
    initialize_autonomous_core(root)
    source = tmp_path / "source.md"
    source.write_text(
        "# A bounded semantic source\n"
        "This source contains enough text for a deterministic Source Revision and boundary checks.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(vault, source, source_kind="document", confirm_no_case_data=True)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        public_grant = store.enable_grant(
            writer_id="v013-v3-public-agent",
            allowed_scope="project",
            max_sensitivity="public",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
        broad_grant = store.enable_grant(
            writer_id="v013-v3-broad-agent",
            allowed_scope="project",
            max_sensitivity="restricted",
            operations=("add_relation", "upsert_concept"),
        )["grant_id"]
        visible = store.remember(
            grant_id=broad_grant,
            idempotency_key="v3-visible-concept",
            title="V3 visible concept",
            body="The public concept is visible to the semantic compiler.",
            kind="concept",
            operation="upsert_concept",
            sensitivity="public",
            semantic_key="concept:v3-visible-concept",
            confirm_no_case_data=True,
        )
        hidden = store.remember(
            grant_id=broad_grant,
            idempotency_key="v3-hidden-concept",
            title="V3 restricted concept",
            body="The restricted concept must not cross the public grant boundary.",
            kind="concept",
            operation="upsert_concept",
            sensitivity="restricted",
            semantic_key="concept:v3-restricted-concept",
            confirm_no_case_data=True,
        )
        fragment = store.connection.execute(
            """
            SELECT fragment_id, locator, text_sha256
            FROM source_fragments
            WHERE source_id = ?
            ORDER BY fragment_id
            LIMIT 1
            """,
            (compiled["source"]["source_id"],),
        ).fetchone()
        assert fragment is not None
        store.add_relation(
            grant_id=broad_grant,
            idempotency_key="v3-hidden-relation",
            subject_knowledge_id=visible["knowledge_id"],
            predicate="related_to",
            object_knowledge_id=hidden["knowledge_id"],
            evidence_refs=[
                {
                    "source_revision_id": compiled["identity"]["source_revision_id"],
                    "fragment_id": fragment["fragment_id"],
                    "locator": fragment["locator"],
                    "quote_sha256": fragment["text_sha256"],
                }
            ],
            confirm_no_case_data=True,
        )

    profile = KnowledgeOS.open(root).compilations.profile(version="3")
    begun = CompilationCoordinator(root).begin(
        grant_id=public_grant,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version="3",
        host_identity="v013-v3-public-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        packet_max_fragments=128,
        confirm_no_case_data=True,
    )
    run_id = begun["compilation_run_id"]
    service = SemanticCompilationService(root)
    source_packet = service.next_observation_packet(run_id)
    assert source_packet is not None
    fragment = source_packet["fragments"][0]

    observations = []
    for key, title in (
        ("concept:v3-visible-concept", "V3 visible concept"),
        ("concept:v3-restricted-concept", "V3 restricted concept"),
    ):
        observation = {
            "packet_id": source_packet["packet_id"],
            "semantic_key_candidate": key,
            "kind": "concept",
            "title_candidate": title,
            "body_candidate": "A bounded concept observation.",
            "aliases": [title],
            "source_refs": [
                {
                    "source_revision_id": source_packet["source_revision_id"],
                    "fragment_id": fragment["fragment_id"],
                    "locator": fragment["locator"],
                    "quote_sha256": fragment["text_sha256"],
                }
            ],
            "assertion": None,
            "applicability": None,
            "tags": ["v3-boundary"],
            "reason": "Exercise grant-boundary filtering.",
        }
        observation["observation_id"] = service.observation_id(
            compilation_run_id=run_id,
            packet_id=source_packet["packet_id"],
            observation=observation,
        )
        observations.append(observation)
    fragment_ids = [item["fragment_id"] for item in source_packet["fragments"]]
    service.stage_observations(
        grant_id=public_grant,
        compilation_run_id=run_id,
        plan={
            "schema_version": "deeplaw.source-compilation-observation-plan/v2",
            "compilation_run_id": run_id,
            "source_revision_id": source_packet["source_revision_id"],
            "packet_id": source_packet["packet_id"],
            "expected_audit_head": source_packet["input_audit_head"],
            "observations": observations,
            "coverage": {
                "packet_fragment_count": len(fragment_ids),
                "covered_fragment_ids": fragment_ids,
                "omitted_fragments": [],
                "ratio": 1.0,
            },
            "warnings": [],
        },
        confirm_no_case_data=True,
    )
    while packet := service.next_observation_packet(run_id):
        remaining_ids = [item["fragment_id"] for item in packet["fragments"]]
        service.stage_observations(
            grant_id=public_grant,
            compilation_run_id=run_id,
            plan={
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": run_id,
                "source_revision_id": packet["source_revision_id"],
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": [],
                "coverage": {
                    "packet_fragment_count": len(remaining_ids),
                    "covered_fragment_ids": remaining_ids,
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            },
            confirm_no_case_data=True,
        )
    inventory = service.inventory(
        grant_id=public_grant,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    packet = service.finalization_packet(run_id)
    assert inventory["coverage"]["runtime_facts"]["existing"]["knowledge_ids"] == [
        visible["knowledge_id"]
    ]
    assert inventory["coverage"]["runtime_facts"]["existing"]["relation_count"] == 0
    assert [item["knowledge_id"] for item in packet["existing_canonical_knowledge"]] == [
        visible["knowledge_id"]
    ]
    encoded = canonical_json(packet)
    assert hidden["knowledge_id"] not in encoded
    assert hidden["revision_id"] not in encoded
    assert "concept:v3-restricted-concept" not in encoded


def test_v3_freezes_admitted_candidates_at_run_reference_time(tmp_path: Path) -> None:
    service, grant_id, run_id, begun = _v3_fixture(tmp_path)
    source_packet = service.next_observation_packet(run_id)
    assert source_packet is not None
    fragment = source_packet["fragments"][0]
    observation = {
        "packet_id": source_packet["packet_id"],
        "semantic_key_candidate": "concept:v3-late-candidate",
        "kind": "concept",
        "title_candidate": "V3 late candidate",
        "body_candidate": "A candidate that is not yet canonical.",
        "aliases": ["V3 late candidate"],
        "source_refs": [
            {
                "source_revision_id": source_packet["source_revision_id"],
                "fragment_id": fragment["fragment_id"],
                "locator": fragment["locator"],
                "quote_sha256": fragment["text_sha256"],
            }
        ],
        "assertion": None,
        "applicability": None,
        "tags": ["v3-freeze"],
        "reason": "Exercise run-bound candidate freezing.",
    }
    observation["observation_id"] = service.observation_id(
        compilation_run_id=run_id,
        packet_id=source_packet["packet_id"],
        observation=observation,
    )
    fragment_ids = [item["fragment_id"] for item in source_packet["fragments"]]
    service.stage_observations(
        grant_id=grant_id,
        compilation_run_id=run_id,
        plan={
            "schema_version": "deeplaw.source-compilation-observation-plan/v2",
            "compilation_run_id": run_id,
            "source_revision_id": source_packet["source_revision_id"],
            "packet_id": source_packet["packet_id"],
            "expected_audit_head": source_packet["input_audit_head"],
            "observations": [observation],
            "coverage": {
                "packet_fragment_count": len(fragment_ids),
                "covered_fragment_ids": fragment_ids,
                "omitted_fragments": [],
                "ratio": 1.0,
            },
            "warnings": [],
        },
        confirm_no_case_data=True,
    )
    while packet := service.next_observation_packet(run_id):
        remaining_ids = [item["fragment_id"] for item in packet["fragments"]]
        service.stage_observations(
            grant_id=grant_id,
            compilation_run_id=run_id,
            plan={
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": run_id,
                "source_revision_id": packet["source_revision_id"],
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": [],
                "coverage": {
                    "packet_fragment_count": len(remaining_ids),
                    "covered_fragment_ids": remaining_ids,
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            },
            confirm_no_case_data=True,
        )
    inventory = service.inventory(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(tmp_path / "vault", read_only=True) as store:
        event = store.connection.execute(
            "SELECT recorded_at FROM autonomous_events_v3 WHERE event_hash = ?",
            (begun["input_audit_head"],),
        ).fetchone()
    assert event is not None
    assert inventory["coverage"]["runtime_facts"]["reference_time"] == event["recorded_at"]

    with AutonomousKnowledgeStore(tmp_path / "vault", read_only=False) as store:
        late_grant = store.enable_grant(
            writer_id="v013-v3-late-agent",
            max_sensitivity="public",
            operations=("upsert_concept",),
        )["grant_id"]
        late = store.remember(
            grant_id=late_grant,
            idempotency_key="v3-late-canonical",
            title="V3 late candidate",
            body="This object was created after inventory freeze.",
            kind="concept",
            operation="upsert_concept",
            sensitivity="public",
            semantic_key="concept:v3-late-candidate",
            confirm_no_case_data=True,
        )
    packet = service.finalization_packet(run_id)
    assert late["knowledge_id"] not in canonical_json(packet)
    assert packet["existing_canonical_knowledge"] == []


def test_v3_duty_basis_tamper_is_rejected(tmp_path: Path) -> None:
    service, grant_id, run_id, _ = _v3_fixture(tmp_path)
    while packet := service.next_observation_packet(run_id):
        fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
        service.stage_observations(
            grant_id=grant_id,
            compilation_run_id=run_id,
            plan={
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": run_id,
                "source_revision_id": packet["source_revision_id"],
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": [],
                "coverage": {
                    "packet_fragment_count": len(fragment_ids),
                    "covered_fragment_ids": fragment_ids,
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            },
            confirm_no_case_data=True,
        )
    service.inventory(grant_id=grant_id, compilation_run_id=run_id, confirm_no_case_data=True)
    packet = service.finalization_packet(run_id)
    report = packet["duties"][0]
    tampered = dict(report)
    tampered["deterministic_basis"] = dict(report["deterministic_basis"])
    tampered["deterministic_basis"]["rule_id"] = "tampered-rule"
    with pytest.raises(ValueError):
        from deeplaw.compilation.finalization import SemanticFinalizer

        SemanticFinalizer._validate_duties_v3(
            compilation_run_id=run_id,
            reports=[tampered, *packet["duties"][1:]],
            expected={item["duty_type"]: item for item in packet["duties"]},
        )


def test_v3_satisfied_duty_rejects_omission_reason(tmp_path: Path) -> None:
    service, grant_id, run_id, _ = _v3_fixture(tmp_path)
    while packet := service.next_observation_packet(run_id):
        fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
        service.stage_observations(
            grant_id=grant_id,
            compilation_run_id=run_id,
            plan={
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": run_id,
                "source_revision_id": packet["source_revision_id"],
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": [],
                "coverage": {
                    "packet_fragment_count": len(fragment_ids),
                    "covered_fragment_ids": fragment_ids,
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            },
            confirm_no_case_data=True,
        )
    service.inventory(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    packet = service.finalization_packet(run_id)
    reports = [dict(item) for item in packet["duties"]]
    report = next(item for item in reports if item["applicability"] == "applicable")
    report["status"] = "satisfied"
    report["unresolved_items"] = []
    report["omission_reason"] = "This reason must not accompany a satisfied duty."

    from deeplaw.compilation.finalization import SemanticFinalizer

    with pytest.raises(ValueError, match="satisfied applicable duty"):
        SemanticFinalizer._validate_duties_v3(
            compilation_run_id=run_id,
            reports=reports,
            expected={item["duty_type"]: item for item in packet["duties"]},
        )

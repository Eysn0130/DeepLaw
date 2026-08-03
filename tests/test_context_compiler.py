from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import deeplaw.context_compiler as context_compiler
from deeplaw.context_compiler import compile_context, verify_capsule, verify_capsule_file
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json


def _ready_vault(tmp_path: Path) -> tuple[Path, list[str]]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="context", scope="project")
    identifiers: list[str] = []
    with KnowledgeVault(root, read_only=False) as vault:
        for kind, title, statement, key in (
            (
                "constraint",
                "Storage boundary",
                "Preserve the immutable storage boundary during migration.",
                "storage.boundary",
            ),
            (
                "decision",
                "Runtime decision",
                "The Go runtime remains the production runtime.",
                "runtime.production",
            ),
            (
                "experience",
                "Migration lesson",
                "A prior migration failed because it bypassed the storage boundary.",
                None,
            ),
            (
                "reference",
                "Long reference",
                "Storage boundary evidence " * 500,
                None,
            ),
        ):
            proposal = vault.propose_asset(
                kind=kind,
                memory_tier="experience" if kind == "experience" else "project",
                title=title,
                statement=statement,
                semantic_key=key,
                sensitivity="internal",
            )
            identifiers.append(
                vault.approve_asset(
                    proposal.asset_id,
                    confirm_reviewed=True,
                ).asset_id
            )
    return root, identifiers


def test_context_compiler_prioritizes_constraints_and_respects_hard_budgets(
    tmp_path: Path,
) -> None:
    root, _ = _ready_vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="migrate storage runtime while preserving the boundary",
            confirm_no_case_data=True,
            max_items=3,
            max_chars=700,
        )

    assert capsule["constraints"][0]["title"] == "Storage boundary"
    assert capsule["constraints"][0]["directive_mode"] == "reviewed_instruction"
    assert capsule["decisions"][0]["title"] == "Runtime decision"
    assert capsule["budget"]["selected_items"] <= 3
    assert capsule["budget"]["selected_chars"] <= 700
    assert any("excluded" in gap or "rejected" in gap for gap in capsule["gaps"])
    assert capsule["budget"]["selected_source_refs"] <= 8
    assert capsule["budget"]["selected_source_ref_chars"] <= 4_000
    assert capsule["budget"]["payload_chars"] == len(canonical_json(capsule))
    assert capsule["budget"]["payload_chars"] <= 64_000
    assert capsule["trust_boundary"]["automatic_memory_write"] is False


def test_context_compiler_requires_explicit_case_boundary_confirmation(
    tmp_path: Path,
) -> None:
    root, _ = _ready_vault(tmp_path)
    with (
        KnowledgeVault(root, read_only=True) as vault,
        pytest.raises(ValueError, match="no Analytix case material"),
    ):
        compile_context(
            vault,
            task="compile project context",
            confirm_no_case_data=False,
        )


def test_capsule_digest_detects_tampering_and_reports_staleness(tmp_path: Path) -> None:
    root, _ = _ready_vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="preserve the storage boundary",
            confirm_no_case_data=True,
        )
        initial = verify_capsule(capsule, vault=vault)
    assert initial["valid"] is True
    assert initial["stale"] is False

    tampered = {**capsule, "task": "different task"}
    assert verify_capsule(tampered)["valid"] is False
    timestamp_tampered = {**capsule, "generated_at": "2030-01-01T00:00:00Z"}
    assert verify_capsule(timestamp_tampered)["valid"] is False

    forged = deepcopy(capsule)
    selected = next(
        item
        for group_name in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in forged[group_name]
    )
    selected["content"] = "X" * len(selected["content"])
    context_compiler._seal_capsule(forged)
    with KnowledgeVault(root, read_only=True) as vault:
        forged_result = verify_capsule(forged, vault=vault)
    assert forged_result["digest_valid"] is True
    assert forged_result["valid"] is False
    assert any(not check["valid"] for check in forged_result["asset_checks"])

    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="question",
            memory_tier="project",
            title="Open migration question",
            statement="Which migration phase owns the compatibility check?",
        )
        vault.approve_asset(proposal.asset_id, confirm_reviewed=True)
    with KnowledgeVault(root, read_only=True) as vault:
        stale = verify_capsule(capsule, vault=vault)

    assert stale["valid"] is True
    assert stale["stale"] is True
    assert stale["audit_anchor_valid"] is True


def test_autonomous_capsule_is_verified_by_first_party_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "autonomous-vault"
    initialize_knowledge_vault(root, name="autonomous context", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(writer_id="capsule-test", operations=("remember",))
        revision = store.remember(
            grant_id=grant["grant_id"],
            idempotency_key="autonomous-capsule-verification",
            title="Autonomous capsule verification",
            body="Autonomous Knowledge Capsules must verify through the first-party dispatcher.",
            kind="decision",
            scope="project",
            sensitivity="public",
            confirm_no_case_data=True,
        )
        capsule = store.build_capsule(
            task="Verify the autonomous Knowledge Capsule",
            scope="project",
            max_sensitivity="public",
            confirm_no_case_data=True,
        )
    with KnowledgeVault(root, read_only=True) as vault:
        verified = verify_capsule(capsule, vault=vault)
        tampered = deepcopy(capsule)
        tampered["sections"]["receipts"][0]["revision_id"] = (
            "knowledgerev_000000000000000000000000"
        )
        tampered["capsule_digest"] = context_compiler.sha256_bytes(
            canonical_json(context_compiler._digest_body(tampered)).encode("utf-8")
        )
        tampered["capsule_id"] = context_compiler.stable_id(
            "capsule", tampered["vault_id"], tampered["capsule_digest"]
        )
        tampered_result = verify_capsule(tampered, vault=vault)

    assert capsule["sections"]["receipts"][0]["revision_id"] == revision["revision_id"]
    assert verified["valid"] is True
    assert verified["autonomous_integrity_valid"] is True
    assert verified["receipt_checks"][0]["source_integrity_valid"] is True
    assert verified["receipt_checks"][0]["valid"] is True
    assert tampered_result["digest_valid"] is True
    assert tampered_result["valid"] is False


def test_autonomous_capsule_rejects_canonical_ledger_identity_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ledger-tamper-vault"
    initialize_knowledge_vault(root, name="ledger tamper", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(writer_id="capsule-test", operations=("remember",))
        revision = store.remember(
            grant_id=grant["grant_id"],
            idempotency_key="ledger-tamper-capsule",
            title="Ledger identity receipt",
            body="Capsule verification must bind the canonical Ledger identity.",
            kind="decision",
            scope="project",
            sensitivity="public",
            confirm_no_case_data=True,
        )
        capsule = store.build_capsule(
            task="Verify the Ledger identity receipt",
            scope="project",
            max_sensitivity="public",
            confirm_no_case_data=True,
        )
        store.connection.execute(
            "UPDATE knowledge_objects_v3 SET current_revision_id = NULL "
            "WHERE knowledge_id = ?",
            (revision["knowledge_id"],),
        )
        store.connection.commit()

    with KnowledgeVault(root, read_only=True) as vault:
        verified = verify_capsule(capsule, vault=vault)

    assert verified["autonomous_integrity_valid"] is False
    assert verified["receipt_checks"][0]["valid"] is False
    assert verified["valid"] is False


def test_autonomous_capsule_rechecks_bound_source_bytes(tmp_path: Path) -> None:
    root = tmp_path / "source-tamper-vault"
    initialize_knowledge_vault(root, name="source tamper", scope="project")
    source = tmp_path / "source.md"
    source.write_text(
        "# Evidence\n\nThe immutable evidence supports the capsule receipt.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            sensitivity="public",
            confirm_no_case_data=True,
        )
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(writer_id="capsule-test", operations=("remember",))
        revision = store.remember(
            grant_id=grant["grant_id"],
            idempotency_key="source-tamper-capsule",
            title="Source-bound receipt",
            body="The capsule receipt is bound to immutable source evidence.",
            kind="claim",
            scope="project",
            sensitivity="public",
            source_refs=[{"source_id": compiled["source"]["source_id"]}],
            confirm_no_case_data=True,
        )
        capsule = store.build_capsule(
            task="immutable source evidence capsule receipt",
            scope="project",
            max_sensitivity="public",
            confirm_no_case_data=True,
        )
    assert capsule["sections"]["receipts"][0]["revision_id"] == revision["revision_id"]
    with KnowledgeVault(root, read_only=True) as vault:
        before = verify_capsule(capsule, vault=vault)
        stored_source = vault.source_file_path(compiled["source"]["source_id"])
    stored_source.write_bytes(b"X" + stored_source.read_bytes()[1:])
    with KnowledgeVault(root, read_only=True) as vault:
        after = verify_capsule(capsule, vault=vault)

    assert before["receipt_checks"][0]["source_integrity_valid"] is True
    assert before["valid"] is True
    assert after["autonomous_integrity_valid"] is True
    assert after["receipt_checks"][0]["source_integrity_valid"] is False
    assert after["receipt_checks"][0]["valid"] is False
    assert after["valid"] is False


def test_capsule_file_verification_rejects_a_symbolic_link(tmp_path: Path) -> None:
    root, _ = _ready_vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="preserve the storage boundary",
            confirm_no_case_data=True,
        )
    capsule_path = tmp_path / "capsule.json"
    capsule_path.write_text(__import__("json").dumps(capsule), encoding="utf-8")
    linked_capsule = tmp_path / "linked-capsule.json"
    linked_capsule.symlink_to(capsule_path)

    with pytest.raises(ValueError, match="non-symlink"):
        verify_capsule_file(linked_capsule)


def test_capsule_verification_enforces_the_complete_json_contract(
    tmp_path: Path,
) -> None:
    root, _ = _ready_vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="preserve the storage boundary",
            confirm_no_case_data=True,
        )

    invalid_revision = deepcopy(capsule)
    invalid_revision["vault_revision"] = "1"
    context_compiler._seal_capsule(invalid_revision)
    with pytest.raises(ValueError, match="closed JSON contract"):
        verify_capsule(invalid_revision)

    invalid_uri = deepcopy(capsule)
    selected = next(
        item
        for group_name in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in invalid_uri[group_name]
    )
    selected["uri"] = f"deeplaw://wrong/assets/{selected['asset_id']}"
    context_compiler._seal_capsule(invalid_uri)
    with pytest.raises(ValueError, match="invalid asset URI"):
        verify_capsule(invalid_uri)


def test_capsule_becomes_unusable_when_a_selected_asset_is_revoked(
    tmp_path: Path,
) -> None:
    root, identifiers = _ready_vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="preserve storage boundary",
            confirm_no_case_data=True,
        )
    selected = {
        item["asset_id"]
        for key in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in capsule[key]
    }
    target = next(asset_id for asset_id in identifiers if asset_id in selected)
    with KnowledgeVault(root, read_only=False) as vault:
        vault.revoke_asset(target, reason="No longer applicable.", confirm=True)
    with KnowledgeVault(root, read_only=True) as vault:
        verification = verify_capsule(capsule, vault=vault)

    assert verification["valid"] is False
    assert any(
        check["asset_id"] == target and check["current_status"] == "revoked"
        for check in verification["asset_checks"]
    )


def test_capsule_matches_the_public_closed_contract(tmp_path: Path) -> None:
    root, _ = _ready_vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="storage boundary runtime decision",
            confirm_no_case_data=True,
            max_items=4,
            max_chars=4_000,
        )

    repository = Path(__file__).resolve().parents[1]
    schema = __import__("json").loads(
        (repository / "contracts/knowledge-capsule.v1.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(capsule)


def test_context_never_includes_proposed_or_quarantined_assets(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="gated", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        vault.propose_asset(
            kind="constraint",
            memory_tier="project",
            title="Unreviewed instruction",
            statement="Unreviewed migration instruction should remain hidden.",
        )
        vault.propose_asset(
            kind="reference",
            memory_tier="project",
            title="Quarantined reference",
            statement="Quarantined migration reference should remain hidden.",
            quarantined=True,
        )
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="migration instruction reference",
            confirm_no_case_data=True,
        )

    assert capsule["budget"]["selected_items"] == 0
    assert not capsule["constraints"]
    assert not capsule["knowledge_assets"]
    assert "no active reviewed knowledge asset matched" in capsule["gaps"][0]


def test_context_rejects_a_weak_single_term_candidate_from_a_long_task(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="context-relevance", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        relevant = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="SQLite migration decision",
            statement="Use SQLite for the project storage migration.",
        )
        weak = vault.propose_asset(
            kind="constraint",
            memory_tier="project",
            title="Payroll review",
            statement="Every project payroll export requires a manual review.",
        )
        relevant = vault.approve_asset(relevant.asset_id, confirm_reviewed=True)
        weak = vault.approve_asset(weak.asset_id, confirm_reviewed=True)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="Implement the project SQLite storage migration safely",
            confirm_no_case_data=True,
        )

    serialized = canonical_json(capsule)
    assert relevant.asset_id in serialized
    assert weak.asset_id not in serialized
    assert any("weak lexical candidate" in gap for gap in capsule["gaps"])


def test_context_surfaces_reviewed_contradictions_instead_of_resolving_them(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="contradictions", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        first = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="Migration strategy A",
            statement="Use an in-place migration for the storage system.",
        )
        second = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="Migration strategy B",
            statement="Use a side-by-side migration for the storage system.",
        )
        first = vault.approve_asset(first.asset_id, confirm_reviewed=True)
        second = vault.approve_asset(second.asset_id, confirm_reviewed=True)
        vault.add_relation(
            subject_asset_id=first.asset_id,
            predicate="contradicts",
            object_asset_id=second.asset_id,
            confirm_reviewed=True,
        )
        capsule = compile_context(
            vault,
            task="choose the storage migration strategy",
            confirm_no_case_data=True,
        )

    assert any(relation["predicate"] == "contradicts" for relation in capsule["relations"])
    assert any("contradiction relation" in gap for gap in capsule["gaps"])


def test_context_expands_one_reviewed_relation_without_broad_retrieval(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="relation-expansion", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        seed = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="Mercury release decision",
            statement="The Mercury release uses the blue deployment path.",
        )
        neighbor = vault.propose_asset(
            kind="constraint",
            memory_tier="project",
            title="Artifact integrity constraint",
            statement="Every production artifact must pass signature verification.",
        )
        unrelated = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Office lunch",
            statement="The team lunch starts at noon.",
        )
        seed = vault.approve_asset(seed.asset_id, confirm_reviewed=True)
        neighbor = vault.approve_asset(neighbor.asset_id, confirm_reviewed=True)
        unrelated = vault.approve_asset(unrelated.asset_id, confirm_reviewed=True)
        vault.add_relation(
            subject_asset_id=seed.asset_id,
            predicate="depends_on",
            object_asset_id=neighbor.asset_id,
            confirm_reviewed=True,
        )
        capsule = compile_context(
            vault,
            task="Prepare the Mercury release.",
            confirm_no_case_data=True,
        )

    selected = {
        item["asset_id"]: item
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in capsule[group]
    }
    assert seed.asset_id in selected
    assert neighbor.asset_id in selected
    assert unrelated.asset_id not in selected
    assert selected[seed.asset_id]["selection_reason"] == "lexical_match"
    assert selected[neighbor.asset_id]["selection_reason"] == (
        f"reviewed_relation:depends_on:{seed.asset_id}"
    )
    assert all(item["legal_authority"] is False for item in selected.values())


def test_context_turns_open_questions_into_uri_only_review_actions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="safe-actions", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        question = vault.propose_asset(
            kind="question",
            memory_tier="project",
            title="Migration ownership question",
            statement="Which migration owner approves the compatibility checkpoint?",
        )
        question = vault.approve_asset(question.asset_id, confirm_reviewed=True)
        capsule = compile_context(
            vault,
            task="Resolve migration ownership.",
            confirm_no_case_data=True,
        )

    assert capsule["next_actions"] == [
        f"Review unresolved question asset {question.uri}"
    ]
    assert question.statement not in capsule["next_actions"]


def test_context_does_not_leak_a_restricted_neighbor_through_relations(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="restricted-relation", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        visible = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Visible boundary",
            statement="Visible boundary knowledge is safe for the selected Agent.",
            sensitivity="internal",
        )
        restricted = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Hidden neighbor",
            statement="Hidden neighbor content must stay outside Agent context.",
            sensitivity="restricted",
        )
        visible = vault.approve_asset(visible.asset_id, confirm_reviewed=True)
        restricted = vault.approve_asset(restricted.asset_id, confirm_reviewed=True)
        vault.add_relation(
            subject_asset_id=visible.asset_id,
            predicate="related_to",
            object_asset_id=restricted.asset_id,
            confirm_reviewed=True,
        )
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="visible boundary knowledge",
            confirm_no_case_data=True,
        )

    assert capsule["relations"] == []
    assert restricted.asset_id not in canonical_json(capsule)


def test_context_does_not_expose_a_relation_bound_to_restricted_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="restricted-evidence", scope="project")
    source = tmp_path / "restricted.md"
    source.write_text(
        "# Restricted evidence\nSensitive provenance for a reviewed relation.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        first = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Visible relation subject",
            statement="Visible relation subject supports the migration boundary.",
            sensitivity="internal",
        )
        second = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Visible relation object",
            statement="Visible relation object supports the migration boundary.",
            sensitivity="internal",
        )
        first = vault.approve_asset(first.asset_id, confirm_reviewed=True)
        second = vault.approve_asset(second.asset_id, confirm_reviewed=True)
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            sensitivity="restricted",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        evidence_asset = vault.get_asset(
            compiled["asset_ids"][0],
            include_inactive=True,
        )
        vault.add_relation(
            subject_asset_id=first.asset_id,
            predicate="supports",
            object_asset_id=second.asset_id,
            evidence_fragment_id=evidence_asset.source_refs[0].fragment_id,
            confirm_reviewed=True,
        )
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="migration boundary relation",
            confirm_no_case_data=True,
        )

    assert capsule["relations"] == []


def test_context_keeps_same_titled_sections_from_different_sources(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="cross-source-sections", scope="project")
    first_source = tmp_path / "alpha.md"
    second_source = tmp_path / "beta.md"
    first_source.write_text(
        "# Architecture\n"
        "The Orion migration architecture requires the alpha integrity boundary.",
        encoding="utf-8",
    )
    second_source.write_text(
        "# Architecture\n"
        "The Orion migration architecture requires the beta recovery boundary.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        first = compile_source(
            vault,
            first_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        second = compile_source(
            vault,
            second_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        first_manifest = vault.source_review_manifest(first["source"]["source_id"])
        second_manifest = vault.source_review_manifest(second["source"]["source_id"])
        vault.approve_source_assets(
            first["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=first_manifest["review_manifest_sha256"],
        )
        vault.approve_source_assets(
            second["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=second_manifest["review_manifest_sha256"],
        )
        capsule = compile_context(
            vault,
            task=(
                "Compare the alpha integrity boundary and beta recovery boundary "
                "in the Orion migration architecture."
            ),
            confirm_no_case_data=True,
            max_items=4,
            max_chars=2_000,
        )

    selected = [
        item
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in capsule[group]
    ]
    selected_source_ids = {
        reference["source_id"]
        for item in selected
        for reference in item["source_refs"]
    }
    assert selected_source_ids == {
        first["source"]["source_id"],
        second["source"]["source_id"],
    }


def test_context_keeps_distinct_same_titled_sections_within_one_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="same-source-sections", scope="project")
    source = tmp_path / "architecture.md"
    source.write_text(
        "# Architecture\n"
        "The Orion alpha boundary preserves immutable receipts.\n"
        "# Architecture\n"
        "The Orion beta boundary preserves atomic rollback.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        capsule = compile_context(
            vault,
            task="Compare Orion alpha immutable receipts with Orion beta atomic rollback.",
            confirm_no_case_data=True,
            max_items=4,
            max_chars=2_000,
        )

    selected = [
        item
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in capsule[group]
    ]
    assert {item["asset_id"] for item in selected} == set(compiled["asset_ids"])


def test_context_never_selects_more_source_bound_items_than_it_can_prove(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="source-reference-budget", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        for index in range(context_compiler.MAX_CAPSULE_SOURCE_REFS + 1):
            source = tmp_path / f"boundary-{index}.md"
            source.write_text(
                f"# Boundary {index}\n"
                f"Omega migration boundary record {index} preserves source provenance.",
                encoding="utf-8",
            )
            compiled = compile_source(
                vault,
                source,
                source_kind="document",
                confirm_no_case_data=True,
            )
            review_manifest = vault.source_review_manifest(compiled["source"]["source_id"])
            vault.approve_source_assets(
                compiled["source"]["source_id"],
                confirm_reviewed=True,
                review_manifest_sha256=review_manifest["review_manifest_sha256"],
            )
        capsule = compile_context(
            vault,
            task="Review every Omega migration boundary record and source provenance.",
            confirm_no_case_data=True,
            max_items=context_compiler.MAX_CAPSULE_SOURCE_REFS + 1,
            max_chars=8_000,
        )

    selected = [
        item
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in capsule[group]
    ]
    assert len(selected) <= context_compiler.MAX_CAPSULE_SOURCE_REFS
    assert selected
    assert all(item["source_refs"] for item in selected)
    assert all(item["source_ref_count"] >= 1 for item in selected)
    assert capsule["budget"]["selected_source_refs"] >= len(selected)


def test_capsule_verifier_rejects_source_bound_items_without_embedded_provenance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="source-reference-verifier", scope="project")
    source = tmp_path / "boundary.md"
    source.write_text(
        "# Boundary\nThe Atlas release must retain compact source provenance.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        capsule = compile_context(
            vault,
            task="Apply the Atlas release source-provenance boundary.",
            confirm_no_case_data=True,
        )

    forged = deepcopy(capsule)
    selected = next(
        item
        for group_name in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in forged[group_name]
    )
    assert selected["source_ref_count"] > 0
    selected["source_refs"] = []
    selected["source_refs_truncated"] = True
    forged["evidence"] = []
    forged["budget"]["selected_source_refs"] = 0
    forged["budget"]["selected_source_ref_chars"] = 0
    context_compiler._seal_capsule(forged)

    with (
        KnowledgeVault(root, read_only=True) as vault,
        pytest.raises(ValueError, match="source-bound asset without embedded provenance"),
    ):
        verify_capsule(forged, vault=vault)


def test_context_fairly_budgets_long_assets_and_uses_query_aware_excerpts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="fair-context", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        for index in range(3):
            proposal = vault.propose_asset(
                kind="reference",
                memory_tier="project",
                title=f"Evidence session {index}",
                statement=(
                    f"{'background material ' * 200}"
                    f"nebula checkpoint evidence from session {index} "
                    f"{'trailing material ' * 200}"
                ),
            )
            vault.approve_asset(proposal.asset_id, confirm_reviewed=True)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="nebula checkpoint",
            confirm_no_case_data=True,
            max_items=3,
            max_chars=900,
        )
        verification = verify_capsule(capsule, vault=vault)

    items = capsule["knowledge_assets"]
    assert len(items) == 3
    assert all("nebula checkpoint" in item["content"] for item in items)
    assert all(len(item["content"]) <= 300 for item in items)
    assert capsule["budget"]["selected_chars"] <= 900
    assert verification["valid"] is True


def test_context_diversifies_compiler_parts_before_filling_the_item_budget(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="diverse-context", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        for title in ("Session Alpha", "Session Alpha · part 2", "Session Beta"):
            proposal = vault.propose_asset(
                kind="reference",
                memory_tier="project",
                title=title,
                statement=f"orbit migration evidence recorded in {title}.",
            )
            vault.approve_asset(proposal.asset_id, confirm_reviewed=True)
    with KnowledgeVault(root, read_only=True) as vault:
        capsule = compile_context(
            vault,
            task="orbit migration evidence",
            confirm_no_case_data=True,
            max_items=2,
            max_chars=1_000,
        )

    titles = {item["title"] for item in capsule["knowledge_assets"]}
    assert "Session Beta" in titles
    assert len(titles & {"Session Alpha", "Session Alpha · part 2"}) == 1

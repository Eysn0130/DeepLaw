from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.relation_workflow import (
    pending_relation_carry_forward,
    plan_relation_carry_forward,
    propose_relation_carry_forward,
    review_relation_carry_forward,
)

_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "contracts" / "relation-carry-forward.v1.schema.json")
    .read_text(encoding="utf-8")
)
_VALIDATOR = Draft202012Validator(_SCHEMA)


def _compile_and_approve(vault: KnowledgeVault, source: Path) -> dict[str, object]:
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
    return compiled


def _relation_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="relation workflow", scope="project")
    source = tmp_path / "relations.md"
    source.write_text(
        "# Alpha\nAlpha depends on Beta.\n\n# Beta\nBeta is stable evidence.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = _compile_and_approve(vault, source)
        assets = [vault.get_asset(asset_id) for asset_id in compiled["asset_ids"]]
        relation = vault.add_relation(
            subject_asset_id=assets[0].asset_id,
            predicate="depends_on",
            object_asset_id=assets[1].asset_id,
            evidence_fragment_id=assets[0].source_refs[0].fragment_id,
            confirm_reviewed=True,
        )
    return root, source, relation


def test_unchanged_endpoints_create_review_gated_carry_forward(
    tmp_path: Path,
) -> None:
    root, source, relation = _relation_fixture(tmp_path)
    source.write_text(
        "# Alpha\nAlpha depends on Beta.\n\n"
        "# Beta\nBeta is stable evidence.\n\n"
        "# Gamma\nGamma is newly observed.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        _compile_and_approve(vault, source)
        assert vault.temporal_relations(mode="current")["relations"] == []

        plan = plan_relation_carry_forward(vault)
        _VALIDATOR.validate(plan)
        assert plan["candidate_count"] == 1
        assert plan["carry_forward_candidate_count"] == 1
        assert plan["candidates"][0]["review_mode"] == "carry_forward"
        assert {
            details["lineage_status"]
            for details in plan["candidates"][0]["endpoint_lineage"].values()
        } == {"unchanged"}

        proposed = propose_relation_carry_forward(vault)
        _VALIDATOR.validate(proposed)
        assert proposed["created_count"] == 1
        candidate = proposed["created_candidates"][0]
        assert candidate["approval_inherited"] is False
        assert pending_relation_carry_forward(vault)["total"] == 1
        _VALIDATOR.validate(pending_relation_carry_forward(vault))
        assert vault.temporal_relations(mode="current")["relations"] == []

        reviewed = review_relation_carry_forward(
            vault,
            relation_revision_id=candidate["relation_revision_id"],
            decision="approve",
            confirm_reviewed=True,
            reviewer_id="reviewer@example.test",
            reason="Both unchanged endpoints and the exact successor evidence were reviewed.",
        )
        _VALIDATOR.validate(reviewed)
        current = vault.temporal_relations(mode="current")["relations"]
        integrity = vault.verify_integrity()

    assert reviewed["status"] == "active"
    assert reviewed["approval_inherited"] is False
    assert current[0]["relation_key"] == relation["relation_key"]
    assert current[0]["relation_revision_id"] == reviewed["relation_revision_id"]
    assert current[0]["relation_revision_id"] != relation["relation_revision_id"]
    assert integrity["valid"] is True


def test_modified_endpoint_requires_full_relation_review(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="modified relation", scope="project")
    source = tmp_path / "relations.md"
    source.write_text(
        "# Alpha\nAlpha depends on Beta.\n\n# Beta\nBeta is stable evidence.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = _compile_and_approve(vault, source)
        assets = [vault.get_asset(asset_id) for asset_id in compiled["asset_ids"]]
        vault.add_relation(
            subject_asset_id=assets[0].asset_id,
            predicate="depends_on",
            object_asset_id=assets[1].asset_id,
            evidence_fragment_id=assets[1].source_refs[0].fragment_id,
            confirm_reviewed=True,
        )

        source.write_text(
            "# Alpha\nAlpha conditionally depends on Beta.\n\n"
            "# Beta\nBeta is stable evidence.\n",
            encoding="utf-8",
        )
        _compile_and_approve(vault, source)
        plan = plan_relation_carry_forward(vault)
        proposed = propose_relation_carry_forward(vault)
        _VALIDATOR.validate(plan)
        _VALIDATOR.validate(proposed)

        assert plan["full_review_candidate_count"] == 1
        assert plan["candidates"][0]["review_mode"] == "full_review"
        assert plan["candidates"][0]["endpoint_lineage"]["subject"][
            "lineage_status"
        ] == "modified"
        assert proposed["created_candidates"][0]["status"] == "proposed"
        assert vault.temporal_relations(mode="current")["relations"] == []
        rejected = review_relation_carry_forward(
            vault,
            relation_revision_id=proposed["created_candidates"][0][
                "relation_revision_id"
            ],
            decision="reject",
            confirm_reviewed=True,
            reason="The modified endpoint changes the relation semantics.",
        )
        _VALIDATOR.validate(rejected)
        assert rejected["status"] == "revoked"
        assert pending_relation_carry_forward(vault)["total"] == 0
        assert vault.verify_integrity()["valid"] is True


def test_deleted_endpoint_blocks_relation_successor(tmp_path: Path) -> None:
    root, source, _relation = _relation_fixture(tmp_path)
    source.write_text("# Alpha\nAlpha depends on Beta.\n", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        _compile_and_approve(vault, source)
        plan = plan_relation_carry_forward(vault)
        proposed = propose_relation_carry_forward(vault)
        _VALIDATOR.validate(plan)
        _VALIDATOR.validate(proposed)

        assert plan["candidate_count"] == 0
        assert plan["blocked_count"] == 1
        assert any(
            reason.startswith("object_endpoint_deleted:")
            for reason in plan["blocked"][0]["blocked_reasons"]
        )
        assert proposed["created_count"] == 0
        assert vault.temporal_relations(mode="current")["relations"] == []

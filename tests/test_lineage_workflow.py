from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.lineage_workflow import review_lineage_mapping
from deeplaw.relation_workflow import plan_relation_carry_forward

_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "knowledge-lineage-review.v1.schema.json"
    ).read_text(encoding="utf-8")
)
_VALIDATOR = Draft202012Validator(_SCHEMA)


def _compile_and_approve(
    vault: KnowledgeVault,
    path: Path,
    text: str,
) -> list[str]:
    path.write_text(text, encoding="utf-8")
    compiled = compile_source(
        vault,
        path,
        source_kind="document",
        confirm_no_case_data=True,
    )
    manifest = vault.source_review_manifest(compiled["source"]["source_id"])
    vault.approve_source_assets(
        compiled["source"]["source_id"],
        confirm_reviewed=True,
        review_manifest_sha256=manifest["review_manifest_sha256"],
    )
    return list(compiled["asset_ids"])


def test_reviewed_split_is_cross_key_source_bound_replayable_and_blocks_relations(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="split lineage", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        predecessor, anchor = _compile_and_approve(
            vault,
            tmp_path / "before.md",
            "# Combined policy\nKeep storage local and Agent tools read-only.\n\n"
            "# Release anchor\nThe release depends on the combined policy.\n",
        )
        successors = _compile_and_approve(
            vault,
            tmp_path / "after.md",
            "# Local storage policy\nKeep canonical storage local.\n\n"
            "# Read-only Agent policy\nKeep Agent tools read-only.\n",
        )
        evidence = vault.get_asset(predecessor).source_refs[0]
        vault.add_relation(
            subject_asset_id=predecessor,
            predicate="supports",
            object_asset_id=anchor,
            evidence_fragment_id=evidence.fragment_id,
            confirm_reviewed=True,
        )

        reviewed = review_lineage_mapping(
            vault,
            status="split",
            from_asset_ids=(predecessor,),
            to_asset_ids=tuple(successors),
            confirm_reviewed=True,
            reviewer_id="lineage-reviewer",
            reason="The former combined policy now has two independently sourced successors.",
        )
        _VALIDATOR.validate(reviewed)
        revision_after_review = vault.revision
        replayed = review_lineage_mapping(
            vault,
            status="split",
            from_asset_ids=(predecessor,),
            to_asset_ids=tuple(successors),
            confirm_reviewed=True,
            reviewer_id="lineage-reviewer",
            reason="The former combined policy now has two independently sourced successors.",
        )
        revision_after_replay = vault.revision
        plan = plan_relation_carry_forward(vault)
        lineages = [
            vault.knowledge_lineage(knowledge_key=knowledge_key)
            for knowledge_key in reviewed["knowledge_keys"]
        ]
        integrity = vault.verify_integrity()

    assert reviewed["approval_inherited"] is False
    assert reviewed["replayed"] is False
    assert replayed["replayed"] is True
    assert replayed["transition_ids"] == reviewed["transition_ids"]
    assert replayed["reviewed_at"] == reviewed["reviewed_at"]
    assert revision_after_review == revision_after_replay
    assert all(
        any(transition["status"] == "split" for transition in item["transitions"])
        for item in lineages
    )
    assert plan["candidate_count"] == 0
    assert plan["blocked_count"] == 1
    assert any(
        reason.startswith("subject_endpoint_split:")
        for reason in plan["blocked"][0]["blocked_reasons"]
    )
    assert integrity["valid"] is True

    connection = sqlite3.connect(root / "vault.sqlite3")
    try:
        connection.execute(
            "UPDATE knowledge_lineage_v2 SET mapping_evidence_json = '{}'"
        )
        connection.commit()
    finally:
        connection.close()
    with KnowledgeVault(root, read_only=True) as tampered:
        tamper_result = tampered.verify_integrity()
    assert tamper_result["valid"] is False
    assert tamper_result["state"]["reason"] == "identity_v2_snapshot_mismatch"


@pytest.mark.parametrize(
    ("status", "from_count", "to_count"),
    (("merged", 2, 1), ("ambiguous", 1, 1)),
)
def test_reviewed_cross_key_merge_and_ambiguity_are_visible_from_every_key(
    tmp_path: Path,
    status: str,
    from_count: int,
    to_count: int,
) -> None:
    root = tmp_path / status
    initialize_knowledge_vault(root, name=f"{status} lineage", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        predecessors = _compile_and_approve(
            vault,
            tmp_path / f"{status}-before.md",
            "# First predecessor\nThe first predecessor has exact evidence.\n\n"
            "# Second predecessor\nThe second predecessor has exact evidence.\n",
        )[:from_count]
        successors = _compile_and_approve(
            vault,
            tmp_path / f"{status}-after.md",
            "# Reviewed successor\nThe reviewed successor has exact evidence.\n",
        )[:to_count]
        reviewed = review_lineage_mapping(
            vault,
            status=status,
            from_asset_ids=tuple(predecessors),
            to_asset_ids=tuple(successors),
            confirm_reviewed=True,
            reviewer_id="lineage-reviewer",
            reason=f"The operator explicitly reviewed this {status} mapping.",
        )
        _VALIDATOR.validate(reviewed)
        views = [
            vault.knowledge_lineage(knowledge_key=knowledge_key)
            for knowledge_key in reviewed["knowledge_keys"]
        ]
        integrity = vault.verify_integrity()

    assert len(reviewed["transition_ids"]) == len(reviewed["knowledge_keys"])
    assert all(
        any(transition["status"] == status for transition in view["transitions"])
        for view in views
    )
    assert integrity["valid"] is True


def test_advanced_cli_records_an_explicit_lineage_review(tmp_path: Path) -> None:
    root = tmp_path / "cli-vault"
    initialize_knowledge_vault(root, name="CLI lineage review", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        predecessor = _compile_and_approve(
            vault,
            tmp_path / "cli-before.md",
            "# Previous interpretation\nThe previous interpretation has exact evidence.\n",
        )[0]
        successor = _compile_and_approve(
            vault,
            tmp_path / "cli-after.md",
            "# Candidate interpretation\nThe candidate interpretation has exact evidence.\n",
        )[0]

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "deeplaw",
            "knowledge",
            "lineage",
            "--vault",
            str(root),
            "--map-status",
            "ambiguous",
            "--from-asset-id",
            predecessor,
            "--to-asset-id",
            successor,
            "--reviewer-id",
            "cli-reviewer",
            "--reason",
            "The mapping remains ambiguous after explicit source review.",
            "--confirm-reviewed",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert process.returncode == 0, process.stderr
    reviewed = json.loads(process.stdout)
    _VALIDATOR.validate(reviewed)
    assert reviewed["status"] == "ambiguous"
    assert reviewed["approval_inherited"] is False

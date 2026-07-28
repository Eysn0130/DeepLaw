from __future__ import annotations

from pathlib import Path

import pytest

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.operator_workbench import (
    _asset_ids_from_visible_rows,
    _run_curses,
    operator_review_action,
    operator_review_lineage_by_selection,
    operator_snapshot,
    review_side_by_side,
)


class _SmokeScreen:
    def getmaxyx(self) -> tuple[int, int]:
        return 30, 120

    def erase(self) -> None:
        return None

    def addnstr(self, *_args: object) -> None:
        return None

    def hline(self, *_args: object) -> None:
        return None

    def refresh(self) -> None:
        return None

    def keypad(self, _enabled: bool) -> None:
        return None

    def getkey(self) -> str:
        return "q"


def test_workbench_snapshot_and_review_actions_use_canonical_review_service(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="workbench", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        first = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="Storage",
            statement="Use the local canonical store.",
            semantic_key="decision.storage",
        )
        second = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="Audit",
            statement="Retain an append-only audit chain.",
            semantic_key="decision.audit",
        )

    snapshot = operator_snapshot(root)
    detail = review_side_by_side(root, first.asset_id)
    assert {"source-tree", "source-diff", "review", "feedback"} <= set(
        snapshot["panels"]
    )
    assert snapshot["canonical_write_boundary"] == "review service only"
    assert detail["asset"]["asset_id"] == first.asset_id

    merged = operator_review_action(
        root,
        action="merge",
        asset_ids=(first.asset_id, second.asset_id),
        reviewer_id="local-operator",
        reason="The two decisions form one reviewed candidate.",
        confirm_reviewed=True,
        title="Local audited storage",
        statement="Use the local canonical store with an append-only audit chain.",
    )
    assert merged["created_proposal_count"] == 1
    assert merged["review_required"] is True
    merged_id = merged["created_proposals"][0]["asset_id"]

    approved = operator_review_action(
        root,
        action="approve",
        asset_ids=(merged_id,),
        reviewer_id="local-operator",
        reason="Source-free manual synthesis was explicitly reviewed.",
        confirm_reviewed=True,
        confirm_quarantined=True,
    )
    assert approved["decisions"][0]["asset"]["status"] == "active"


def test_workbench_split_outputs_remain_quarantined(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="workbench split", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="constraint",
            memory_tier="project",
            title="Combined constraint",
            statement="Keep data local and keep tools read-only.",
        )

    result = operator_review_action(
        root,
        action="split",
        asset_ids=(proposal.asset_id,),
        reviewer_id="local-operator",
        reason="The statement contains two independently reviewable constraints.",
        confirm_reviewed=True,
        split_items=(
            ("Local data", "Keep canonical data local."),
            ("Read-only tools", "Keep Agent tools read-only."),
        ),
    )

    assert result["created_proposal_count"] == 2
    assert all(item["status"] == "quarantined" for item in result["created_proposals"])
    assert result["approval_inherited"] is False


def test_curses_workbench_starts_and_quits_on_local_vault(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="workbench smoke", scope="project")
    monkeypatch.setattr("curses.curs_set", lambda _value: None)

    _run_curses(_SmokeScreen(), root)


def test_workbench_reviews_cross_key_lineage_by_rows_without_copied_ids(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lineage-vault"
    initialize_knowledge_vault(root, name="workbench lineage", scope="project")
    source = tmp_path / "lineage.md"
    source.write_text(
        "# Combined rule\nKeep storage local and tools read-only.\n\n"
        "# Local rule\nKeep canonical storage local.\n\n"
        "# Tool rule\nKeep Agent tools read-only.\n",
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

    snapshot = operator_snapshot(root)
    positions = {
        item["title"]: item["position"]
        for item in snapshot["lineage_mapping_inventory"]["items"]
    }
    reviewed = operator_review_lineage_by_selection(
        root,
        status="split",
        from_positions=(positions["Combined rule"],),
        to_positions=(positions["Local rule"], positions["Tool rule"]),
        reviewer_id="workbench-reviewer",
        reason="The operator reviewed two exact source-bound successors.",
        confirm_reviewed=True,
    )

    assert "lineage-mapping" in snapshot["panels"]
    assert reviewed["status"] == "split"
    assert reviewed["selection"]["internal_ids_copied_by_operator"] is False
    assert reviewed["approval_inherited"] is False


def test_workbench_source_diff_follows_lifecycle_link_not_timestamp_order(
    tmp_path: Path,
) -> None:
    root = tmp_path / "diff-vault"
    initialize_knowledge_vault(root, name="workbench diff", scope="project")
    source = tmp_path / "policy.md"
    source.write_text(
        "# Policy\nUse the reviewed amber deployment path.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        first = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(first["source"]["source_id"])
        vault.approve_source_assets(
            first["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        source.write_text(
            "# Policy\nUse the reviewed emerald deployment path.\n",
            encoding="utf-8",
        )
        second = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )

    snapshot = operator_snapshot(root)

    assert len(snapshot["source_diffs"]) == 1
    assert snapshot["source_diffs"][0]["old_source_id"] == first["source"]["source_id"]
    assert snapshot["source_diffs"][0]["new_source_id"] == second["source"]["source_id"]


def test_workbench_source_bound_split_is_atomic_and_retains_exact_lineage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source-bound-split"
    initialize_knowledge_vault(root, name="source-bound split", scope="project")
    source = tmp_path / "combined.md"
    source.write_text(
        "# Combined rule\nKeep canonical data local and keep Agent tools read-only.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        predecessor_id = compiled["asset_ids"][0]
        predecessor = vault.get_asset(predecessor_id, include_inactive=True)
        predecessor_identity = vault._asset_revision_identity(predecessor_id)
        assert predecessor_identity is not None

    result = operator_review_action(
        root,
        action="split",
        asset_ids=(predecessor_id,),
        reviewer_id="local-operator",
        reason="The two constraints have independent review and lifecycle semantics.",
        confirm_reviewed=True,
        split_items=(
            ("Local canonical data", "Keep canonical data local."),
            ("Read-only Agent tools", "Keep Agent tools read-only."),
        ),
    )

    assert result["atomic"] is True
    assert result["lineage_review"]["status"] == "split"
    assert result["approval_inherited"] is False
    assert result["created_proposal_count"] == 2
    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.get_asset(predecessor_id, include_inactive=True).status == "revoked"
        successor_identities = []
        for item in result["created_proposals"]:
            successor = vault.get_asset(item["asset_id"], include_inactive=True)
            assert successor.status == "quarantined"
            assert successor.verification == "source_bound"
            assert successor.source_refs == predecessor.source_refs
            identity = vault._asset_revision_identity(successor.asset_id)
            assert identity is not None
            successor_identities.append(identity)
        involved_keys = {
            predecessor_identity["knowledge_key"],
            *(identity["knowledge_key"] for identity in successor_identities),
        }
        rows = vault.connection.execute(
            """
            SELECT knowledge_key, status
            FROM knowledge_lineage_v2
            WHERE status = 'split'
            """
        ).fetchall()
        assert {row["knowledge_key"] for row in rows} == involved_keys
        governance = vault.connection.execute(
            """
            SELECT review_status, lifecycle_status, activation_status, reviewer_id
            FROM governance_revisions_v2
            WHERE subject_kind = 'asset_revision' AND subject_id = ?
            ORDER BY recorded_at DESC, governance_revision DESC
            LIMIT 1
            """,
            (predecessor_identity["asset_revision_id"],),
        ).fetchone()
        assert dict(governance) == {
            "review_status": "human_verified",
            "lifecycle_status": "revoked",
            "activation_status": "inactive",
            "reviewer_id": "local-operator",
        }
        assert vault.verify_integrity()["valid"] is True


def test_workbench_source_bound_merge_unions_references_and_records_mapping(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source-bound-merge"
    initialize_knowledge_vault(root, name="source-bound merge", scope="project")
    source = tmp_path / "two-rules.md"
    source.write_text(
        "# Storage\nKeep canonical data local.\n\n"
        "# Tools\nKeep Agent tools read-only.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        predecessor_ids = tuple(compiled["asset_ids"])
        assert len(predecessor_ids) == 2

    result = operator_review_action(
        root,
        action="merge",
        asset_ids=predecessor_ids,
        reviewer_id="local-operator",
        reason="The operator reviewed one combined source-bound rule.",
        confirm_reviewed=True,
        title="Local read-only operation",
        statement="Keep canonical data local and Agent tools read-only.",
    )

    assert result["lineage_review"]["status"] == "merged"
    merged_id = result["created_proposals"][0]["asset_id"]
    with KnowledgeVault(root, read_only=True) as vault:
        merged = vault.get_asset(merged_id, include_inactive=True)
        assert merged.verification == "source_bound"
        assert len(merged.source_refs) == 2
        assert all(
            vault.get_asset(asset_id, include_inactive=True).status == "revoked"
            for asset_id in predecessor_ids
        )
        assert vault._asset_revision_identity(merged_id) is not None
        assert vault.verify_integrity()["valid"] is True


def test_workbench_pending_source_bound_edit_retains_refs_and_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source-bound-edit"
    initialize_knowledge_vault(root, name="source-bound edit", scope="project")
    source = tmp_path / "rule.md"
    source.write_text("# Rule\nKeep evidence exact.\n", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        predecessor_id = compiled["asset_ids"][0]
        predecessor = vault.get_asset(predecessor_id, include_inactive=True)
        predecessor_identity = vault._asset_revision_identity(predecessor_id)
        assert predecessor_identity is not None

    result = operator_review_action(
        root,
        action="edit",
        asset_ids=(predecessor_id,),
        reviewer_id="local-operator",
        reason="Clarified wording without changing the exact evidence.",
        confirm_reviewed=True,
        title="Exact evidence",
        statement="Keep every evidence reference exact.",
    )

    edited_id = result["created_proposals"][0]["asset_id"]
    with KnowledgeVault(root, read_only=True) as vault:
        edited = vault.get_asset(edited_id, include_inactive=True)
        edited_identity = vault._asset_revision_identity(edited_id)
        assert edited.source_refs == predecessor.source_refs
        assert edited_identity is not None
        assert edited_identity["knowledge_key"] == predecessor_identity["knowledge_key"]
        assert vault.get_asset(predecessor_id, include_inactive=True).status == "revoked"
        assert vault.verify_integrity()["valid"] is True


def test_workbench_source_bound_transform_rolls_back_every_output_on_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "atomic-transform"
    initialize_knowledge_vault(root, name="atomic transform", scope="project")
    source = tmp_path / "combined.md"
    source.write_text("# Combined\nKeep A and B.\n", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        predecessor_id = compiled["asset_ids"][0]
        original_count = vault.connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]

    with pytest.raises(ValueError, match="asset title"):
        operator_review_action(
            root,
            action="split",
            asset_ids=(predecessor_id,),
            reviewer_id="local-operator",
            reason="The second output deliberately violates a closed bound.",
            confirm_reviewed=True,
            split_items=(("A", "Keep A."), ("x" * 501, "Keep B.")),
        )

    with KnowledgeVault(root, read_only=True) as vault:
        asset_count = vault.connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        assert asset_count == original_count
        assert vault.get_asset(predecessor_id, include_inactive=True).status == "proposed"
        assert vault.verify_integrity()["valid"] is True


def test_workbench_visible_merge_rows_never_require_copied_asset_ids() -> None:
    visible = [
        {"review_kind": "asset", "asset_id": "asset_" + "1" * 24},
        {"review_kind": "relation", "relation_revision_id": "relationrev_x"},
        {"review_kind": "asset", "asset_id": "asset_" + "2" * 24},
    ]

    assert _asset_ids_from_visible_rows(visible, (1, 3)) == (
        "asset_" + "1" * 24,
        "asset_" + "2" * 24,
    )
    with pytest.raises(ValueError, match="does not identify"):
        _asset_ids_from_visible_rows(visible, (2,))


def test_workbench_batch_approval_is_atomic_and_quarantine_requires_confirmation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "atomic-review"
    initialize_knowledge_vault(root, name="atomic review", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        first = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="First pending decision",
            statement="Keep the first decision local and explicitly reviewed.",
        )
        second = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="Second pending decision",
            statement="Keep the second decision local and explicitly reviewed.",
        )
        quarantined = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="Quarantined decision",
            statement="This candidate requires a separate quarantine confirmation.",
            quarantined=True,
        )
        vault.approve_asset(
            second.asset_id,
            confirm_reviewed=True,
            reviewer_id="setup-reviewer",
        )

    with pytest.raises(ValueError, match="only proposed or quarantined"):
        operator_review_action(
            root,
            action="approve",
            asset_ids=(first.asset_id, second.asset_id),
            reviewer_id="local-operator",
            reason="The second decision makes this batch fail after the first mutation.",
            confirm_reviewed=True,
        )

    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.get_asset(first.asset_id, include_inactive=True).status == "proposed"
        assert vault.get_asset(second.asset_id, include_inactive=True).status == "active"
        assert vault.verify_integrity()["valid"] is True

    with pytest.raises(ValueError, match="only proposed or quarantined"):
        operator_review_action(
            root,
            action="reject",
            asset_ids=(first.asset_id, second.asset_id),
            reviewer_id="local-operator",
            reason="The second decision makes rejection roll back the full batch.",
            confirm_reviewed=True,
        )

    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.get_asset(first.asset_id, include_inactive=True).status == "proposed"
        assert vault.get_asset(second.asset_id, include_inactive=True).status == "active"
        assert vault.verify_integrity()["valid"] is True

    with pytest.raises(ValueError, match="quarantined asset approval"):
        operator_review_action(
            root,
            action="approve",
            asset_ids=(quarantined.asset_id,),
            reviewer_id="local-operator",
            reason="No separate quarantine confirmation was supplied.",
            confirm_reviewed=True,
        )

    accepted = operator_review_action(
        root,
        action="approve",
        asset_ids=(quarantined.asset_id,),
        reviewer_id="local-operator",
        reason="The operator separately confirmed the quarantine risk.",
        confirm_reviewed=True,
        confirm_quarantined=True,
    )
    assert accepted["atomic"] is True
    assert accepted["decisions"][0]["asset"]["status"] == "active"

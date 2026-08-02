from __future__ import annotations

from pathlib import Path

import pytest

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_maintenance import (
    create_knowledge_snapshot,
    garbage_collect_derived,
    knowledge_doctor,
    restore_knowledge_snapshot,
    verify_knowledge_snapshot,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault


def _active_vault(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="maintenance", scope="project")
    source = tmp_path / "source.md"
    source.write_text(
        "# Snapshot procedure\nVerify every source hash before restoring a snapshot.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(result["source"]["source_id"])
        vault.approve_source_assets(
            result["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        return root, result["asset_ids"][0]


def test_snapshot_verifies_and_restores_while_retaining_previous_vault(
    tmp_path: Path,
) -> None:
    root, active_asset_id = _active_vault(tmp_path)
    snapshot = tmp_path / "snapshot"
    with KnowledgeVault(root, read_only=True) as vault:
        expected_revision = vault.revision
        created = create_knowledge_snapshot(vault, snapshot)
        assert created["valid"] is True

    with KnowledgeVault(root, read_only=False) as vault:
        vault.propose_asset(
            kind="question",
            memory_tier="project",
            title="Post-snapshot question",
            statement="Which operator validates the post-snapshot state?",
        )
        assert vault.revision > expected_revision

    restored = restore_knowledge_snapshot(root, snapshot=snapshot, confirm=True)
    assert restored["valid"] is True
    assert Path(restored["retained_previous_vault"]).is_dir()
    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.revision == expected_revision
        assert vault.get_asset(active_asset_id).status == "active"
        assert vault.review_queue()["total"] == 0
        assert vault.verify_integrity()["valid"] is True


def test_snapshot_verification_detects_file_tampering(tmp_path: Path) -> None:
    root, _ = _active_vault(tmp_path)
    snapshot = tmp_path / "snapshot"
    with KnowledgeVault(root, read_only=True) as vault:
        create_knowledge_snapshot(vault, snapshot)
    source_copy = next((snapshot / "vault" / "sources").iterdir())
    source_copy.write_bytes(source_copy.read_bytes() + b"tampered")
    assert verify_knowledge_snapshot(snapshot)["valid"] is False


def test_failed_post_swap_verification_restores_the_previous_vault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _active_vault(tmp_path)
    snapshot = tmp_path / "snapshot"
    with KnowledgeVault(root, read_only=True) as vault:
        create_knowledge_snapshot(vault, snapshot)
    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="question",
            memory_tier="project",
            title="Retained state",
            statement="This post-snapshot state must survive a failed restore.",
        )
        expected_revision = vault.revision

    original_verify = KnowledgeVault.verify_integrity
    failed_once = False

    def fail_post_swap_once(vault: KnowledgeVault) -> dict[str, object]:
        nonlocal failed_once
        result = original_verify(vault)
        if vault.root == root and not failed_once:
            failed_once = True
            return {**result, "valid": False}
        return result

    monkeypatch.setattr(KnowledgeVault, "verify_integrity", fail_post_swap_once)
    with pytest.raises(RuntimeError, match="post-swap verification"):
        restore_knowledge_snapshot(root, snapshot=snapshot, confirm=True)

    assert failed_once is True
    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.revision == expected_revision
        assert vault.get_asset(proposal.asset_id, include_inactive=True).title == "Retained state"
        assert vault.verify_integrity()["valid"] is True
    assert not list(tmp_path.glob(".vault.failed-restore-*.tmp"))


def test_doctor_and_gc_only_repair_removable_state(tmp_path: Path) -> None:
    root, _ = _active_vault(tmp_path)
    temporary = root / "derived" / "orphan.tmp"
    temporary.parent.mkdir(mode=0o700)
    temporary.write_text("derived temporary state", encoding="utf-8")
    temporary.chmod(0o600)

    report = knowledge_doctor(root)
    assert report["canonical_valid"] is True
    assert "derived/orphan.tmp" in report["checks"]["orphans"]["temporary_files"]

    with KnowledgeVault(root, read_only=True) as vault:
        dry_run = garbage_collect_derived(vault, confirm=False, dry_run=True)
        assert dry_run["candidate_count"] == 1
        assert temporary.is_file()
        collected = garbage_collect_derived(vault, confirm=True, dry_run=False)
        assert collected["removed"] == ["derived/orphan.tmp"]
        assert collected["canonical_database_modified"] is False
        assert vault.verify_integrity()["valid"] is True
    assert not temporary.exists()


def test_doctor_repairs_missing_search_index_without_weakening_canonical_checks(
    tmp_path: Path,
) -> None:
    root, _ = _active_vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        vault.connection.execute("DELETE FROM asset_search")
        vault.connection.commit()
        assert vault.verify_integrity()["state"]["reason"] == (
            "search_index_inventory_mismatch"
        )

    repaired = knowledge_doctor(root, repair_derived=True)

    assert repaired["canonical_valid"] is True
    assert repaired["ready"] is True
    assert repaired["repair"]["valid"] is True
    assert repaired["checks"]["canonical_integrity"]["valid"] is True
    assert repaired["checks"]["orphans"]["valid"] is True

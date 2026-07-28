from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from deeplaw import knowledge_compiler, knowledge_store
from deeplaw.context_compiler import compile_context
from deeplaw.knowledge_compiler import compile_directory, compile_source
from deeplaw.knowledge_feedback import (
    create_run_receipt,
    record_structured_feedback,
    replay_feedback,
)
from deeplaw.knowledge_models import utc_now
from deeplaw.knowledge_store import (
    KnowledgeVault,
    create_knowledge_migration_backup,
    initialize_knowledge_vault,
    knowledge_vault_permission_report,
    restore_knowledge_migration_backup,
    verify_knowledge_migration_backup,
)


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="control", scope="project")
    return root


def _validate_contract(record: dict[str, object], contract_name: str) -> None:
    repository = Path(__file__).resolve().parents[1]
    schema = json.loads((repository / "contracts" / contract_name).read_text(encoding="utf-8"))
    portable = {key: record[key] for key in schema["properties"] if key in record}
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(portable)


def test_source_update_is_review_gated_and_switches_versions_atomically(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "architecture.md"
    source.write_text(
        "# Decision\nThe Mercury runtime uses the blue deployment path.\n"
        "# Constraint\nEvery Mercury artifact must retain a source digest.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        first = compile_source(
            vault,
            source,
            source_kind="document",
            typed_extraction="deterministic-v1",
            confirm_no_case_data=True,
        )
        first_assets = [
            vault.get_asset(asset_id, include_inactive=True) for asset_id in first["asset_ids"]
        ]
        manifest = vault.source_review_manifest(first["source"]["source_id"])
        with pytest.raises(ValueError, match="exact review manifest"):
            vault.approve_source_assets(
                first["source"]["source_id"],
                confirm_reviewed=True,
                review_manifest_sha256="",
            )
        approval = vault.approve_source_assets(
            first["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="maintainer@example.test",
            review_reason="Reviewed both exact source sections.",
        )
        assert approval["source_activated"] is True
        assert approval["review_receipt"]["signature"] is None
        _validate_contract(
            approval["review_receipt"],
            "knowledge-review-receipt.v1.schema.json",
        )
        assert vault.get_review_receipt(approval["review_receipt"]["review_receipt_id"])["valid"]
        first_revision = vault.revision

    assert {asset.kind for asset in first_assets} == {"decision", "constraint"}
    source.write_text(
        "# Decision\nThe Mercury runtime uses the green deployment path.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        second = compile_source(
            vault,
            source,
            source_kind="document",
            typed_extraction="deterministic-v1",
            confirm_no_case_data=True,
        )
        assert second["source"]["source_key"] == first["source"]["source_key"]
        assert second["source"]["previous_source_id"] == first["source"]["source_id"]
        assert second["source"]["status"] == "pending"
        assert vault.source_info(first["source"]["source_id"])["status"] == "active"
        assert vault.search("blue deployment path").results
        with pytest.raises(ValueError, match="atomic update"):
            vault.approve_asset(
                second["asset_ids"][0],
                confirm_reviewed=True,
            )
        diff = vault.source_diff(
            first["source"]["source_id"],
            second["source"]["source_id"],
        )
        assert diff["changed_count"] == 1
        assert diff["removed_count"] == 1
        manifest = vault.source_review_manifest(second["source"]["source_id"])
        switched = vault.approve_source_assets(
            second["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="maintainer@example.test",
            review_reason="Reviewed the successor and deleted section.",
        )
        assert switched["source_activated"] is True
        assert switched["revoked_prior_asset_count"] == 1
        assert vault.source_info(first["source"]["source_id"])["status"] == "superseded"
        assert vault.source_info(second["source"]["source_id"])["status"] == "active"
        superseded_governance = vault.connection.execute(
            """
            SELECT lifecycle_status, activation_status
            FROM governance_revisions_v2
            WHERE subject_kind = 'source_revision' AND subject_id = ?
            ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
            """,
            (first["identity"]["source_revision_id"],),
        ).fetchone()
        assert tuple(superseded_governance) == ("superseded", "inactive")
        assert vault.search("green deployment path").results
        current_results = vault.search("blue deployment path").results
        assert all(
            result.asset_id not in {asset.asset_id for asset in first_assets}
            for result in current_results
        )
        assert vault.revision > first_revision
        removal = vault.remove_source(
            second["source"]["source_id"],
            reason="The reviewed source was intentionally retired.",
            confirm=True,
        )
        assert removal["removed_asset_count"] == 1
        assert vault.source_info(second["source"]["source_id"])["status"] == "removed"
        removed_governance = vault.connection.execute(
            """
            SELECT lifecycle_status, activation_status
            FROM governance_revisions_v2
            WHERE subject_kind = 'source_revision' AND subject_id = ?
            ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
            """,
            (second["identity"]["source_revision_id"],),
        ).fetchone()
        assert tuple(removed_governance) == ("removed", "inactive")
        removed_lineage = vault.knowledge_lineage(asset_id=second["asset_ids"][0])
        assert any(
            transition["status"] == "deleted"
            and transition["to_asset_revision_ids"] == []
            for transition in removed_lineage["transitions"]
        )
        assert not vault.search("green deployment path").results
        assert vault.verify_integrity()["valid"] is True


def test_directory_ingest_dry_run_and_repeated_run_are_bounded_and_idempotent(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "one.md").write_text(
        "# One\nOrion source one contains stable project knowledge for compilation.",
        encoding="utf-8",
    )
    (sources / "two.txt").write_text(
        "Orion source two contains stable project knowledge for compilation.",
        encoding="utf-8",
    )
    (sources / "skip.bin").write_bytes(b"not supported")
    with KnowledgeVault(root, read_only=False) as vault:
        dry_run = compile_directory(
            vault,
            sources,
            recursive=True,
            include=("*.md", "*.txt"),
            confirm_no_case_data=True,
            dry_run=True,
        )
        assert dry_run["files_admitted"] == 2
        assert dry_run["write_performed"] is False
        assert vault.inspect()["source_count"] == 0
        first = compile_directory(
            vault,
            sources,
            recursive=True,
            include=("*.md", "*.txt"),
            confirm_no_case_data=True,
        )
        revision = vault.revision
        repeated = compile_directory(
            vault,
            sources,
            recursive=True,
            include=("*.md", "*.txt"),
            confirm_no_case_data=True,
        )

    assert first["files_admitted"] == 2
    assert first["files_failed"] == 0
    assert repeated["source_ids"] == first["source_ids"]
    assert repeated["manifest_sha256"] == first["manifest_sha256"]
    with KnowledgeVault(root, read_only=True) as vault:
        assert revision == vault.revision


def test_directory_ingest_rejects_an_unbounded_entry_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "one.md").write_text("# One\nOne bounded entry.", encoding="utf-8")
    (sources / "two.md").write_text("# Two\nA second bounded entry.", encoding="utf-8")
    monkeypatch.setattr(knowledge_compiler, "_MAX_DIRECTORY_ENTRIES", 1)
    with (
        KnowledgeVault(root, read_only=False) as vault,
        pytest.raises(ValueError, match="entry scan bound"),
    ):
        compile_directory(
            vault,
            sources,
            recursive=True,
            confirm_no_case_data=True,
            dry_run=True,
        )


def test_run_receipt_and_structured_feedback_form_a_tamper_evident_loop(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    capsule_path = tmp_path / "capsule.json"
    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="constraint",
            memory_tier="project",
            title="Mercury source boundary",
            statement="Every Mercury change must retain exact source provenance.",
        )
        asset = vault.approve_asset(
            proposal.asset_id,
            confirm_reviewed=True,
            reviewer_id="maintainer@example.test",
        )
        capsule = compile_context(
            vault,
            task="Preserve Mercury source provenance during the change.",
            confirm_no_case_data=True,
        )
        capsule_path.write_text(json.dumps(capsule), encoding="utf-8")
        run = create_run_receipt(
            vault,
            capsule_path=capsule_path,
            status="partial",
            host_name="codex",
            host_version="test",
            latency_ms=12.5,
        )
        _validate_contract(run, "knowledge-run-receipt.v1.schema.json")
        run_payload_fields = {
            "schema_version",
            "vault_id",
            "vault_revision",
            "audit_head",
            "capsule_id",
            "capsule_digest",
            "task_sha256",
            "goal_sha256",
            "selected_asset_ids",
            "source_ids",
            "host",
            "model",
            "started_at",
            "finished_at",
            "status",
            "outcome_artifact_sha256",
            "metrics",
        }
        mismatched_payload = {field: run[field] for field in run_payload_fields}
        mismatched_payload["capsule_digest"] = "0" * 64
        with pytest.raises(ValueError, match="does not match its verified knowledge Capsule"):
            vault.record_run_receipt(mismatched_payload, capsule=capsule)
        unrelated = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Unrelated Zebra record",
            statement="A Zebra-only record was not selected for the Mercury task.",
        )
        unrelated = vault.approve_asset(
            unrelated.asset_id,
            confirm_reviewed=True,
            reviewer_id="maintainer@example.test",
        )
        with pytest.raises(ValueError, match="bound run Capsule"):
            record_structured_feedback(
                vault,
                run_id=run["run_id"],
                outcome="partial",
                helpful_asset_ids=(unrelated.asset_id,),
                observation="This attribution points outside the run Capsule.",
                recommended_action="Reject the invalid attribution.",
            )
        feedback = record_structured_feedback(
            vault,
            run_id=run["run_id"],
            outcome="partial",
            helpful_asset_ids=(asset.asset_id,),
            missing_knowledge=("The rollback owner is not documented.",),
            observation="The source boundary was useful but ownership was missing.",
            recommended_action="Review and add a source-bound rollback owner decision.",
        )
        _validate_contract(feedback, "knowledge-feedback-ledger.v1.schema.json")
        assert feedback["proposal"]["status"] == "proposed"
        assert vault.get_run_receipt(run["run_id"])["valid"] is True
        assert vault.get_feedback(feedback["feedback_id"])["valid"] is True
        replay = replay_feedback(
            vault,
            feedback_id=feedback["feedback_id"],
            capsule_path=capsule_path,
        )
        assert replay["retained_helpful_asset_ids"] == [asset.asset_id]
        assert replay["task_success_inferred"] is False
        assert vault.verify_integrity()["valid"] is True

        vault.connection.execute(
            "UPDATE feedback_records SET receipt_sha256 = ? WHERE feedback_id = ?",
            ("0" * 64, feedback["feedback_id"]),
        )
        vault.connection.commit()
        tampered = vault.get_feedback(feedback["feedback_id"])
        assert tampered["record_valid"] is False
        assert tampered["vault_integrity_valid"] is False
        assert tampered["valid"] is False
        assert vault.verify_integrity()["valid"] is False


def test_run_receipt_store_rejects_a_fabricated_capsule_identity(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        timestamp = utc_now()
        payload = {
            "schema_version": "deeplaw.knowledge-run-receipt/v1",
            "vault_id": vault.vault_id,
            "vault_revision": vault.revision,
            "audit_head": vault.audit_head,
            "capsule_id": f"capsule_{'0' * 24}",
            "capsule_digest": "0" * 64,
            "task_sha256": "1" * 64,
            "goal_sha256": None,
            "selected_asset_ids": [],
            "source_ids": [],
            "host": {"name": "test", "version": "test"},
            "model": None,
            "started_at": timestamp,
            "finished_at": timestamp,
            "status": "success",
            "outcome_artifact_sha256": None,
            "metrics": {
                "input_tokens": None,
                "output_tokens": None,
                "latency_ms": None,
                "cost": None,
                "currency": None,
            },
        }
        with pytest.raises(ValueError, match="verified knowledge Capsule"):
            vault.record_run_receipt(payload)


def test_permission_report_is_truthful_about_platform_guarantees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    report = knowledge_vault_permission_report(root)
    assert report["structural_valid"] is True
    if os.name == "nt":
        assert report["status"] == "verified"
        assert report["permissions_verified"] is True
        assert report["security_model"] == "windows_native_acl_owner_only"
        assert report["native_windows_acl"]["permissions_verified"] is True
    else:
        assert report["status"] == "verified"
        assert report["permissions_verified"] is True
        manifest = root / "vault.json"
        manifest.chmod(0o644)
        failed = knowledge_vault_permission_report(root)
        assert failed["status"] == "failed"
        assert failed["permissions_verified"] is False

        monkeypatch.setattr(
            "deeplaw.knowledge_store._MAX_PERMISSION_REPORT_SOURCE_DETAILS",
            1,
        )
        sources = root / "sources"
        first = sources / "first"
        second = sources / "second"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        first.chmod(0o600)
        second.chmod(0o644)
        manifest.chmod(0o600)
        truncated = knowledge_vault_permission_report(root)
        assert truncated["stored_source_files_checked"] == 2
        assert truncated["stored_source_files_returned"] == 1
        assert truncated["stored_source_files_truncated"] is True
        assert truncated["status"] == "failed"
        assert truncated["permissions_verified"] is False


def test_legacy_vault_can_plan_and_apply_the_additive_control_migration(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "legacy-source.md"
    source.write_text(
        "# Legacy\nA legacy source remains replayable after control migration.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    database = root / "vault.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM metadata WHERE key = 'control_schema'")
        connection.execute("DROP TABLE feedback_records")
        connection.execute("DROP TABLE run_receipts")
        connection.execute("DROP TABLE review_receipts")
        connection.execute("DROP TABLE source_lifecycle")
        connection.commit()
    finally:
        connection.close()

    with KnowledgeVault(root, read_only=True) as vault:
        plan = vault.migrate_knowledge_control(apply=False)
        assert plan["required"] is True
    backup = tmp_path / "legacy-vault-backup"
    with KnowledgeVault(root, read_only=False) as vault:
        applied = vault.migrate_knowledge_control(apply=True, backup_path=backup)
        assert applied["applied"] is True
        assert applied["backup"]["valid"] is True
        assert applied["verification"]["valid"] is True
        removal = vault.remove_source(
            compiled["source"]["source_id"],
            reason="Legacy lifecycle replay regression test.",
            confirm=True,
        )
        assert removal["removed_asset_count"] == 1
        assert vault.verify_integrity()["valid"] is True

    backup_verification = verify_knowledge_migration_backup(
        backup,
        expected_vault_id=applied["vault_id"],
    )
    assert backup_verification["valid"] is True
    rollback = restore_knowledge_migration_backup(
        root,
        backup=backup,
        confirm=True,
    )
    assert rollback["restored"] is True
    assert Path(rollback["retained_previous_vault"]).is_dir()
    with KnowledgeVault(root, read_only=True) as vault:
        restored_plan = vault.migrate_knowledge_control(apply=False)
        assert restored_plan["required"] is True
        assert vault.source_info(compiled["source"]["source_id"])
        assert vault.verify_integrity()["valid"] is True


def test_migration_rollback_rejects_a_tampered_backup(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    backup = tmp_path / "tampered-backup"
    with KnowledgeVault(root, read_only=False) as vault:
        result = vault.migrate_knowledge_control(apply=False)
        assert result["required"] is False
    created = create_knowledge_migration_backup(root, output=backup)
    assert created["valid"] is True
    (backup / "vault.sqlite3").write_bytes(b"tampered")
    assert verify_knowledge_migration_backup(backup)["valid"] is False
    with pytest.raises(RuntimeError, match="valid matching backup"):
        restore_knowledge_migration_backup(root, backup=backup, confirm=True)


def test_interrupted_migration_rolls_back_and_retains_a_verified_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    database = root / "vault.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM metadata WHERE key = 'control_schema'")
        connection.execute("DROP TABLE feedback_records")
        connection.execute("DROP TABLE run_receipts")
        connection.execute("DROP TABLE review_receipts")
        connection.execute("DROP TABLE source_lifecycle")
        connection.commit()
    finally:
        connection.close()
    backup = tmp_path / "interrupted-backup"
    install = knowledge_store._install_control_tables

    def interrupted_install(connection: sqlite3.Connection) -> None:
        install(connection)
        raise RuntimeError("synthetic migration interruption")

    monkeypatch.setattr(knowledge_store, "_install_control_tables", interrupted_install)
    with (
        KnowledgeVault(root, read_only=False) as vault,
        pytest.raises(RuntimeError, match="synthetic migration interruption"),
    ):
        vault.migrate_knowledge_control(apply=True, backup_path=backup)
    assert verify_knowledge_migration_backup(backup)["valid"] is True
    with KnowledgeVault(root, read_only=True) as vault:
        plan = vault.migrate_knowledge_control(apply=False)
        assert plan["required"] is True
        assert vault.verify_integrity()["valid"] is True

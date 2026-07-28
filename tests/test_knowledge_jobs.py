from __future__ import annotations

import json
import os
from pathlib import Path

import deeplaw.knowledge_jobs as knowledge_jobs
from deeplaw.knowledge_jobs import (
    cancel_ingest_job,
    create_ingest_job,
    list_ingest_jobs,
    load_ingest_job,
    plan_registered_sync,
    run_ingest_job,
    run_registered_sync,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="jobs", scope="project")
    return root


def test_ingest_job_checkpoints_retries_and_registers_sources(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "guide.md"
    original = "# Recovery\nThe local recovery procedure preserves source evidence.\n"
    source.write_text(original, encoding="utf-8")

    with KnowledgeVault(root, read_only=False) as vault:
        planned = create_ingest_job(vault, source)
        source.write_text(original + "changed after planning\n", encoding="utf-8")
        failed = run_ingest_job(vault, planned["job_id"])
        assert failed["state"] == "interrupted"
        assert failed["summary"]["failed"] == 1

        source.write_text(original, encoding="utf-8")
        completed = run_ingest_job(vault, planned["job_id"], retry_failed=True)
        assert completed["state"] == "completed"
        assert completed["items"][0]["attempts"] == 2
        assert completed["items"][0]["source_id"].startswith("source_")
        assert list_ingest_jobs(vault)["jobs"][0]["job_id"] == planned["job_id"]
        assert plan_registered_sync(vault)["change_count"] == 0
        assert vault.verify_integrity()["valid"] is True


def test_registered_sync_preserves_source_identity_across_rename(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    documents = tmp_path / "documents"
    documents.mkdir()
    old_path = documents / "old-name.md"
    old_path.write_text(
        "# Stable procedure\nAlways verify the immutable artifact before release.\n",
        encoding="utf-8",
    )

    with KnowledgeVault(root, read_only=False) as vault:
        job = create_ingest_job(vault, documents, recursive=True)
        completed = run_ingest_job(vault, job["job_id"])
        old_source_id = completed["items"][0]["source_id"]
        old_source = vault.source_info(old_source_id)
        manifest = vault.source_review_manifest(old_source_id)
        vault.approve_source_assets(
            old_source_id,
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )

        new_path = documents / "new-name.md"
        old_path.rename(new_path)
        plan = plan_registered_sync(vault)
        assert plan["change_count"] == 1
        assert plan["changes"][0]["action"] == "move"
        assert plan["changes"][0]["source_key"] == old_source["canonical_source_key"]

        synced = run_registered_sync(vault)
        assert synced["state"] == "completed"
        new_source = next(
            source
            for source in vault.source_versions(old_source["source_key"])
            if source["logical_path"] == "new-name.md"
        )
        new_source_id = new_source["source_id"]
        assert new_source_id != old_source_id
        assert new_source["source_revision_id"] == old_source["source_revision_id"]
        assert new_source["logical_path"] == "new-name.md"
        lineage_rows = vault.connection.execute(
            "SELECT status FROM knowledge_lineage_v2 ORDER BY created_at"
        ).fetchall()
        assert any(row["status"] == "renamed" for row in lineage_rows)
        assert vault.verify_integrity()["valid"] is True


def test_ingest_job_can_be_cancelled_before_it_starts(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "cancel.md"
    source.write_text(
        "# Cancel\nThis planned source must not be compiled after cancellation.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        planned = create_ingest_job(vault, source, register_for_sync=False)
        cancelled = cancel_ingest_job(vault, planned["job_id"])
        assert cancelled["state"] == "cancelled"
        assert cancelled["summary"]["cancelled"] == 1
        assert vault.all_sources() == ()


def test_read_only_job_listing_does_not_create_operator_directories(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    operations = root / "operations"
    assert not operations.exists()

    with KnowledgeVault(root, read_only=True) as vault:
        assert list_ingest_jobs(vault)["jobs"] == []

    assert not operations.exists()


def test_v1_job_is_loaded_and_persisted_as_v2_without_losing_state(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "legacy-job.md"
    source.write_text("# Legacy job\nPreserve resumable work.\n", encoding="utf-8")

    with KnowledgeVault(root, read_only=False) as vault:
        created = create_ingest_job(vault, source, register_for_sync=False)
        path = root / "operations" / "jobs" / f"{created['job_id']}.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        legacy["schema_version"] = knowledge_jobs.INGEST_JOB_SCHEMA_V1
        for item in legacy["items"]:
            item.pop("collection_id")
            item.pop("origin_uri")
            item.pop("snapshot_id")
        legacy["record_sha256"] = knowledge_jobs._job_digest(legacy)
        path.write_text(
            json.dumps(legacy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            os.chmod(path, 0o600)

        normalized = load_ingest_job(vault, created["job_id"])
        assert normalized["schema_version"] == knowledge_jobs.INGEST_JOB_SCHEMA
        assert normalized["items"][0]["origin_uri"] is None
        assert normalized["items"][0]["snapshot_id"] is None
        assert normalized["items"][0]["collection_id"].startswith("collection_")

        completed = run_ingest_job(vault, created["job_id"])
        assert completed["state"] == "completed"
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["schema_version"] == knowledge_jobs.INGEST_JOB_SCHEMA
        assert vault.verify_integrity()["valid"] is True

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_knowledge_cli_lifecycle_compiles_a_verified_capsule(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialized = _run_cli(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "cli-project",
        "--scope",
        "project",
    )
    assert initialized.returncode == 0, initialized.stderr

    proposed = _run_cli(
        "knowledge",
        "propose",
        "--vault",
        str(vault),
        "--kind",
        "constraint",
        "--memory-tier",
        "project",
        "--title",
        "Stable storage boundary",
        "--statement",
        "Preserve the accepted storage contract during migration.",
        "--semantic-key",
        "storage.boundary",
        "--sensitivity",
        "internal",
        "--confirm-no-case-data",
    )
    assert proposed.returncode == 0, proposed.stderr
    asset_id = json.loads(proposed.stdout)["asset_id"]

    approved = _run_cli(
        "knowledge",
        "approve",
        "--vault",
        str(vault),
        "--asset-id",
        asset_id,
        "--confirm-reviewed",
    )
    assert approved.returncode == 0, approved.stderr
    assert json.loads(approved.stdout)["status"] == "active"

    capsule_path = tmp_path / "capsule.json"
    compiled = _run_cli(
        "knowledge",
        "context",
        "--vault",
        str(vault),
        "--task",
        "migrate storage while preserving the accepted contract",
        "--confirm-no-case-data",
        "--output",
        str(capsule_path),
    )
    assert compiled.returncode == 0, compiled.stderr
    capsule = json.loads(compiled.stdout)
    assert capsule["constraints"][0]["asset_id"] == asset_id
    assert capsule_path.is_file()

    verified = _run_cli(
        "knowledge",
        "verify-capsule",
        "--capsule",
        str(capsule_path),
        "--vault",
        str(vault),
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["valid"] is True


def test_knowledge_cli_supports_stable_jsonl_and_human_output(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialized = _run_cli(
        "knowledge",
        "--format",
        "jsonl",
        "init",
        "--vault",
        str(vault),
        "--name",
        "output-contract",
    )
    assert initialized.returncode == 0, initialized.stderr
    assert len(initialized.stdout.rstrip("\n").splitlines()) == 1
    assert json.loads(initialized.stdout)["vault_id"].startswith("vault_")

    human = _run_cli(
        "knowledge",
        "--format",
        "human",
        "doctor",
        "--vault",
        str(vault),
        "--permissions",
    )
    assert human.returncode == 0, human.stderr
    assert "permissions_verified:" in human.stdout
    assert "schema_version:" in human.stdout


def test_knowledge_cli_rejects_a_manual_proposal_without_case_boundary(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    assert (
        _run_cli(
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "boundary",
        ).returncode
        == 0
    )
    rejected = _run_cli(
        "knowledge",
        "propose",
        "--vault",
        str(vault),
        "--kind",
        "fact",
        "--memory-tier",
        "project",
        "--title",
        "Unconfirmed material",
        "--statement",
        "This statement has no explicit case boundary confirmation.",
    )

    assert rejected.returncode == 2
    assert "--confirm-no-case-data" in rejected.stderr


def test_knowledge_cli_refuses_to_overwrite_a_capsule_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialized = _run_cli(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "capsule-output",
    )
    assert initialized.returncode == 0, initialized.stderr
    capsule_path = tmp_path / "existing.json"
    capsule_path.write_text("user content", encoding="utf-8")

    result = _run_cli(
        "knowledge",
        "context",
        "--vault",
        str(vault),
        "--task",
        "compile a bounded context",
        "--confirm-no-case-data",
        "--output",
        str(capsule_path),
    )

    assert result.returncode == 2
    assert "already exists" in result.stderr
    assert capsule_path.read_text(encoding="utf-8") == "user content"


def test_knowledge_cli_does_not_offer_verified_source_as_user_input(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    assert (
        _run_cli(
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "trust-boundary",
        ).returncode
        == 0
    )
    result = _run_cli(
        "knowledge",
        "propose",
        "--vault",
        str(vault),
        "--kind",
        "fact",
        "--memory-tier",
        "domain",
        "--title",
        "False authority",
        "--statement",
        "A user cannot self-assert publisher verification.",
        "--trust",
        "verified_source",
        "--confirm-no-case-data",
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_knowledge_cli_approves_one_exact_compiled_source_atomically(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "scale-guide.md"
    source.write_text(
        "# Alpha\n"
        "The alpha batch fact uses token alphaquartz.\n"
        "# Beta\n"
        "The beta batch fact uses token betajade.\n",
        encoding="utf-8",
    )
    assert (
        _run_cli(
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "batch-review",
        ).returncode
        == 0
    )
    ingested = _run_cli(
        "knowledge",
        "ingest",
        "--vault",
        str(vault),
        "--source",
        str(source),
        "--confirm-no-case-data",
    )
    assert ingested.returncode == 0, ingested.stderr
    source_id = json.loads(ingested.stdout)["source"]["source_id"]

    manifest = _run_cli(
        "knowledge",
        "review",
        "manifest",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    assert manifest.returncode == 0, manifest.stderr
    manifest_sha256 = json.loads(manifest.stdout)["review_manifest_sha256"]

    approved = _run_cli(
        "knowledge",
        "approve-source",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
        "--review-manifest-sha256",
        manifest_sha256,
        "--confirm-reviewed",
    )
    assert approved.returncode == 0, approved.stderr
    assert json.loads(approved.stdout)["approved_asset_count"] == 2

    searched = _run_cli(
        "knowledge",
        "search",
        "--vault",
        str(vault),
        "--query",
        "betajade",
    )
    assert searched.returncode == 0, searched.stderr
    assert json.loads(searched.stdout)["results"][0]["title"] == "Beta"


def test_knowledge_cli_bounds_large_ingest_receipts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "large-guide.md"
    source.write_text(
        "".join(
            f"# Record {index:03d}\nReviewed record {index:03d} keeps token glyph{index:03d}.\n"
            for index in range(105)
        ),
        encoding="utf-8",
    )
    assert (
        _run_cli(
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "bounded-receipt",
        ).returncode
        == 0
    )

    ingested = _run_cli(
        "knowledge",
        "ingest",
        "--vault",
        str(vault),
        "--source",
        str(source),
        "--confirm-no-case-data",
    )

    assert ingested.returncode == 0, ingested.stderr
    receipt = json.loads(ingested.stdout)
    assert receipt["asset_count"] == 105
    assert len(receipt["asset_ids"]) == 100
    assert receipt["asset_ids_truncated"] is True


def test_knowledge_cli_source_review_run_and_feedback_control_plane(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "decision.md"
    source.write_text(
        "# Decision\nThe Orion release uses the signed blue artifact.\n",
        encoding="utf-8",
    )
    initialized = _run_cli(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "control-plane",
        "--scope",
        "project",
    )
    assert initialized.returncode == 0, initialized.stderr
    permissions = _run_cli(
        "knowledge",
        "doctor",
        "--vault",
        str(vault),
        "--permissions",
    )
    assert permissions.returncode == 0, permissions.stderr
    permission_report = json.loads(permissions.stdout)
    assert permission_report["structural_valid"] is True
    assert "security_model" in permission_report
    ingested = _run_cli(
        "knowledge",
        "source",
        "add",
        "--vault",
        str(vault),
        "--source",
        str(source),
        "--typed-extraction",
        "deterministic-v1",
        "--confirm-no-case-data",
    )
    assert ingested.returncode == 0, ingested.stderr
    source_id = json.loads(ingested.stdout)["source"]["source_id"]
    queue = _run_cli(
        "knowledge",
        "review",
        "queue",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    assert queue.returncode == 0, queue.stderr
    assert json.loads(queue.stdout)["total"] == 1
    manifest_result = _run_cli(
        "knowledge",
        "review",
        "manifest",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    assert manifest_result.returncode == 0, manifest_result.stderr
    manifest_sha256 = json.loads(manifest_result.stdout)["review_manifest_sha256"]
    approved = _run_cli(
        "knowledge",
        "review",
        "approve-source",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
        "--review-manifest-sha256",
        manifest_sha256,
        "--reviewer-id",
        "maintainer@example.test",
        "--reason",
        "Reviewed the exact source manifest.",
        "--confirm-reviewed",
    )
    assert approved.returncode == 0, approved.stderr
    approved_payload = json.loads(approved.stdout)
    asset_id = approved_payload["approved_asset_ids"][0]
    assert approved_payload["review_receipt"]["review_receipt_id"].startswith("review_")

    capsule_path = tmp_path / "capsule.json"
    context = _run_cli(
        "knowledge",
        "context",
        "--vault",
        str(vault),
        "--task",
        "Prepare the signed Orion release artifact.",
        "--confirm-no-case-data",
        "--output",
        str(capsule_path),
    )
    assert context.returncode == 0, context.stderr
    run = _run_cli(
        "knowledge",
        "run-receipt",
        "create",
        "--vault",
        str(vault),
        "--capsule",
        str(capsule_path),
        "--status",
        "partial",
        "--host-name",
        "codex",
        "--host-version",
        "test",
    )
    assert run.returncode == 0, run.stderr
    run_id = json.loads(run.stdout)["run_id"]
    feedback = _run_cli(
        "knowledge",
        "feedback",
        "record",
        "--vault",
        str(vault),
        "--run-id",
        run_id,
        "--outcome",
        "partial",
        "--helpful-asset-id",
        asset_id,
        "--missing-knowledge",
        "The rollback owner is missing.",
        "--observation",
        "The signed artifact decision was useful.",
        "--recommended-action",
        "Review a source-bound rollback owner decision.",
        "--confirm-no-case-data",
    )
    assert feedback.returncode == 0, feedback.stderr
    feedback_id = json.loads(feedback.stdout)["feedback_id"]
    replay = _run_cli(
        "knowledge",
        "feedback",
        "replay",
        "--vault",
        str(vault),
        "--feedback-id",
        feedback_id,
        "--capsule",
        str(capsule_path),
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["task_success_inferred"] is False


def test_knowledge_cli_migration_backup_verify_and_rollback(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    backup = tmp_path / "migration-backup"
    initialized = _run_cli(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "migration-cli",
    )
    assert initialized.returncode == 0, initialized.stderr
    connection = sqlite3.connect(vault / "vault.sqlite3")
    try:
        connection.execute("DELETE FROM metadata WHERE key = 'control_schema'")
        connection.execute("DROP TABLE feedback_records")
        connection.execute("DROP TABLE run_receipts")
        connection.execute("DROP TABLE review_receipts")
        connection.execute("DROP TABLE source_lifecycle")
        connection.commit()
    finally:
        connection.close()

    planned = _run_cli("knowledge", "migrate", "--vault", str(vault))
    assert planned.returncode == 0, planned.stderr
    assert json.loads(planned.stdout)["required"] is True
    applied = _run_cli(
        "knowledge",
        "migrate",
        "--vault",
        str(vault),
        "--apply",
        "--backup",
        str(backup),
    )
    assert applied.returncode == 0, applied.stderr
    applied_payload = json.loads(applied.stdout)
    assert applied_payload["backup"]["valid"] is True
    assert applied_payload["verification"]["valid"] is True
    verified = _run_cli(
        "knowledge",
        "migrate",
        "--vault",
        str(vault),
        "--verify",
        "--backup",
        str(backup),
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["valid"] is True
    rolled_back = _run_cli(
        "knowledge",
        "migrate",
        "--vault",
        str(vault),
        "--rollback",
        "--backup",
        str(backup),
        "--confirm-rollback",
    )
    assert rolled_back.returncode == 0, rolled_back.stderr
    assert json.loads(rolled_back.stdout)["restored"] is True
    restored_plan = _run_cli("knowledge", "migrate", "--vault", str(vault))
    assert restored_plan.returncode == 0, restored_plan.stderr
    assert json.loads(restored_plan.stdout)["required"] is True

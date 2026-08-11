from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

REPOSITORY = Path(__file__).resolve().parents[1]
CODEX_EVIDENCE = (
    REPOSITORY
    / "benchmarks/hosts/evidence/codex-continuity-qualification-2026-08-11"
)
OBSIDIAN_EVIDENCE = (
    REPOSITORY
    / "benchmarks/hosts/evidence/obsidian-desktop-qualification-2026-08-11"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_sanitized(path: Path) -> None:
    rendered = path.read_text(encoding="utf-8")
    assert "/Users/" not in rendered
    assert not re.search(r"[A-Za-z]:\\\\Users\\\\", rendered)
    assert "auth.json" not in rendered
    assert not re.search(r"sk-[A-Za-z0-9_-]{20,}", rendered)


def test_real_codex_receipt_is_bound_sanitized_and_fails_release_closed() -> None:
    report_path = CODEX_EVIDENCE / "codex-continuity-qualification-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (
            REPOSITORY
            / "contracts/codex-continuity-qualification-report.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(report)

    assert report["binding"] == {
        "candidate_wheel_name": "deeplaw-0.12.0-py3-none-any.whl",
        "candidate_wheel_sha256": (
            "9c35dc1f575499c221dc30274105418483124101214497845e9d933c79032677"
        ),
        "commit": "b14c90e4cf3b2d4954461306dc8cc77f434891d9",
        "package_version": "0.12.0",
        "tree": "9fa784c671f71c63a7c33730338b27858c3257cf",
        "worktree_clean": True,
    }
    assert report["host"]["model"] == "gpt-5.6-luna"
    assert report["aggregate"]["passed_runs"] == 3
    assert report["aggregate"]["failed_runs"] == 0
    assert report["aggregate"]["wrong_state_admission"] == 0
    assert report["aggregate"]["actual_total_tokens"] == 118_183
    assert report["aggregate"]["release_gate_passed"] is False
    assert report["claim_eligible"] is False
    assert report["release_ready"] is False
    assert all(run["status"] == "passed" for run in report["runs"])
    assert all(run["exit_status"] == 0 for run in report["runs"])
    assert all(run["ledger_unchanged"] is True for run in report["runs"])
    assert all(run["wrong_state_admission"] == 0 for run in report["runs"])

    manifest = json.loads((CODEX_EVIDENCE / "SHA256SUMS.json").read_text())
    for artifact in manifest["artifacts"]:
        artifact_path = CODEX_EVIDENCE / artifact["name"]
        assert artifact_path.is_file()
        assert artifact_path.stat().st_size == artifact["bytes"]
        assert _sha256(artifact_path) == artifact["sha256"]
    for run in report["runs"]:
        event_receipt = run["actual_event_receipt"]
        event_path = CODEX_EVIDENCE / event_receipt["sanitized_events_name"]
        assert _sha256(event_path) == event_receipt["sanitized_events_sha256"]

    for path in CODEX_EVIDENCE.glob("*.json*"):
        _assert_sanitized(path)


def test_real_obsidian_receipt_preserves_identity_without_release_claim() -> None:
    report_path = OBSIDIAN_EVIDENCE / "obsidian-desktop-qualification.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checksum, relative_name = (
        (OBSIDIAN_EVIDENCE / "SHA256SUMS")
        .read_text(encoding="utf-8")
        .strip()
        .split("  ", maxsplit=1)
    )

    assert relative_name == report_path.name
    assert _sha256(report_path) == checksum
    assert report["status"] == "executed"
    assert report["claim_eligible"] is False
    assert report["release_ready"] is False
    assert report["candidate"]["package_version"] == "0.12.0"
    assert report["environment"]["obsidian_ui_version"] == "1.13.4"
    assert report["plugin"]["desktop_loaded"] is True
    assert report["plugin"]["verify_command_valid"] is True
    assert report["outcome"]["rename_passed"] is True
    assert report["outcome"]["edit_passed"] is True
    assert report["outcome"]["reconcile_passed"] is True
    assert report["outcome"]["stable_identity_preserved"] is True
    assert report["outcome"]["renamed_path_preserved"] is True
    assert report["outcome"]["parent_revision_bound"] is True
    assert report["outcome"]["legal_authority"] is False
    assert report["outcome"]["conflict_count"] == 0
    assert report["outcome"]["post_reconcile_verification_valid"] is True
    assert report["synthetic_fixture"]["contains_case_or_customer_data"] is False
    assert report["synthetic_fixture"]["vault_path_in_report"] is False
    assert report["synthetic_fixture"]["credential_or_secret_in_report"] is False
    assert len(report["captures"]) == 4
    _assert_sanitized(report_path)

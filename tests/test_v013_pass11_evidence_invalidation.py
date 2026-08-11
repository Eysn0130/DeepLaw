from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.hosts.run_codex_continuity_qualification import (
    _environment_receipt,
)
from benchmarks.v013.query_graph_scale import verify_report

REPOSITORY = Path(__file__).resolve().parents[1]
DISPOSITION = REPOSITORY / "benchmarks/v013/pass10-evidence-invalidation-v1.json"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pass10_statement_report_reproduces_gold_binding_failure_when_present() -> None:
    disposition = _load(DISPOSITION)
    statement = disposition["statement"]
    assert isinstance(statement, dict)
    assert statement["status"] == "historical_candidate_evidence"
    assert statement["verification_errors"] == ["Gold byte binding mismatch"]

    report_path = REPOSITORY / str(statement["artifact_relative_path"])
    if report_path.is_file():
        assert _sha256(report_path) == statement["artifact_sha256"]
        assert verify_report(_load(report_path)) == {
            "valid": False,
            "errors": ["Gold byte binding mismatch"],
        }


def test_pass10_environment_receipt_is_rejected_by_current_argv_contract() -> None:
    disposition = _load(DISPOSITION)
    codex = disposition["codex"]
    assert isinstance(codex, dict)
    receipt_path = REPOSITORY / str(codex["environment_receipt_relative_path"])

    assert _sha256(receipt_path) == codex["environment_receipt_sha256"]
    assert _load(receipt_path)["child_argv"] == codex["observed_child_argv"]
    assert _environment_receipt(receipt_path) is None


def test_pass10_candidate_prompt_contamination_remains_bound_as_history() -> None:
    disposition = _load(DISPOSITION)
    codex = disposition["codex"]
    assert isinstance(codex, dict)
    fixture_path = REPOSITORY / str(codex["fixture_relative_path"])
    assert _sha256(fixture_path) == codex["fixture_sha256"]
    assert codex["prompt_contamination"] == {
        "expected_first_action_scoring_instruction": True,
        "expected_first_action_literal": False,
        "confirmed_decision_scoring_instruction": True,
        "confirmed_decision_literal": False,
        "expected_marker_value": True,
        "exact_knowledge_id": True,
    }


def test_pass10_evidence_remains_release_closed() -> None:
    disposition = _load(DISPOSITION)
    assert disposition["qualification"] == {
        "claim_eligible": False,
        "release_ready": False,
        "version_change_allowed": False,
        "real_model_runs_allowed_before_harness_freeze": False,
    }
    statuses = {
        disposition[name]["status"]
        for name in ("statement", "codex", "obsidian")
        if isinstance(disposition[name], dict)
    }
    assert statuses == {"historical_candidate_evidence"}

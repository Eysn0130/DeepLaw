from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from benchmarks.scale.run_retrieval_fabric_scale import run_diagnostic


def _validate_report(report: dict[str, object]) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (
            repository_root
            / "contracts"
            / "retrieval-fabric-scale-diagnostic.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)


def test_retrieval_fabric_scale_runner_uses_real_identity_retrieval_and_capsules(
    tmp_path: Path,
) -> None:
    report = run_diagnostic(
        tmp_path / "workspace",
        asset_count=100,
        query_count=3,
    )

    _validate_report(report)

    assert report["claim_eligible"] is False
    assert report["corpus"]["asset_count"] == 100
    assert report["corpus"]["stored_asset_count_before_lifecycle_probe"] == 100
    assert report["corpus"]["stored_asset_count_after_lifecycle_probe"] > 100
    assert report["build"]["integrity_valid"] is True
    assert report["build"]["cold_cli_exit_code"] == 0
    assert report["build"]["cold_cli_capsule_valid"] is True
    assert report["build"]["cold_cli_process_ms"] > 0
    assert report["measurements"]["lexical_hit_at_1"] == 1.0
    assert report["measurements"]["hybrid_hit_at_1"] == 1.0
    assert report["measurements"]["capsule_recall"] == 1.0
    assert report["measurements"]["capsule_verification_rate"] == 1.0
    assert report["measurements"]["provenance_coverage"] == 1.0
    assert report["measurements"]["no_answer_empty"] is True
    assert report["lifecycle"]["source_key_stable"] is True
    assert report["lifecycle"]["source_revision_changed"] is True
    assert report["lifecycle"]["update_correct"] is True
    assert report["lifecycle"]["forgetting_correct"] is True
    assert report["lifecycle"]["history_retained"] is True
    assert report["thresholds"]["quality_and_integrity_passed"] is True
    assert report["thresholds"]["formal_100k_run"] is False
    assert report["thresholds"]["million_asset_diagnostic_run"] is False


def _assert_historical_dirty_candidate_is_not_a_current_claim(
    report: dict[str, object],
    *,
    repository_root: Path,
) -> None:
    """Keep old dirty-worktree diagnostics honest after implementation changes.

    The checked-in reports deliberately recorded an uncommitted candidate.  Its
    file hashes can prove which bytes were measured, but those bytes are not a
    reconstructable Git revision and must never be relabelled as evidence for a
    later implementation.  Current behavior is exercised by the real runner
    test above.
    """

    candidate = report["candidate"]
    assert isinstance(candidate, dict)
    implementation_files = candidate["implementation_files"]
    assert isinstance(implementation_files, dict)
    assert implementation_files
    current_hashes = {
        relative_path: hashlib.sha256(
            (repository_root / relative_path).read_bytes()
        ).hexdigest()
        for relative_path in implementation_files
    }
    assert any(
        current_hashes[path] != expected
        for path, expected in implementation_files.items()
    )
    assert report["claim_eligible"] is False


def test_checked_in_100k_diagnostic_remains_historical_and_claim_ineligible() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    report_path = (
        repository_root
        / "benchmarks"
        / "scale"
        / "retrieval-fabric-100k-2026-07-27.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    _validate_report(report)

    assert report["schema_version"] == "deeplaw.retrieval-fabric-scale-diagnostic/v1"
    assert report["claim_eligible"] is False
    assert report["candidate"]["tracked_or_untracked_worktree_dirty"] is True
    assert report["corpus"]["asset_count"] == 100_000
    assert report["corpus"]["stored_asset_count_before_lifecycle_probe"] == 100_000
    assert report["corpus"]["stored_asset_count_after_lifecycle_probe"] > 100_000
    assert report["build"]["integrity_valid"] is True
    assert report["build"]["cold_cli_exit_code"] == 0
    assert report["build"]["cold_cli_capsule_valid"] is True
    assert report["thresholds"]["formal_100k_run"] is True
    assert report["thresholds"]["million_asset_diagnostic_run"] is False
    assert report["thresholds"]["quality_and_integrity_passed"] is True
    assert report["thresholds"]["warm_lexical_p95_lt_50_ms"] is True
    assert report["thresholds"]["warm_hybrid_p95_lt_500_ms"] is True
    assert report["thresholds"]["recall_and_context_p95_lt_750_ms"] is True
    assert report["lifecycle"]["source_key_stable"] is True
    assert report["lifecycle"]["source_revision_changed"] is True
    assert report["lifecycle"]["update_correct"] is True
    assert report["lifecycle"]["forgetting_correct"] is True
    assert report["lifecycle"]["history_retained"] is True

    _assert_historical_dirty_candidate_is_not_a_current_claim(
        report,
        repository_root=repository_root,
    )


def test_checked_in_one_million_diagnostic_remains_historical_and_claim_ineligible() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    report_path = (
        repository_root
        / "benchmarks"
        / "scale"
        / "retrieval-fabric-1m-2026-07-28.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    _validate_report(report)

    assert report["claim_eligible"] is False
    assert report["candidate"]["tracked_or_untracked_worktree_dirty"] is True
    assert report["corpus"]["asset_count"] == 1_000_000
    assert report["corpus"]["source_count"] == 10
    assert report["corpus"]["stored_asset_count_before_lifecycle_probe"] == 1_000_000
    assert report["corpus"]["stored_asset_count_after_lifecycle_probe"] > 1_000_000
    assert report["build"]["integrity_valid"] is True
    assert report["build"]["cold_cli_exit_code"] == 0
    assert report["build"]["cold_cli_capsule_valid"] is True
    assert report["thresholds"]["formal_100k_run"] is False
    assert report["thresholds"]["million_asset_diagnostic_run"] is True
    assert report["thresholds"]["quality_and_integrity_passed"] is True
    assert report["thresholds"]["warm_lexical_p95_lt_50_ms"] is True
    assert report["thresholds"]["warm_hybrid_p95_lt_500_ms"] is True
    assert report["thresholds"]["recall_and_context_p95_lt_750_ms"] is True
    assert report["lifecycle"]["source_key_stable"] is True
    assert report["lifecycle"]["source_revision_changed"] is True
    assert report["lifecycle"]["update_correct"] is True
    assert report["lifecycle"]["forgetting_correct"] is True
    assert report["lifecycle"]["history_retained"] is True

    _assert_historical_dirty_candidate_is_not_a_current_claim(
        report,
        repository_root=repository_root,
    )

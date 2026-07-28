from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


def _assert_snapshot(repository: Path, summary_name: str) -> None:
    summary_path = repository / "benchmarks" / summary_name
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    cases_path = repository / summary["cases_path"]
    cases_bytes = cases_path.read_bytes()
    cases = [
        json.loads(line)
        for line in cases_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    buckets = Counter(case.get("expected_bucket", "evidence") for case in cases)

    assert hashlib.sha256(cases_bytes).hexdigest() == summary["cases_sha256"]
    assert len(cases) == summary["case_count"]
    assert dict(buckets) == summary["expected_bucket_counts"]


def test_historical_legal_benchmark_fixtures_are_immutable() -> None:
    repository = Path(__file__).resolve().parents[1]

    _assert_snapshot(repository, "core-v0.4.0-candidate-2026-07-26.json")
    _assert_snapshot(repository, "core-v0.4.0-candidate-2026-07-25.json")
    _assert_snapshot(repository, "core-v5-candidate-2026-07-15.json")


def test_historical_v060_knowledge_control_candidate_keeps_its_identity() -> None:
    repository = Path(__file__).resolve().parents[1]
    snapshot = json.loads(
        (
            repository
            / "benchmarks"
            / "knowledge-os-control-plane-candidate-2026-07-27.json"
        ).read_text(encoding="utf-8")
    )
    implementation = snapshot["implementation"]
    assert snapshot["schema_version"] == "deeplaw.knowledge-os-candidate/v1"
    assert snapshot["status"] == "release_candidate_internal_not_held_out"
    assert snapshot["package_version"] == "0.6.0"
    assert implementation["candidate_code_commit"] == (
        "aea0c319bdd72c2c5e3ed2a26bd71a97f6fa686f"
    )
    assert implementation["pyproject_sha256"] == (
        "f1a7dba03940e095748b1e3020acbc87cccc38a36bd0cff7e45946bd96ec7066"
    )
    assert implementation["uv_lock_sha256"] == (
        "d1f3989517706a2d23daa3f755ce27423e011db1735599757aec1a9171c93df8"
    )
    assert implementation["python_source_tree_sha256"] == (
        "4302ba55322553d64680ee5a612f1b828be93fa7106e4f37a8601f57a1710a11"
    )
    assert implementation["tracked_worktree_dirty_at_manifest_generation"] is False
    assert implementation["local_build_artifacts"]["fresh_wheel_lifecycle_verified"] is True

    for evidence in snapshot["evidence"].values():
        path = repository / evidence["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence["sha256"]
        report = json.loads(path.read_text(encoding="utf-8"))
        if "claim_eligible" in evidence:
            assert report["claim_eligible"] is evidence["claim_eligible"]

    diagnostic = json.loads(
        (
            repository
            / snapshot["evidence"]["knowledge_control_diagnostic"]["path"]
        ).read_text(encoding="utf-8")
    )
    assert diagnostic["implementation"]["python_source_tree_sha256"] == implementation[
        "python_source_tree_sha256"
    ]
    assert diagnostic["quality"]["exact_hit1"] == 1.0
    assert diagnostic["quality"]["source_ref_coverage"] == 1.0
    assert diagnostic["quality"]["same_title_cross_source_preserved"] is True
    assert diagnostic["quality"]["source_update"]["atomic_switch_passed"] is True
    assert diagnostic["quality"]["final_integrity"] is True
    external_proof = snapshot["external_proof"]
    assert external_proof["status"] == "pending_external_execution"
    assert external_proof["current_candidate_frozen"] is False
    assert external_proof["unbounded_claim_allowed"] is False


def test_frozen_v050_external_candidate_keeps_its_historical_identity() -> None:
    repository = Path(__file__).resolve().parents[1]
    snapshot = json.loads(
        (
            repository
            / "benchmarks"
            / "knowledge-os-v0.5.0-candidate-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )
    assert snapshot["implementation"]["python_source_tree_sha256"] == (
        "a8a0561534f85bfd28618a5a9ff99eebc35294793f99f9dfb7ad4b636fb67f38"
    )
    external_proof = snapshot["external_proof"]
    assert external_proof["status"] == "pending_external_execution"
    assert external_proof["unbounded_claim_allowed"] is False
    protocol = json.loads(
        (
            repository / "benchmarks/external/protocol-v3.json"
        ).read_text(encoding="utf-8")
    )
    protocol_canonical = json.dumps(
        protocol,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert (
        hashlib.sha256(protocol_canonical).hexdigest()
        == external_proof["protocol_canonical_sha256"]
    )
    pending = json.loads(
        (
            repository / "benchmarks/external/claim-evidence.pending.json"
        ).read_text(encoding="utf-8")
    )
    candidate_identity = json.loads(
        (
            repository / "benchmarks/external/candidate-v0.5.0.json"
        ).read_text(encoding="utf-8")
    )
    assert pending["protocol_id"] == external_proof["protocol"]
    assert candidate_identity == pending["candidate"]
    assert pending["candidate"]["git_commit"] == external_proof["candidate_git_commit"]
    assert (
        pending["candidate"]["artifact_sha256"]
        == external_proof["candidate_wheel_sha256"]
    )

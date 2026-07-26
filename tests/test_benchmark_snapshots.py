from __future__ import annotations

import hashlib
import json
import tomllib
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


def _python_source_tree_sha256(repository: Path) -> str:
    source_tree = hashlib.sha256()
    for path in sorted((repository / "src" / "deeplaw").rglob("*.py")):
        source_tree.update(path.relative_to(repository).as_posix().encode("utf-8"))
        source_tree.update(b"\0")
        source_tree.update(path.read_bytes())
        source_tree.update(b"\0")
    return source_tree.hexdigest()


def test_historical_legal_benchmark_fixtures_are_immutable() -> None:
    repository = Path(__file__).resolve().parents[1]

    _assert_snapshot(repository, "core-v0.4.0-candidate-2026-07-26.json")
    _assert_snapshot(repository, "core-v0.4.0-candidate-2026-07-25.json")
    _assert_snapshot(repository, "core-v5-candidate-2026-07-15.json")


def test_current_knowledge_os_candidate_snapshot_is_source_bound() -> None:
    repository = Path(__file__).resolve().parents[1]
    snapshot = json.loads(
        (
            repository
            / "benchmarks"
            / "knowledge-os-v0.5.0-candidate-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )
    implementation = snapshot["implementation"]
    project = tomllib.loads(
        (repository / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert snapshot["schema_version"] == "deeplaw.knowledge-os-candidate/v1"
    assert snapshot["status"] == "candidate_development_not_held_out"
    assert snapshot["package_version"] == project["project"]["version"] == "0.5.0"
    assert (
        hashlib.sha256((repository / "pyproject.toml").read_bytes()).hexdigest()
        == implementation["pyproject_sha256"]
    )
    assert (
        hashlib.sha256((repository / "uv.lock").read_bytes()).hexdigest()
        == implementation["uv_lock_sha256"]
    )
    assert (
        _python_source_tree_sha256(repository)
        == implementation["python_source_tree_sha256"]
    )

    for evidence in snapshot["evidence"].values():
        path = repository / evidence["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence["sha256"]
        report = json.loads(path.read_text(encoding="utf-8"))
        if "claim_eligible" in evidence:
            assert report["claim_eligible"] is evidence["claim_eligible"]

    discovery = json.loads(
        (
            repository
            / snapshot["evidence"]["discovery_development_ablation"]["path"]
        ).read_text(encoding="utf-8")
    )
    scale = json.loads(
        (
            repository
            / snapshot["evidence"]["discovery_scale_diagnostic"]["path"]
        ).read_text(encoding="utf-8")
    )
    assert discovery["candidate"]["version"] == snapshot["package_version"]
    assert discovery["decision"]["default_activation"] == "rejected"
    assert scale["candidate"]["version"] == snapshot["package_version"]
    external_proof = snapshot["external_proof"]
    assert external_proof["status"] == "pending_external_execution"
    assert snapshot["external_proof"]["unbounded_claim_allowed"] is False
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

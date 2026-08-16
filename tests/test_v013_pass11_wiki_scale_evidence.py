from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from benchmarks.v013.query_graph_scale import verify_report
from benchmarks.v013.scale_performance import verify_scale_performance_report

REPOSITORY = Path(__file__).resolve().parents[1]
EVIDENCE = (
    REPOSITORY
    / "benchmarks/v013/evidence/pass11-wiki-evidence-scale-2026-08-11"
)
MANIFEST = EVIDENCE / "SHA256SUMS.json"
LOCAL_PATH = re.compile(
    r"(?:/Users/|/home/|/tmp/|/private/var/|[A-Za-z]:[\\/])"
)


def _load(name: str) -> dict[str, object]:
    value = json.loads((EVIDENCE / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_pass11_scale_manifest_binds_exact_clean_candidate_artifacts() -> None:
    manifest = _load("SHA256SUMS.json")
    candidate = manifest["candidate"]
    assert isinstance(candidate, dict)
    assert candidate == {
        "git_commit": "69db28cb99846540e3b7c3c600f5268705405015",
        "git_tree": "a32e3218658403376cfbf9340972f9aa0caab177",
        "package_version": "0.12.0",
        "wheel_sha256": (
            "ce5631098d331325c909275d3fa6db788e0630a6b165cf82408c5922db89e33a"
        ),
    }
    assert manifest["claim_eligible"] is False
    assert manifest["release_ready"] is False

    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    assert {item["name"] for item in artifacts} == {
        "query-graph-scale-69db28c.json",
        "scale-performance-69db28c.json",
    }
    for item in artifacts:
        artifact = EVIDENCE / item["name"]
        payload = artifact.read_bytes()
        assert item["bytes"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
        assert LOCAL_PATH.search(payload.decode("utf-8")) is None


def test_pass11_performance_report_is_valid_but_retains_unmet_gates() -> None:
    report = _load("scale-performance-69db28c.json")
    assert verify_scale_performance_report(report) == {"valid": True, "errors": []}
    assert report["claim_eligible"] is False
    assert report["release_gate_passed"] is False
    environment = report["environment"]
    assert isinstance(environment, dict)
    assert environment["git_commit"] == (
        "69db28cb99846540e3b7c3c600f5268705405015"
    )
    assert environment["working_tree_dirty"] is False
    overall = report["overall"]
    assert isinstance(overall, dict)
    assert overall["operation_count"] == 63
    assert overall["executed_count"] == 48
    assert overall["pass_count"] == 17
    assert overall["fail_count"] == 0
    assert overall["not_executed_count"] == 15


def test_pass11_query_graph_report_preserves_historical_limits_without_rebinding() -> None:
    report = _load("query-graph-scale-69db28c.json")
    assert verify_report(report) == {
        "valid": False,
        "errors": [
            "Gold byte binding mismatch",
            "candidate source byte binding mismatch",
        ],
    }
    bindings = report["evidence_bindings"]
    assert isinstance(bindings, dict)
    gold = bindings["gold"]
    assert isinstance(gold, dict)
    assert gold == {
        "execution_status": "not_executed",
        "path": "benchmarks/quality/repository-gold-development-v3.json",
        "reason": "synthetic scale construction does not read or score Gold",
        "sha256": "4b64eb8db4c1c81fee3be5a9fd8ef93cb41bd2801f03cf84e34c63a9a3022fa8",
    }
    assert report["claim_eligible"] is False
    assert report["competitive_claim_eligible"] is False
    assert report["release_gate_passed"] is False
    candidate = report["candidate"]
    assert isinstance(candidate, dict)
    assert candidate["git_commit"] == "69db28cb99846540e3b7c3c600f5268705405015"
    assert candidate["git_tree"] == "a32e3218658403376cfbf9340972f9aa0caab177"
    assert candidate["wheel"] == {
        "filename": "deeplaw-0.12.0-py3-none-any.whl",
        "sha256": "ce5631098d331325c909275d3fa6db788e0630a6b165cf82408c5922db89e33a",
        "status": "bound",
    }

    lanes = report["scale_reports"]
    assert isinstance(lanes, list)
    assert [lane["scale"] for lane in lanes] == [1_000, 10_000, 100_000]
    for lane in lanes:
        statement = lane["statement"]
        graph = lane["graph"]
        assert statement["tail_recall"] is True
        assert statement["position_independent"] is True
        assert statement["candidate_bound"] == 512
        assert max(item["provider_bytes"] for item in statement["targets"]) < 65_536
        assert graph["status"] == "not_executed"
        assert "no safe equivalent audited bulk relation constructor" in graph["reason"]
    assert lanes[0]["snapshot_restore"]["status"] == "executed"
    assert lanes[0]["snapshot_restore"]["restore_valid"] is True
    assert lanes[1]["snapshot_restore"]["status"] == "not_executed"
    assert lanes[2]["snapshot_restore"]["status"] == "not_executed"

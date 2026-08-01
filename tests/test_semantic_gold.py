from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.hosts.run_semantic_host_harness import not_executed_report
from benchmarks.semantic.review_gold import confirm_candidate, validate_candidate
from benchmarks.semantic.score_semantic_run import score

REPOSITORY = Path(__file__).resolve().parents[1]
CANDIDATE = REPOSITORY / "benchmarks/semantic/semantic-gold-candidate-v1.json"


def _candidate() -> dict:
    return json.loads(CANDIDATE.read_text(encoding="utf-8"))


def test_semantic_gold_candidate_is_complete_but_not_self_confirmed() -> None:
    value = _candidate()
    digest = validate_candidate(value, repository=REPOSITORY)
    assert value["status"] == "maintainer_review_pending"
    assert value["review"] is None
    assert len(value["sources"]) == 10
    assert len(value["cases"]) == 15
    assert len({case["task_type"] for case in value["cases"]}) == 15
    assert len(digest) == 64


def test_semantic_gold_confirmation_is_explicit_and_digest_bound() -> None:
    confirmed = confirm_candidate(
        _candidate(),
        repository=REPOSITORY,
        reviewer_id="maintainer:test-reviewer",
        reason="Reviewed all public fixture labels and forbidden merges.",
        reviewed_at="2026-08-01T01:02:03Z",
    )
    assert confirmed["status"] == "maintainer_confirmed"
    assert confirmed["review"]["reviewer_id"] == "maintainer:test-reviewer"
    validate_candidate(confirmed, repository=REPOSITORY)

    confirmed["cases"][0]["notes"] = "changed after review"
    with pytest.raises(ValueError, match="does not bind"):
        validate_candidate(confirmed, repository=REPOSITORY)


def test_semantic_gold_rejects_changed_fixture_bytes(tmp_path: Path) -> None:
    value = _candidate()
    fixture = value["sources"][0]
    fixture["relative_path"] = "benchmarks/semantic/fixtures/missing.md"
    with pytest.raises(FileNotFoundError):
        validate_candidate(value, repository=REPOSITORY)


def test_real_semantic_host_unavailable_is_schema_valid_not_executed() -> None:
    gold = _candidate()
    corpus = {
        "schema_version": "deeplaw.semantic-host-corpus/v1",
        "gold_id": gold["gold_id"],
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "sources": [
            {
                "source_key": source["source_key"],
                "source_revision_id": f"sourcerev_{index:024x}",
            }
            for index, source in enumerate(gold["sources"], start=1)
        ],
    }
    report = not_executed_report(
        host="claude_code",
        host_version="unavailable",
        model_identity="unavailable",
        network_policy="offline",
        grant_id="grant_0123456789abcdef01234567",
        gold=gold,
        corpus=corpus,
        reason="The external host is not installed in core CI.",
    )
    assert report["status"] == "not_executed"
    assert report["executed"] is False
    assert len(report["runs"]) == len(gold["sources"])
    assert report["formal_release_evidence_ready"] is False


def test_semantic_scorer_refuses_pending_gold(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="maintainer-confirmed"):
        score(
            gold=_candidate(),
            host_report={},
            vault=tmp_path,
        )

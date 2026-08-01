from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.hosts.run_semantic_host_harness import (
    _provider_token_usage,
    not_executed_report,
)
from benchmarks.semantic.export_review_bundle import export_review_bundle
from benchmarks.semantic.review_gold import confirm_candidate, validate_candidate
from benchmarks.semantic.run_query_suite import _case_result
from benchmarks.semantic.score_semantic_run import _query_cost, score
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault

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


def test_semantic_review_bundle_excludes_capability_material(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="semantic review", scope="personal")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="semantic-review-test",
            operations=tuple(sorted(SINK_OPERATIONS)),
        )
        assert Path(grant["token_path"]).is_file()

    output = tmp_path / "review-bundle"
    manifest = export_review_bundle(vault, output)

    assert manifest["capability_tokens_included"] is False
    assert manifest["source_vault_verified_before_export"] is True
    assert not (output / ".deeplaw" / "capabilities").exists()
    assert not list(output.rglob("*.token"))
    with AutonomousKnowledgeStore(output, read_only=True) as store:
        assert store.vault_id == manifest["vault_id"]
        assert store.audit_head == manifest["audit_head"]


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


def _phased_corpus(gold: dict) -> dict:
    sources = []
    atlas_key = "sourcekey_" + "a" * 24
    for index, source in enumerate(gold["sources"], start=1):
        canonical_source_key = (
            atlas_key
            if source["source_key"] in {"update-v1", "update-v2"}
            else f"sourcekey_{index:024x}"
        )
        sources.append(
            {
                "source_key": source["source_key"],
                "canonical_source_key": canonical_source_key,
                "source_id": f"source_{index:024x}",
                "source_revision_id": f"sourcerev_{index:024x}",
                "phase": ("successor" if source["source_key"] == "update-v2" else "baseline"),
                "initial_lifecycle_status": (
                    "pending" if source["source_key"] == "update-v2" else "active"
                ),
                "review_manifest_sha256": f"{index:064x}",
            }
        )
    return {
        "schema_version": "deeplaw.semantic-host-corpus/v2",
        "corpus_id": "semanticcorpus_0123456789abcdef01234567",
        "gold_id": gold["gold_id"],
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "vault_id": "vault_0123456789abcdef01234567",
        "snapshot_sha256": "a" * 64,
        "grant_id": "grant_0123456789abcdef01234567",
        "sources": sources,
        "transitions": [
            {
                "operation": "activate_successor",
                "predecessor_source_key": "update-v1",
                "successor_source_key": "update-v2",
            },
            {"operation": "withdraw_source", "source_key": "retention-a"},
        ],
    }


def test_phased_semantic_host_unavailable_binds_lifecycle() -> None:
    gold = _candidate()
    corpus = _phased_corpus(gold)
    report = not_executed_report(
        host="opencode",
        host_version="unavailable",
        model_identity="unavailable",
        network_policy="offline",
        grant_id=corpus["grant_id"],
        gold=gold,
        corpus=corpus,
        reason="The external host is not installed in core CI.",
    )
    assert report["schema_version"] == "deeplaw.real-semantic-host-report/v2"
    assert len(report["binding"]["commit"]) == 40
    assert report["binding"]["package_version"] == "0.12.0"
    assert [item["phase"] for item in report["phases"]] == [
        "baseline",
        "successor",
    ]
    assert [item["status"] for item in report["transitions"]] == [
        "not_executed",
        "not_executed",
    ]
    assert report["formal_release_evidence_ready"] is False


def test_real_host_usage_accepts_only_provider_reported_turn_events() -> None:
    stdout = b"\n".join(
        (
            b'{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}',
            b'{"type":"item.completed","usage":{"input_tokens":999,"output_tokens":999}}',
            b'{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}',
        )
    )
    assert _provider_token_usage(stdout) == {
        "status": "provider_reported",
        "input_tokens": 17,
        "output_tokens": 5,
        "total_tokens": 22,
    }
    assert _provider_token_usage(b"not-json") == {
        "status": "unreported",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def test_phased_semantic_host_rejects_false_successor_identity() -> None:
    gold = _candidate()
    corpus = _phased_corpus(gold)
    successor = next(item for item in corpus["sources"] if item["source_key"] == "update-v2")
    successor["canonical_source_key"] = "sourcekey_" + "b" * 24
    with pytest.raises(ValueError, match="preserve its canonical Source identity"):
        not_executed_report(
            host="opencode",
            host_version="unavailable",
            model_identity="unavailable",
            network_policy="offline",
            grant_id=corpus["grant_id"],
            gold=gold,
            corpus=corpus,
            reason="unavailable",
        )


def test_semantic_scorer_refuses_pending_gold(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="maintainer-confirmed"):
        score(
            gold=_candidate(),
            host_report={},
            vault=tmp_path,
        )


def test_semantic_query_cost_is_closed_and_bound_to_the_host_run() -> None:
    value = {
        "schema_version": "deeplaw.semantic-query-cost/v1",
        "gold_id": "semanticgold_0123456789abcdef01234567",
        "host_report_id": "semantichostrun_0123456789abcdef01234567",
        "query_set_sha256": "0" * 64,
        "first_party_command": "deeplaw knowledge query",
        "query_count": 15,
        "total_query_tokens": 1200,
        "total_context_bytes": 4800,
        "raw_fragment_baseline_bytes": 22000,
        "measurement_method": "provider_reported",
        "budget": {
            "max_items": 8,
            "max_sources": 12,
            "max_chars": 8000,
            "max_tokens": 6000,
            "max_sensitivity": "private",
            "cold_or_warm": "warm",
        },
        "measured_at": "2026-08-01T01:02:03Z",
    }
    assert (
        _query_cost(
            value,
            gold_id=value["gold_id"],
            host_report_id=value["host_report_id"],
        )
        == value
    )
    with pytest.raises(ValueError, match="does not bind"):
        _query_cost(
            value,
            gold_id=value["gold_id"],
            host_report_id="semantichostrun_aaaaaaaaaaaaaaaaaaaaaaaa",
        )


def _query_output(
    *,
    compiled: list[dict] | None = None,
    gaps: list[dict] | None = None,
) -> dict:
    return {
        "compiled": compiled or [],
        "evidence": [],
        "gaps": gaps or [],
        "contradictions": [],
        "query_plan": {"fallback": {"used": False}},
        "metrics": {
            "provider_payload_bytes": 1024,
            "repeated_query_reused_compilation": True,
        },
        "write_performed": False,
        "authority_changed_by_ranking": False,
    }


def test_query_suite_requires_only_an_explicit_gap_for_unanswerable() -> None:
    case = next(case for case in _candidate()["cases"] if case["task_type"] == "unanswerable")
    output = _query_output(gaps=[{"code": "retrieval_gap"}])
    result = _case_result(
        case=case,
        cold=output,
        warm=output,
        cold_latency_ms=5,
        warm_latency_ms=3,
        source_ids={
            "retention-a": "sourcerev_" + "a" * 24,
            "update-v1": "sourcerev_" + "b" * 24,
            "update-v2": "sourcerev_" + "c" * 24,
        },
    )
    assert result["status"] == "passed"
    assert result["explicit_gap"] is True


def test_query_suite_rejects_withdrawn_source_selection() -> None:
    case = next(case for case in _candidate()["cases"] if case["task_type"] == "source_withdrawal")
    withdrawn = "sourcerev_" + "a" * 24
    output = _query_output(
        compiled=[
            {
                "revision_id": "knowledgerev_" + "d" * 24,
                "source_refs": [{"source_revision_id": withdrawn}],
            }
        ]
    )
    result = _case_result(
        case=case,
        cold=output,
        warm=output,
        cold_latency_ms=5,
        warm_latency_ms=3,
        source_ids={
            "retention-a": withdrawn,
            "update-v1": "sourcerev_" + "b" * 24,
            "update-v2": "sourcerev_" + "c" * 24,
        },
    )
    assert result["status"] == "failed"
    assert result["failure_reason"] == "withdrawn Source Revision was selected"

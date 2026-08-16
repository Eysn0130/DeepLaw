"""Development qualification checks for the Evidence Wiki candidate lane."""

from __future__ import annotations

from copy import deepcopy

import pytest

from benchmarks.v013.evidence_wiki_candidate import run_candidate
from benchmarks.v013.score_evidence_wiki import score_evidence_wiki
from deeplaw.util import canonical_json

_SOURCE = {
    "schema_version": "deeplaw.evidence-wiki-development-source/v1",
    "case_id": "aurora-evidence-wiki-human-agent",
    "source_filename": "aurora-evidence-policy.md",
    "source_text": (
        "# Aurora evidence\n\n"
        "Exact Source Revision bytes, quotes, locators, and version metadata remain "
        "evidence. An Agent interpretation is derived knowledge and never becomes "
        "legal Authority."
    ),
    "agent_interpretation": {
        "title": "Aurora interpretation",
        "body": "An agent interpretation remains explicitly non-authoritative.",
        "semantic_key": "interpretation:aurora",
    },
    "human_task": "Read the Wiki and exact source.",
    "agent_task": "Recover exact evidence and gaps.",
}


def _gold() -> dict[str, object]:
    return {
        "schema_version": "deeplaw.evidence-wiki-owner-task-gold/v1",
        "status": "development_human_review_pending",
        "candidate_visible_when_frozen": False,
        "claim_eligible": False,
        "case_id": "aurora-evidence-wiki-human-agent",
        "required_quote": (
            "Exact Source Revision bytes, quotes, locators, and version metadata remain "
            "evidence. An Agent interpretation is derived knowledge and never becomes "
            "legal Authority."
        ),
        "required_relation_predicate": "derived_from",
        "required_chain_nodes": [
            "source_bytes",
            "source_revision",
            "fragment",
            "locator",
            "knowledge_revision",
            "statement",
            "relation_revision",
            "ledger_current",
            "page_registry",
            "link_index",
            "resolver",
            "wiki_page",
            "backlink_or_outlink",
            "exact_evidence_read",
        ],
        "hard_requirements": {
            "source_bytes_sha256_valid": True,
            "statement_quote_sha256_valid": True,
            "locator_present": True,
            "statement_receipt_valid": True,
            "registry_and_link_index_verified": True,
            "source_fragment_resolved": True,
            "exact_source_read_matches_ingested_bytes": True,
            "agent_origin": "agent_derived",
            "agent_legal_authority": False,
            "read_write_performed": False,
            "read_audit_head_changed": False,
        },
        "known_expected_limitation": (
            "statement_target resolver may remain index_unavailable/statement_map_deferred; "
            "it must be reported."
        ),
    }


@pytest.fixture(scope="module")
def candidate() -> dict[str, object]:
    return run_candidate(_SOURCE)


def test_candidate_exercises_source_to_wiki_chain_without_payload_leak(candidate) -> None:
    serialized = canonical_json(candidate)
    assert candidate["status"] == "executed"
    assert candidate["claim_eligible"] is False
    assert candidate["competitive_claim_eligible"] is False
    assert candidate["write_performed"] is False
    assert _SOURCE["source_text"] not in serialized
    assert _SOURCE["agent_interpretation"]["body"] not in serialized

    source_read = candidate["source_read"]
    assert source_read["source_fragment_exact_match"] is True
    assert source_read["source_bytes_exact_match"] is True
    assert source_read["source_read_write_performed"] is False
    statement = candidate["statement"]
    assert statement["receipt_status"] == "present"
    assert statement["source_refs"]
    relation = candidate["relation"]
    assert relation["predicate"] == "derived_from"
    assert relation["evidence_refs"]
    assert relation["legal_authority"] is False

    wiki = candidate["wiki"]
    assert wiki["registry"]["valid"] is True
    assert wiki["link_index"]["valid"] is True
    assert wiki["link_index_used"] is True
    assert wiki["resolver"]["source_fragment"]["admitted"] is True
    assert wiki["resolver"]["claim"]["admitted"] is True
    assert wiki["resolver"]["statement_target"]["status"] == "index_unavailable"
    assert wiki["resolver"]["statement_target"]["reason"] == "statement_map_deferred"
    assert wiki["resolver"]["statement_target"]["gap"] == "statement_semantic_target_not_indexed"

    assert candidate["query"]["query_plan_version"] == "6"
    assert candidate["context"]["query_plan_version"] == "6"
    assert candidate["query"]["write_performed"] is False
    assert candidate["context"]["write_performed"] is False
    assert candidate["limits"]["provider_hard_limit_bytes"] == 65_536
    assert candidate["limits"]["query_provider_bytes"] <= 65_536
    assert candidate["limits"]["context_provider_bytes"] <= 65_536


def test_gold_only_scorer_accepts_development_candidate(candidate) -> None:
    report = score_evidence_wiki(candidate=candidate, gold=_gold())
    assert report["schema_version"] == "deeplaw.v013.evidence-wiki-score/v1"
    assert report["hard_failures"] == []
    assert report["development_thresholds_passed"] is True
    assert "statement_target_resolver_deferred" in report["known_limitations"]
    assert report["release_gate_passed"] is False
    assert report["claim_eligible"] is False
    assert report["competitive_claim_eligible"] is False


def test_scorer_reports_reproduced_chain_failure(candidate) -> None:
    broken = deepcopy(candidate)
    broken["source_read"]["source_fragment_exact_match"] = False
    report = score_evidence_wiki(candidate=broken, gold=_gold())
    assert "source_exact_read" in report["hard_failures"]
    assert report["development_thresholds_passed"] is False
    assert report["claim_eligible"] is False


def test_scorer_rejects_non_frozen_gold(candidate) -> None:
    gold = _gold()
    gold["candidate_visible_when_frozen"] = True
    with pytest.raises(ValueError, match="frozen"):
        score_evidence_wiki(candidate=candidate, gold=gold)

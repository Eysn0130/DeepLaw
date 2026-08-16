from __future__ import annotations

from copy import deepcopy

from benchmarks.v013.legal_exact_evidence_candidate import build_candidate
from benchmarks.v013.score_legal_exact_evidence import score_candidate


def _source() -> dict[str, object]:
    return {
        "schema_version": "deeplaw.legal-exact-evidence-source/v1",
        "documents": [
            {
                "path": "current.docx",
                "title": "合成测试法",
                "effective_date": "2020-01-01",
                "status": "verified_current",
                "document_number": "合成令第1号",
                "paragraphs": [
                    "合成测试法",
                    "第一章 总则",
                    "第一条 为验证权威证据链，所有引用必须绑定精确片段。",
                    "第二条 发现分数不得提升权威。",
                    "第三条 但书：紧急情况除外。",
                ],
            },
            {
                "path": "future.docx",
                "title": "合成未来办法",
                "effective_date": "2030-01-01",
                "status": "not_yet_effective",
                "paragraphs": ["合成未来办法", "第一条 未来内容不得进入当前证据。"],
            },
        ],
        "queries": [
            {
                "case_id": "current",
                "category": "exact_current",
                "query": "合成测试法 第一条",
                "purpose": "exact_citation",
            },
            {
                "case_id": "exception",
                "category": "exception",
                "query": "合成测试法 第三条",
                "purpose": "exact_citation",
            },
            {
                "case_id": "wrong-version",
                "category": "wrong_version",
                "query": "合成未来办法 第一条",
                "purpose": "as_of_version",
                "as_of": "2026-01-01",
            },
            {
                "case_id": "no-answer",
                "category": "no_answer",
                "query": "合成测试法 第九千九百九十九条",
                "purpose": "exact_citation",
            },
        ],
    }


def _gold(status: str) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.legal-exact-evidence-gold/v1",
        "gold_id": "gold-development-001",
        "status": status,
        "review": (
            {
                "independent": True,
                "reviewer_id": "independent-legal-reviewer",
                "reviewer_role": "legal_expert",
                "reason": "Independent deterministic fixture review.",
            }
            if status == "independent_legal_human_confirmed"
            else None
        ),
        "cases": [
            {
                "case_id": "current",
                "category": "exact_current",
                "answerability": "duty_evidence_available",
                "expected": {"document_title": "合成测试法", "article_label": "第一条"},
            },
            {
                "case_id": "exception",
                "category": "exception",
                "answerability": "duty_evidence_available",
                "expected": {"document_title": "合成测试法", "article_label": "第三条"},
            },
            {
                "case_id": "wrong-version",
                "category": "wrong_version",
                "answerability": "duty_not_in_corpus",
                "expected": {"blocking_gap": None},
            },
            {
                "case_id": "no-answer",
                "category": "no_answer",
                "answerability": "duty_not_in_corpus",
                "expected": {"blocking_gap": None},
            },
        ],
    }


def test_citation_audit_detects_quote_locator_receipt_and_mapping_mutation_exact_candidate(
) -> None:
    candidate = build_candidate(_source())

    assert candidate["schema_version"] == "deeplaw.legal-exact-evidence-candidate/v1"
    assert candidate["development_only"] is True
    assert candidate["source_only"] is True
    assert candidate["signed"] is False
    assert candidate["official_claimed"] is False
    assert candidate["release_claimed"] is False
    assert candidate["claim_eligible"] is False
    assert candidate["competitive_claim_eligible"] is False
    assert candidate["authority_partitions"]["preserved"] is True
    assert candidate["authority_partitions"]["official"]["legal_authority"] is True
    assert candidate["authority_partitions"]["agent_interpretation"]["legal_authority"] is False
    assert candidate["agent_interpretation"]["origin"] == "agent_derived"
    assert candidate["agent_interpretation"]["tagged"] is True

    cases = {item["case_id"]: item for item in candidate["cases"]}
    assert cases["current"]["evidence_count"] > 0
    assert cases["exception"]["evidence_count"] > 0
    assert cases["current"]["checks"]["valid_citation"] is True
    assert cases["current"]["checks"]["quote_hash_tamper_rejected"] is True
    assert cases["current"]["checks"]["quote_tamper_rejected"] is True
    assert cases["current"]["checks"]["locator_tamper_rejected"] is True
    assert cases["current"]["checks"]["receipt_tamper_rejected"] is True
    assert cases["current"]["checks"]["source_hash_tamper_rejected"] is True
    assert cases["current"]["checks"]["segment_hash_tamper_rejected"] is True
    assert cases["current"]["checks"]["version_tamper_rejected"] is True
    assert cases["wrong-version"]["evidence_count"] == 0
    assert cases["wrong-version"]["checks"]["wrong_version_excluded"] is True
    assert cases["no-answer"]["evidence_count"] == 0
    assert all(
        isinstance(value, str) and not value.startswith("/")
        for value in candidate["source_hashes"].values()
    )


def test_search_is_bounded_temporal_and_receipted_gold_gate() -> None:
    candidate = build_candidate(_source())
    pending = score_candidate(_gold("machine_review_pending"), candidate)
    assert pending["status"] == "not_eligible"
    assert pending["gold_human_review_confirmed"] is False
    assert pending["claim_eligible"] is False
    assert pending["release_eligible"] is False

    confirmed_gold = _gold("independent_legal_human_confirmed")
    confirmed = score_candidate(confirmed_gold, candidate)
    assert confirmed["gold_human_review_confirmed"] is True
    assert confirmed["status"] == "development_passed"
    assert confirmed["development_thresholds_passed"] is True
    assert confirmed["human_gold_thresholds_passed"] is True
    assert confirmed["claim_eligible"] is False
    assert confirmed["release_eligible"] is False
    assert confirmed["release_gate_passed"] is False
    assert confirmed["competitive_claim_eligible"] is False


def test_exact_unknown_title_with_valid_article_fails_closed_scorer_hard_zero() -> None:
    candidate = build_candidate(_source())
    tampered = deepcopy(candidate)
    tampered["cases"][0]["selected"][0]["citation_valid"] = False
    tampered["cases"][0]["checks"]["valid_citation"] = False
    result = score_candidate(_gold("independent_legal_human_confirmed"), tampered)
    assert result["status"] == "failed"
    assert result["claim_eligible"] is False
    assert result["release_eligible"] is False
    assert any("invalid_quote_locator_or_receipt" in failure for failure in result["failures"])


def test_owner_task_gold_reports_unsigned_temporal_evidence_as_not_eligible() -> None:
    source = {
        "schema_version": "deeplaw.legal-exact-evidence-development-source/v2",
        "case_id": "cobalt-legal-exact-evidence",
        "package": {
            "name": "Synthetic Cobalt Legal Development Pack",
            "retrieved_on": "2026-08-08",
            "documents": [
                {
                    "filename": "cobalt-regulation-2026.docx",
                    "title": "Cobalt Regulation 2026",
                    "effective_date": "2026-08-01",
                    "status": "verified_current",
                    "paragraphs": [
                        "Cobalt Regulation 2026",
                        "第一条 An operator shall preserve exact evidence.",
                        "第二条 Exception: unverifiable evidence requires a gap.",
                    ],
                },
                {
                    "filename": "cobalt-regulation-2030.docx",
                    "title": "Cobalt Regulation 2030",
                    "effective_date": "2030-01-01",
                    "status": "verified_current",
                    "paragraphs": [
                        "Cobalt Regulation 2030",
                        "第一条 Future wording is not current evidence.",
                    ],
                },
            ],
        },
        "queries": [
            {
                "case": "current_exact",
                "query": "Cobalt Regulation 2026 第一条",
                "purpose": "exact_citation",
                "as_of": "2026-08-08",
            },
            {
                "case": "exception_exact",
                "query": "Cobalt Regulation 2026 第二条 Exception",
                "purpose": "exact_citation",
                "as_of": "2026-08-08",
            },
            {
                "case": "future_wrong_version",
                "query": "Cobalt Regulation 2030 第一条",
                "purpose": "exact_citation",
                "as_of": "2026-08-08",
            },
        ],
        "agent_interpretation": "This is Agent-derived and has no legal Authority.",
    }
    gold = {
        "schema_version": "deeplaw.legal-exact-evidence-owner-task-gold/v2",
        "status": "owner_task_spec_gold_second_legal_human_review_not_executed",
        "claim_eligible": False,
        "candidate_visible_when_frozen": False,
        "case_id": "cobalt-legal-exact-evidence",
        "expected": {
            "current_exact": {
                "title": "Cobalt Regulation 2026",
                "article_label": "第一条",
                "required_quote": "An operator shall preserve exact evidence.",
            },
            "exception_exact": {
                "title": "Cobalt Regulation 2026",
                "article_label": "第二条",
                "required_quote": "Exception: unverifiable evidence requires a gap.",
            },
            "future_wrong_version": {
                "title": "Cobalt Regulation 2030",
                "evidence_count": 0,
                "required_gap": True,
            },
        },
        "hard_zero": {
            "false_authority": 0,
            "invalid_quote_or_locator": 0,
            "wrong_version_primary_evidence": 0,
        },
        "required_checks": [
            "release_and_segment_identity",
            "exact_quote_containment",
            "quote_sha256",
            "locator",
            "source_sha256",
            "segment_sha256",
            "receipt",
            "date_and_version",
            "tampered_quote_rejected",
            "tampered_locator_rejected",
        ],
        "agent_interpretation_origin": "agent_derived",
        "agent_interpretation_legal_authority": False,
    }

    candidate = build_candidate(source)
    result = score_candidate(gold, candidate)

    assert result["status"] == "not_eligible"
    assert result["development_thresholds_passed"] is False
    assert result["gold_human_review_confirmed"] is False
    assert result["claim_eligible"] is False
    assert result["release_eligible"] is False
    assert result["release_gate_passed"] is False
    assert result["metrics"]["wrong_version_inclusion"] == 0
    assert result["metrics"]["false_authority_admission"] == 0

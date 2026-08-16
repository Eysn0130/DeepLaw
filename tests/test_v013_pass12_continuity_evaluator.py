from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.evaluator import score_continuity_qualification_v2 as evaluator
from deeplaw.util import canonical_json

REPOSITORY = Path(__file__).resolve().parents[1]
GOLD_PATH = REPOSITORY / "benchmarks/evaluator/continuity-qualification-gold-v2.json"
GOLD_SCHEMA_PATH = REPOSITORY / "contracts/continuity-qualification-gold.v2.schema.json"
REVIEW_SCHEMA_PATH = REPOSITORY / "contracts/continuity-human-review.v1.schema.json"
PROVIDER_SCHEMA_PATH = REPOSITORY / "contracts/provider-knowledge-capsule.v2.schema.json"


def _gold() -> dict[str, object]:
    value = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _digest(value: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _review(
    gold: dict[str, object],
    observation: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.continuity-human-review/v1",
        "review_id": "continuityreview_0123456789abcdef01234567",
        "status": "independent_bilingual_review_complete",
        "gold_id": gold["gold_id"],
        "gold_sha256": _digest(gold),
        "case_id": gold["case_id"],
        "candidate_sha256": _digest(observation) if observation is not None else "b" * 64,
        "reviews": {
            "en": {
                "reviewer_id": "reviewer-en-independent",
                "independent": True,
                "decision": "pass",
                "criterion_results": {
                    "statement_ids": True,
                    "closed_action": True,
                    "release_state": True,
                    "gap_codes": True,
                    "forbidden_state": True,
                },
            },
            "zh": {
                "reviewer_id": "reviewer-zh-independent",
                "independent": True,
                "decision": "pass",
                "criterion_results": {
                    "statement_ids": True,
                    "closed_action": True,
                    "release_state": True,
                    "gap_codes": True,
                    "forbidden_state": True,
                },
            },
        },
        "claim_eligible": False,
    }


def _observation(gold: dict[str, object], *, language: str = "en") -> dict[str, object]:
    texts = (
        [
            "Current owner review state keeps package 0.12.0 release-closed.",
            "Independent bilingual Human review is not available.",
            "Legal qualification remains blocked without corpus and reviewer evidence.",
        ]
        if language == "en"
        else [
            "当前 owner review 状态保持 package 0.12.0 发布关闭。",
            "独立双语 Human review 尚不可用。",
            "缺少 corpus 与 reviewer 证据时，Legal qualification 保持 blocked。",
        ]
    )
    statements = [
        {
            "statement_id": statement_id,
            "statement_text": text,
            "statement_type": "factual",
            "support_status": "supported",
            "current_supported": True,
            "freshness": "fresh",
            "origin": "agent_derived",
            "authority": "none",
            "verification": "machine_checked",
            "legal_authority": False,
            "source_refs": [],
        }
        for statement_id, text in zip(gold["expected_statement_ids"], texts, strict=True)
    ]
    gaps = [
        {
            "gap_id": f"querygap_{index:024x}",
            "code": code,
            "duty": "unresolved_gap",
            "message": f"Qualification gap: {code}",
        }
        for index, code in enumerate(gold["required_gap_codes"], start=1)
    ]
    receipt_id = "queryreceipt_0123456789abcdef01234567"
    capsule = {
        "schema_version": "deeplaw.knowledge-capsule-projection/v1",
        "projection": "compact",
        "receipt_id": receipt_id,
        "hard_limit_bytes": 65_536,
        "statements": statements,
        "gaps": gaps,
        "selected_statement_count": len(statements),
        "selected_source_count": 0,
    }
    provider_bytes = len(canonical_json(capsule).encode("utf-8"))
    provider_sha256 = hashlib.sha256(canonical_json(capsule).encode("utf-8")).hexdigest()
    return {
        "schema_version": "deeplaw.continuity-candidate-observation/v2",
        "case_id": gold["case_id"],
        "candidate": {"task": "Continue the owner review using the available context."},
        "provider_capsule": {
            "schema_version": "deeplaw.provider-knowledge-capsule/v2",
            "purpose": "answer",
            "policy_id": "compiled-first-v1",
            "capsule": capsule,
            "receipt": {"receipt_id": receipt_id},
            "delivery": {
                "hard_limit_bytes": 65_536,
                "provider_content_bytes": provider_bytes,
                "projection": "compact",
                "write_performed": False,
            },
        },
        "provider_bytes": provider_bytes,
        "provider_content_sha256": provider_sha256,
        "actual_event_receipt": {
            "tool_calls": [
                {
                    "ordinal": 1,
                    "tool": "deeplaw_knowledge_knowledge_support",
                    "operation": "context",
                    "status": "completed",
                    "read_only": True,
                    "write_performed": False,
                    "result_valid": True,
                    "provider_bytes": provider_bytes,
                    "provider_content_sha256": provider_sha256,
                }
            ]
        },
        "host_output": {
            "summary": "中文或英文总结均不参与结构评分。",
            "action": gold["expected_action"],
            "release_state": copy.deepcopy(gold["expected_release_state"]),
            "gap_codes": [],
        },
    }


def _score(
    observation: dict[str, object],
    gold: dict[str, object],
    *,
    human_review: dict[str, object] | None = None,
) -> dict[str, object]:
    review = _review(gold, observation) if human_review is None else human_review
    return evaluator.score_observation(
        observation=observation,
        gold=gold,
        human_review=review,
        gold_sha256=_digest(gold),
        candidate_sha256=_digest(observation),
    )


def test_v2_gold_and_human_contracts_are_closed_and_evaluator_only() -> None:
    gold_schema = json.loads(GOLD_SCHEMA_PATH.read_text(encoding="utf-8"))
    review_schema = json.loads(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
    provider_schema = json.loads(PROVIDER_SCHEMA_PATH.read_text(encoding="utf-8"))
    gold = _gold()
    review = _review(gold)
    Draft202012Validator.check_schema(gold_schema)
    Draft202012Validator.check_schema(review_schema)
    Draft202012Validator.check_schema(provider_schema)
    Draft202012Validator(gold_schema).validate(gold)
    Draft202012Validator(review_schema).validate(review)
    Draft202012Validator(provider_schema).validate(_observation(gold)["provider_capsule"])
    assert gold["claim_eligible"] is False
    assert gold["candidate_visible_when_frozen"] is False
    assert gold["historical_evidence_binding"] == []
    assert set(gold["human_rubric"]) == {"en", "zh"}
    assert [duty["duty_label"] for duty in gold["required_duties"]] == [
        "current_release_hold",
        "independent_human_review_boundary",
        "legal_qualification_boundary",
    ]
    assert gold_schema["additionalProperties"] is False
    assert review_schema["additionalProperties"] is False


def test_chinese_and_english_prose_with_same_structure_score_full() -> None:
    gold = _gold()
    english_observation = _observation(gold, language="en")
    chinese_observation = _observation(gold, language="zh")
    english = _score(english_observation, gold)
    chinese = _score(chinese_observation, gold)
    assert english["status"] == chinese["status"] == "passed"
    assert english["hard_failures"] == chinese["hard_failures"] == []
    for report, observation in (
        (english, english_observation),
        (chinese, chinese_observation),
    ):
        metrics = report["metrics"]
        assert metrics["first_correct_action"] == 1.0
        assert metrics["decision_preservation"] == 1.0
        assert metrics["wrong_state_admission"] == 0
        assert metrics["recall_at_k"] == metrics["precision_at_k"] == 1.0
        assert metrics["mrr"] == metrics["ndcg"] == 1.0
        assert 0.0 < metrics["relevant_chars_ratio"] < 1.0
        assert metrics["redundancy"] == 0.0
        assert metrics["duplicate_evidence"] == 0
        assert metrics["duty_coverage"] == metrics["gap_correctness"] == 1.0
        capsule = observation["provider_capsule"]["capsule"]
        assert metrics["context_chars"] == len(canonical_json(capsule))
        assert metrics["provider_bytes"] == len(canonical_json(capsule).encode("utf-8"))
        assert metrics["first_call_validity"] is True
        assert metrics["retry_count"] == 0


def test_forbidden_statement_id_and_wrong_release_state_are_hard_failures() -> None:
    gold = _gold()
    observation = _observation(gold)
    observation["provider_capsule"]["capsule"]["statements"].append(
        {
            "statement_id": gold["forbidden_statement_ids"][0],
            "statement_text": "A stale state must never be admitted.",
        }
    )
    observation["host_output"]["release_state"] = {
        "package_version": "0.13.0",
        "release_ready": True,
        "claim_eligible": True,
    }
    report = _score(observation, gold)
    assert report["status"] == "failed"
    assert "forbidden_statement_id_admitted" in report["hard_failures"]
    assert "wrong_release_state_admitted" in report["hard_failures"]
    assert "wrong_state_admitted" in report["hard_failures"]
    assert report["release_ready"] is False


def test_provider_payload_boundary_and_missing_human_review_fail_closed() -> None:
    gold = _gold()
    observation = _observation(gold)
    exact_bytes = observation["provider_bytes"]
    observation["provider_bytes"] = 1
    mismatch = _score(observation, gold)
    assert "provider_payload_size_mismatch" in mismatch["hard_failures"]
    assert mismatch["metrics"]["provider_bytes"] == exact_bytes
    assert mismatch["metrics"]["observed_provider_bytes"] == 1

    oversized = _observation(gold)
    oversized["provider_capsule"]["capsule"]["statements"][0]["object_summary"] = {
        "padding": "x" * 70_000
    }
    over_limit = _score(oversized, gold)
    assert "provider_payload_overflow" in over_limit["hard_failures"]

    missing_observation = _observation(gold)
    missing_review = evaluator.score_observation(
        observation=missing_observation,
        gold=gold,
        gold_sha256=_digest(gold),
        candidate_sha256=_digest(missing_observation),
    )
    assert missing_review["status"] == "failed"
    assert missing_review["scoring_status"] == "not_scored"
    assert "human_review_missing" in missing_review["hard_failures"]
    assert missing_review["artifact_binding_verified"] is False
    assert missing_review["release_ready"] is False


def test_context_chars_cover_full_provider_content_and_duties_are_explicit() -> None:
    gold = _gold()
    observation = _observation(gold)
    report = _score(observation, gold)
    capsule = observation["provider_capsule"]["capsule"]
    statement_chars = sum(
        len(statement["statement_text"]) for statement in capsule["statements"]
    )
    assert report["metrics"]["context_chars"] == len(canonical_json(capsule))
    assert report["metrics"]["context_chars"] > statement_chars

    missing_duty = _observation(gold)
    missing_duty["provider_capsule"]["capsule"]["gaps"].pop()
    report = _score(missing_duty, gold)
    assert report["metrics"]["recall_at_k"] == 1.0
    assert report["metrics"]["duty_coverage"] < 1.0
    assert "required_duty_unsatisfied" in report["hard_failures"]


def test_bounded_safe_retry_is_separate_from_first_call_validity() -> None:
    gold = _gold()
    missing_call = _observation(gold)
    missing_call["actual_event_receipt"]["tool_calls"] = []
    missing = _score(missing_call, gold)
    assert "knowledge_support_call_missing" in missing["hard_failures"]

    retried = _observation(gold)
    final_call = retried["actual_event_receipt"]["tool_calls"][0]
    final_call["ordinal"] = 2
    retried["actual_event_receipt"]["tool_calls"] = [
        {
            "ordinal": 1,
            "tool": "deeplaw_knowledge_knowledge_support",
            "operation": "context",
            "status": "failed",
            "read_only": True,
            "write_performed": False,
            "result_valid": False,
            "provider_bytes": 0,
            "provider_content_sha256": "0" * 64,
        },
        final_call,
    ]
    retried_report = _score(retried, gold)
    assert retried_report["status"] == "passed"
    assert retried_report["metrics"]["first_call_validity"] is False
    assert retried_report["metrics"]["retry_count"] == 1

    unsafe = copy.deepcopy(retried)
    unsafe["actual_event_receipt"]["tool_calls"][1]["write_performed"] = True
    unsafe_report = _score(unsafe, gold)
    assert "unsafe_or_write_tool_call" in unsafe_report["hard_failures"]


def test_invalid_provider_shape_and_review_digest_fail_closed() -> None:
    gold = _gold()
    missing_text = _observation(gold)
    del missing_text["provider_capsule"]["capsule"]["statements"][0][
        "statement_text"
    ]
    invalid_provider = _score(missing_text, gold)
    assert "provider_capsule_schema_invalid" in invalid_provider["hard_failures"]

    observation = _observation(gold)
    invalid_review = _review(gold, observation)
    invalid_review["gold_sha256"] = "not-a-digest"
    invalid_review_report = evaluator.score_observation(
        observation=observation,
        gold=gold,
        human_review=invalid_review,
        gold_sha256="not-a-digest",
        candidate_sha256=invalid_review["candidate_sha256"],
    )
    assert "human_review_invalid" in invalid_review_report["hard_failures"]


def test_candidate_cannot_expose_gold_material_or_exact_target_ids() -> None:
    gold = _gold()
    contaminated = _observation(gold)
    contaminated["candidate"] = {
        "prompt": gold["expected_action"],
        "expected_statement_ids": gold["expected_statement_ids"],
    }
    report = _score(contaminated, gold)
    assert report["status"] == "failed"
    assert "candidate_gold_material_exposed" in report["hard_failures"]
    assert report["isolation"] == {
        "candidate_gold_material_exposed": True,
        "gold_candidate_separated": False,
    }

    embedded = _observation(gold)
    embedded["candidate"] = {
        "prompt": f"Continue the review, then emit {gold['expected_action']} as instructed."
    }
    embedded_report = _score(embedded, gold)
    assert "candidate_gold_material_exposed" in embedded_report["hard_failures"]


def test_scorer_does_not_use_natural_language_for_closed_fields() -> None:
    gold = _gold()
    observation = _observation(gold, language="zh")
    observation["host_output"]["summary"] = (
        "preserve_release_hold and package 0.12.0 release_ready=false"
    )
    observation["host_output"]["action"] = "different_closed_action"
    report = _score(observation, gold)
    assert report["metrics"]["first_correct_action"] == 0.0
    assert "expected_action_mismatch" in report["hard_failures"]


def test_review_loader_rejects_missing_bilingual_review(tmp_path: Path) -> None:
    gold = _gold()
    path = tmp_path / "review.json"
    review = _review(gold)
    del review["reviews"]["zh"]
    path.write_text(canonical_json(review), encoding="utf-8")
    with pytest.raises(ValueError, match="Human review"):
        evaluator.load_human_review(path, gold=gold)


def test_score_files_requires_review_to_bind_exact_candidate_and_gold_bytes(
    tmp_path: Path,
) -> None:
    gold = _gold()
    observation = _observation(gold)
    gold_path = tmp_path / "gold.json"
    candidate_path = tmp_path / "candidate.json"
    review_path = tmp_path / "review.json"
    gold_bytes = canonical_json(gold).encode("utf-8")
    candidate_bytes = canonical_json(observation).encode("utf-8")
    gold_path.write_bytes(gold_bytes)
    candidate_path.write_bytes(candidate_bytes)
    review = _review(gold, observation)
    review["gold_sha256"] = hashlib.sha256(gold_bytes).hexdigest()
    review["candidate_sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    review_path.write_text(canonical_json(review), encoding="utf-8")

    report = evaluator.score_files(candidate_path, gold_path, review_path)
    assert report["status"] == "passed"
    assert report["artifact_binding_verified"] is True

    candidate_path.write_text(canonical_json(observation) + "\n", encoding="utf-8")
    mismatch = evaluator.score_files(candidate_path, gold_path, review_path)
    assert mismatch["status"] == "failed"
    assert "human_review_digest_binding_mismatch" in mismatch["hard_failures"]
    assert mismatch["artifact_binding_verified"] is False


def test_gold_digest_is_not_pass11_binding() -> None:
    old_gold = REPOSITORY / "benchmarks/evaluator/continuity-qualification-gold-v1.json"
    new_gold = REPOSITORY / "benchmarks/evaluator/continuity-qualification-gold-v2.json"
    assert hashlib.sha256(old_gold.read_bytes()).hexdigest() != hashlib.sha256(
        new_gold.read_bytes()
    ).hexdigest()
    assert "continuity-qualification-gold-v1" not in new_gold.read_text(encoding="utf-8")

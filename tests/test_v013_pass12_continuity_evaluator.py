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
        {"statement_id": statement_id, "statement_text": text}
        for statement_id, text in zip(gold["expected_statement_ids"], texts, strict=True)
    ]
    gaps = [{"code": code} for code in gold["required_gap_codes"]]
    return {
        "schema_version": "deeplaw.continuity-candidate-observation/v2",
        "case_id": gold["case_id"],
        "candidate": {"task": "Continue the owner review using the available context."},
        "provider_capsule": {
            "capsule": {
                "statements": statements,
                "gaps": gaps,
            }
        },
        "provider_bytes": 2_048,
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
    gold = _gold()
    review = _review(gold)
    Draft202012Validator.check_schema(gold_schema)
    Draft202012Validator.check_schema(review_schema)
    Draft202012Validator(gold_schema).validate(gold)
    Draft202012Validator(review_schema).validate(review)
    assert gold["claim_eligible"] is False
    assert gold["candidate_visible_when_frozen"] is False
    assert gold["historical_evidence_binding"] == []
    assert set(gold["human_rubric"]) == {"en", "zh"}
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
    for metrics in (english["metrics"], chinese["metrics"]):
        assert metrics["first_correct_action"] == 1.0
        assert metrics["decision_preservation"] == 1.0
        assert metrics["wrong_state_admission"] == 0
        assert metrics["recall_at_k"] == metrics["precision_at_k"] == 1.0
        assert metrics["mrr"] == metrics["ndcg"] == 1.0
        assert metrics["relevant_chars_ratio"] == 1.0
        assert metrics["redundancy"] == 0.0
        assert metrics["duplicate_evidence"] == 0
        assert metrics["duty_coverage"] == metrics["gap_correctness"] == 1.0
        assert metrics["provider_bytes"] == 2_048


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
    observation["provider_bytes"] = 65_536
    at_limit = _score(observation, gold)
    assert "provider_payload_overflow" not in at_limit["hard_failures"]

    observation["provider_bytes"] = 65_537
    over_limit = _score(observation, gold)
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

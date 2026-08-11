from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from benchmarks.evaluator.score_token_attribution_observation import evaluate

REPOSITORY = Path(__file__).resolve().parents[1]
EVIDENCE = (
    REPOSITORY
    / "benchmarks/hosts/evidence/pass11-token-attribution-2026-08-11"
)
GOLD = REPOSITORY / "benchmarks/evaluator/continuity-qualification-gold-v1.json"


def test_evaluator_scores_only_post_run_provider_capsules(tmp_path: Path) -> None:
    first = evaluate(
        observation_path=EVIDENCE
        / "attempt-1/codex-token-attribution-observation.json",
        gold_path=GOLD,
        output_path=tmp_path / "score.json",
    )
    assert first["status"] == "partial"
    conditions = {item["condition_id"]: item for item in first["conditions"]}
    assert conditions["B"]["scoring_status"] == "scored"
    assert conditions["B"]["metrics"]["first_correct_action"] == 0.0
    assert conditions["B"]["metrics"]["decision_preservation"] == 0.5
    assert conditions["B"]["metrics"]["wrong_state_admission"] == 0
    assert conditions["B"]["metrics"]["useful_context_recall"] == 0.666667
    assert conditions["B"]["duplicate_evidence"] == 0
    assert conditions["B"]["redundancy"] == 0.0
    assert conditions["A"]["scoring_status"] == "not_scored"
    assert all(value is None for value in conditions["A"]["metrics"].values())


def test_failed_attempts_do_not_turn_missing_provider_into_zero(tmp_path: Path) -> None:
    report = evaluate(
        observation_path=EVIDENCE
        / "attempt-3/codex-token-attribution-observation.json",
        gold_path=GOLD,
        output_path=tmp_path / "score.json",
    )
    assert report["status"] == "failed"
    assert report["profile_change_admitted"] is False
    assert all(item["scoring_status"] == "not_scored" for item in report["conditions"])
    assert all(
        "provider_capsule_missing" in item["hard_failures"]
        for item in report["conditions"]
    )


def test_evaluation_contract_is_closed() -> None:
    schema = json.loads(
        (
            REPOSITORY
            / "contracts/codex-token-attribution-evaluation.v1.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["release_ready"] == {"const": False}
    assert schema["properties"]["profile_change_admitted"] == {"const": False}

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY = Path(__file__).resolve().parents[1]
CASES = REPOSITORY / "benchmarks/hosts/pass16-continuity-task-cases-v1.json"
SCHEMA = REPOSITORY / "contracts/host-continuity-task-cases.v1.schema.json"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_pass16_task_cases_and_thresholds_are_frozen_before_model_output() -> None:
    schema = _load(SCHEMA)
    cases = _load(CASES)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(cases)
    assert cases["model_outputs_seen_before_freeze"] is False
    assert cases["development_tuning_material"] is False
    assert cases["hosts"] == ["codex", "opencode"]
    assert [case["scenario"] for case in cases["task_cases"]] == [  # type: ignore[index]
        "cold_start",
        "resume_fork",
        "compaction_forget",
    ]


def test_each_task_has_fresh_stale_wrong_task_and_wrong_worktree_challenges() -> None:
    cases = _load(CASES)
    all_markers: list[str] = []
    for case in cases["task_cases"]:  # type: ignore[index]
        challenges = case["wrong_state_challenges"]
        assert {challenge["challenge"] for challenge in challenges} == {
            "stale_checkpoint",
            "wrong_task_line",
            "wrong_worktree",
        }
        assert all(challenge["maximum_admission_count"] == 0 for challenge in challenges)
        stale_challenge = next(
            challenge for challenge in challenges if challenge["challenge"] == "stale_checkpoint"
        )
        assert stale_challenge["marker"] == case["stale_checkpoint"]["marker"]
        markers = [case["current_checkpoint"]["marker"], case["stale_checkpoint"]["marker"]]
        markers.extend(
            challenge["marker"]
            for challenge in challenges
            if challenge["challenge"] != "stale_checkpoint"
        )
        if case["post_forget_requirement"] is not None:
            markers.append(case["post_forget_requirement"]["forgotten_marker"])
        assert len(markers) == len(set(markers))
        review = case["required_human_review"]
        assert len(review["criterion_ids"]) == len(review["criteria"])
        assert "every criterion is true" in review["pass_condition"]
        all_markers.extend(markers)
    assert len(all_markers) == len(set(all_markers))


def test_scoring_is_fail_closed_and_contains_no_candidate_result() -> None:
    cases = _load(CASES)
    scoring = cases["scoring_rules"]
    assert scoring["maximum_wrong_state_admission"] == 0  # type: ignore[index]
    assert scoring["maximum_stale_state_admission"] == 0  # type: ignore[index]
    assert scoring["maximum_forgotten_state_admission"] == 0  # type: ignore[index]
    assert scoring["require_actual_provider_bytes"] is True  # type: ignore[index]
    assert scoring["require_actual_provider_tokens"] is True  # type: ignore[index]
    assert "human_gold_threshold_miss" in scoring["hard_failures"]  # type: ignore[index]
    assert all(
        set(case).isdisjoint({"result", "score", "passed", "model_output"})
        for case in cases["task_cases"]  # type: ignore[index]
    )

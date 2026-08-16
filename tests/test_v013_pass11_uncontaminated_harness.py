from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.hosts import run_codex_continuity_qualification as qualification
from deeplaw.util import canonical_json

REPOSITORY = Path(__file__).resolve().parents[1]
CANDIDATE_FIXTURE = (
    REPOSITORY
    / "benchmarks/v013/qualification/candidate/continuity-task-suite-v1.json"
)
EVALUATOR_GOLD = (
    REPOSITORY
    / "benchmarks/evaluator/continuity-qualification-gold-v1.json"
)

EVALUATOR_ONLY_LABELS = (
    "expected_first_action",
    "expected_decision",
    "expected_marker",
    "first_correct_action",
    "checkpoint_marker",
    "forbidden_markers",
)


def _render(value: object) -> str:
    return canonical_json(value).lower()


def test_candidate_fixture_and_evaluator_gold_are_separate_surfaces() -> None:
    assert CANDIDATE_FIXTURE.is_file()
    assert EVALUATOR_GOLD.is_file()
    assert "candidate" in CANDIDATE_FIXTURE.parts
    assert "evaluator" not in CANDIDATE_FIXTURE.parts
    assert "evaluator" in EVALUATOR_GOLD.parts
    assert "candidate" not in EVALUATOR_GOLD.parts

    candidate = qualification._candidate_fixture(CANDIDATE_FIXTURE)
    rendered = _render(candidate)
    assert all(label not in rendered for label in EVALUATOR_ONLY_LABELS)
    assert "gold" not in rendered
    assert "scorer" not in rendered
    assert "marker" not in rendered


def test_prompt_uses_natural_task_and_binding_without_target_or_labels(
    tmp_path: Path,
) -> None:
    candidate = qualification._candidate_fixture(CANDIDATE_FIXTURE)
    seeded = qualification._seed_vault(tmp_path / "vault", candidate)
    prompt = qualification._prompt(candidate, seeded["task_binding"])
    rendered_schema = _render(qualification._FINAL_RESPONSE_SCHEMA)

    assert candidate["task"] in prompt
    assert seeded["task_binding"]["project_sha256"] in prompt
    assert "knowledge_id" not in prompt
    assert "query_target" not in prompt
    assert all(label not in prompt for label in EVALUATOR_ONLY_LABELS)
    assert all(label not in rendered_schema for label in EVALUATOR_ONLY_LABELS)
    assert all(
        value not in prompt
        for key, value in seeded.items()
        if key.endswith("knowledge_id") or key.endswith("revision_id")
    )
    assert set(inspect.signature(qualification._prompt).parameters) == {
        "fixture",
        "binding",
    }

    evaluator = importlib.import_module(
        "benchmarks.evaluator.score_continuity_qualification"
    )
    gold = evaluator.load_gold(EVALUATOR_GOLD)
    assert gold["expected_first_action"] not in CANDIDATE_FIXTURE.read_text(
        encoding="utf-8"
    )
    assert gold["expected_first_action"] not in prompt


def test_natural_discovery_rejects_wrong_routes_and_stale_revision(
    tmp_path: Path,
) -> None:
    candidate = qualification._candidate_fixture(CANDIDATE_FIXTURE)
    seeded = qualification._seed_vault(tmp_path / "vault", candidate)
    preflight = qualification._preflight(tmp_path / "vault", candidate, seeded)

    assert preflight["query_target_used"] is False
    assert preflight["correct_state_admitted"] is True
    assert preflight["wrong_state_admission"] == 0
    assert preflight["wrong_state_rejections"] == {
        "repository": True,
        "stale_revision": True,
        "task_line": True,
        "worktree": True,
    }
    assert preflight["stale_snapshot_gap"] == "workspace_diverged"
    assert preflight["provider_bytes"] <= 65_536
    provider = _render(preflight["provider_capsule"])
    assert "query_target" not in provider
    assert all(label not in provider for label in EVALUATOR_ONLY_LABELS)
    evaluator = importlib.import_module(
        "benchmarks.evaluator.score_continuity_qualification"
    )
    gold = evaluator.load_gold(EVALUATOR_GOLD)
    assert gold["expected_first_action"].casefold() not in provider


def test_evaluator_scores_host_output_only_after_candidate_observation() -> None:
    evaluator = importlib.import_module(
        "benchmarks.evaluator.score_continuity_qualification"
    )
    gold = evaluator.load_gold(EVALUATOR_GOLD)
    observation = {
        "case_id": gold["case_id"],
        "host_output": {
            "summary": "The source candidate remains release closed.",
            "next_step": gold["expected_first_action"],
            "preserved_decisions": list(gold["required_decision_units"]),
            "open_gaps": list(gold["required_gap_units"]),
            "artifact_refs": ["commit:539007a"],
        },
        "provider_capsule": {
            "capsule": {
                "statements": [
                    {
                        "statement_text": "\n".join(gold["required_context_units"]),
                    }
                ],
                "gaps": [
                    {"code": code} for code in gold["acceptable_gap_codes"]
                ],
            }
        },
        "provider_bytes": 2048,
    }

    report = evaluator.score_observation(observation=observation, gold=gold)
    assert report["metrics"] == {
        "first_correct_action": 1.0,
        "decision_preservation": 1.0,
        "wrong_state_admission": 0,
        "useful_context_recall": 1.0,
        "relevant_chars": report["metrics"]["relevant_chars"],
        "context_chars": report["metrics"]["context_chars"],
        "relevant_chars_context_chars": report["metrics"][
            "relevant_chars_context_chars"
        ],
        "duty_coverage": 1.0,
        "gap_correctness": 1.0,
    }
    assert report["metrics"]["relevant_chars"] > 0
    assert report["metrics"]["context_chars"] >= report["metrics"]["relevant_chars"]
    assert report["release_ready"] is False
    assert report["claim_eligible"] is False
    assert report["hard_failures"] == []


def test_evaluator_flags_wrong_state_and_provider_overflow() -> None:
    evaluator = importlib.import_module(
        "benchmarks.evaluator.score_continuity_qualification"
    )
    gold = evaluator.load_gold(EVALUATOR_GOLD)
    report = evaluator.score_observation(
        observation={
            "case_id": gold["case_id"],
            "host_output": {
                "summary": gold["forbidden_state_units"][0],
                "next_step": "Publish now.",
                "preserved_decisions": [],
                "open_gaps": [],
                "artifact_refs": [],
            },
            "provider_capsule": {"capsule": {"statements": [], "gaps": []}},
            "provider_bytes": 65_537,
        },
        gold=gold,
    )
    assert report["metrics"]["wrong_state_admission"] == 1
    assert report["hard_failures"] == [
        "provider_payload_invalid_or_overflow",
        "wrong_state_admitted",
    ]


def test_candidate_fixture_contains_no_evaluator_path_reference() -> None:
    candidate_bytes = CANDIDATE_FIXTURE.read_bytes()
    gold_name = EVALUATOR_GOLD.name.encode("utf-8")
    assert gold_name not in candidate_bytes
    parsed = json.loads(candidate_bytes)
    assert "evaluator" not in _render(parsed)

    runner_source = inspect.getsource(qualification).lower()
    assert "benchmarks.evaluator" not in runner_source
    observation_contract = (
        REPOSITORY / "contracts/codex-continuity-observation.v1.schema.json"
    ).read_text(encoding="utf-8")
    assert all(
        label not in observation_contract.lower() for label in EVALUATOR_ONLY_LABELS
    )
    Draft202012Validator.check_schema(json.loads(observation_contract))

    host_configuration = _render(
        {
            "argv": qualification._codex_argv(),
            "response_schema": qualification._FINAL_RESPONSE_SCHEMA,
        }
    )
    assert all(label not in host_configuration for label in EVALUATOR_ONLY_LABELS)
    assert "gold" not in host_configuration
    assert "scorer" not in host_configuration


def test_candidate_output_must_be_outside_repository(tmp_path: Path) -> None:
    outside = qualification._candidate_output_directory(
        tmp_path / "candidate-output",
        repository=REPOSITORY,
    )
    assert outside == (tmp_path / "candidate-output").resolve()
    with pytest.raises(ValueError, match="outside the repository"):
        qualification._candidate_output_directory(
            REPOSITORY / "output/candidate-output",
            repository=REPOSITORY,
        )

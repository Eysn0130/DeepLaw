from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from benchmarks.v013.continuity_candidate import (
    build_candidate,
)
from benchmarks.v013.continuity_candidate import (
    build_parser as build_candidate_parser,
)
from benchmarks.v013.score_continuity import score_continuity


def _sources() -> tuple[dict[str, object], dict[str, object]]:
    thread_a = {
        "case_id": "continuity-case-1",
        "thread_id": "thread-a-development",
        "task": "Persist the bounded continuity checkpoint for Thread B.",
        "checkpoint": {
            "title": "Continuity checkpoint",
            "semantic_key": "checkpoint:continuity-case-1:slot-0",
            "initial_body": "checkpoint-state-v1: cursor=3; phase=prepare",
            "current_body": "checkpoint-state-v1: cursor=7; phase=review",
            "expires_at": "2099-01-01T00:00:00Z",
            "tags": ["checkpoint", "continuity-case-1"],
        },
        "distractor": {
            "title": "Unrelated state",
            "semantic_key": "checkpoint:other-case:slot-0",
            "body": "unrelated-state-v1: cursor=99; phase=discard",
            "tags": ["checkpoint", "other-case"],
        },
    }
    thread_b = {
        "case_id": "continuity-case-1",
        "thread_id": "thread-b-only",
        "task": "Resume checkpoint:continuity-case-1:slot-0 at the saved cursor.",
    }
    return thread_a, thread_b


def _candidate(
    *,
    mode: str,
    decision: str,
    selected: list[dict[str, object]],
    provider_bytes: int,
    context_recovered: bool,
    input_roles: list[str],
) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.continuity-candidate/v1",
        "case_id": "continuity-case-1",
        "mode": mode,
        "status": "executed",
        "claim_eligible": False,
        "competitive_claim_eligible": False,
        "source_hashes": {
            "thread_b": "b" * 64,
            **({"thread_a": "a" * 64} if mode == "host-plus-deeplaw" else {}),
        },
        "selected_statements": selected,
        "provider_selected_statements": selected,
        "gap_codes": [],
        "contradictions": [],
        "context_recovered": context_recovered,
        "stale_revision_selected": False,
        "distractor_selected": False,
        "decision": decision,
        "decision_basis": "current_working_revision_selected"
        if context_recovered
        else "no_current_checkpoint_selected",
        "provider_bytes": provider_bytes,
        "local_bytes": provider_bytes,
        "provider_limit_bytes": 65_536,
        "local_limit_bytes": 262_144,
        "latency_ms": 1.0,
        "write_performed": False,
        "audit_head_before": "c" * 64 if mode == "host-plus-deeplaw" else None,
        "audit_head_after": "c" * 64 if mode == "host-plus-deeplaw" else None,
        "audit_head_unchanged": True if mode == "host-plus-deeplaw" else None,
        "current_knowledge_id": "knowledge-current" if context_recovered else None,
        "current_revision_id": "revision-current" if context_recovered else None,
        "stale_revision_id": "revision-stale" if context_recovered else None,
        "distractor_knowledge_id": "knowledge-distractor" if context_recovered else None,
        "current_body_sha256": "d" * 64 if context_recovered else None,
        "initial_body_sha256": "e" * 64 if context_recovered else None,
        "distractor_body_sha256": "f" * 64 if context_recovered else None,
        "error_code": None,
        "input_roles": input_roles,
        "generated_ms": 1.0,
    }


def test_candidate_modes_keep_thread_a_out_of_host_only_and_restore_current_ids(
    tmp_path: Path,
) -> None:
    thread_a, thread_b = _sources()
    host_only = build_candidate("host-only", thread_b_source=thread_b)
    host_plus = build_candidate(
        "host-plus-deeplaw",
        thread_a_source=thread_a,
        thread_b_source=thread_b,
    )

    assert host_only["input_roles"] == ["thread_b"]
    assert set(host_only["source_hashes"]) == {"thread_b"}
    assert host_only["current_knowledge_id"] is None
    assert host_only["selected_statements"] == []
    assert host_only["provider_bytes"] == 0
    assert host_plus["input_roles"] == ["thread_a", "thread_b"]
    assert set(host_plus["source_hashes"]) == {"thread_a", "thread_b"}
    assert host_plus["current_knowledge_id"]
    assert host_plus["current_revision_id"]
    assert host_plus["current_revision_id"] != host_plus["stale_revision_id"]
    assert host_plus["distractor_knowledge_id"]
    assert host_plus["stale_revision_selected"] is False
    assert host_plus["distractor_selected"] is False
    assert host_plus["write_performed"] is False
    assert host_plus["audit_head_unchanged"] is True
    assert host_plus["provider_bytes"] <= 65_536
    assert host_plus["local_bytes"] <= 262_144

    serialized = json.dumps(host_plus, ensure_ascii=False, sort_keys=True)
    assert re.search(r"(?:/Users/|/home/|/tmp/|/private/var/|[A-Za-z]:[\\/])", serialized) is None
    assert "checkpoint-state-v1: cursor=3; phase=prepare" not in serialized
    assert "unrelated-state-v1: cursor=99; phase=discard" not in serialized
    selected_text = [
        item.get("statement_text")
        for item in host_plus["selected_statements"]
        if isinstance(item, dict)
    ]
    assert all(isinstance(item, str) and len(item) <= 2_000 for item in selected_text)

    with pytest.raises(ValueError, match="host-only accepts only Thread B source"):
        build_candidate(
            "host-only",
            thread_a_source=thread_a,
            thread_b_source=thread_b,
        )


def test_candidate_interface_does_not_accept_label_or_evaluator_inputs() -> None:
    parameters = {
        parameter.lower() for parameter in inspect.signature(build_candidate).parameters
    }
    assert "gold" not in parameters
    help_text = build_candidate_parser().format_help().lower()
    assert "gold" not in help_text
    assert "scorer" not in help_text


def test_continuity_score_is_deterministic_and_keeps_claims_false() -> None:
    current_text = (
        "GOAL: Preserve the current task.\n"
        "CONFIRMED_DECISION: Keep the bounded Context seam.\n"
        "CONSTRAINT: Do not widen scope.\n"
        "OPEN_GAP: Real Host evidence is absent.\n"
        "NEXT_ACTION: Run the focused continuity evaluation.\n"
        "ARTIFACT_REF: commit:fixture."
    )
    selected = [
        {
            "statement_id": "statement-current",
            "knowledge_id": "knowledge-current",
            "knowledge_revision_id": "revision-current",
            "statement_text": current_text,
            "semantic_key": "checkpoint:continuity-case-1:slot-0",
            "origin": "agent_derived",
            "authority": "agent_derived",
            "legal_authority": False,
            "source_refs": [],
            "partition": "source_free_interpretation",
        }
    ]
    host_only = _candidate(
        mode="host-only",
        decision="start_without_checkpoint",
        selected=[],
        provider_bytes=0,
        context_recovered=False,
        input_roles=["thread_b"],
    )
    host_plus = _candidate(
        mode="host-plus-deeplaw",
        decision="Run the focused continuity evaluation.",
        selected=selected,
        provider_bytes=512,
        context_recovered=True,
        input_roles=["thread_a", "thread_b"],
    )
    host_plus["first_action"] = "Run the focused continuity evaluation."
    host_only["first_action"] = None
    gold = {
        "schema_version": "deeplaw.continuity-owner-task-gold/v1",
        "status": "owner_task_spec_gold_second_human_review_not_executed",
        "claim_eligible": False,
        "candidate_visible_when_frozen": False,
        "case_id": "continuity-case-1",
        "expected_first_action": "Run the focused continuity evaluation.",
        "required_goal_units": ["Preserve the current task."],
        "required_decision_units": ["Keep the bounded Context seam."],
        "required_constraint_units": ["Do not widen scope."],
        "required_gap_units": ["Real Host evidence is absent."],
        "required_next_action_units": ["Run the focused continuity evaluation."],
        "required_artifact_units": ["commit:fixture"],
        "forbidden_stale_units": ["Delete the Context seam."],
        "forbidden_distractor_units": ["Run an unrelated campaign."],
        "frozen_thresholds": {
            "first_correct_action": 1.0,
            "decision_preservation": 1.0,
            "maximum_stale_decision_inclusion": 0.0,
            "minimum_useful_context_recall": 1.0,
            "minimum_relevant_chars_context_chars": 0.5,
            "maximum_false_memory_admission": 0.0,
            "minimum_contradiction_gap_coverage": 1.0,
            "maximum_provider_bytes": 65_536,
            "maximum_local_context_latency_ms": 2_000.0,
            "minimum_useful_context_recall_gain_over_host_only": 1.0,
            "minimum_first_correct_action_gain_over_host_only": 1.0,
        },
    }

    report = score_continuity(
        host_only=host_only,
        host_plus_deeplaw=host_plus,
        gold=gold,
    )
    assert (
        report["gold"]["status"]
        == "owner_task_spec_gold_second_human_review_not_executed"
    )
    assert report["lanes"]["host-plus-deeplaw"]["first_correct_action"] == 1.0
    assert report["lanes"]["host-plus-deeplaw"]["decision_preservation"] == 1.0
    assert report["lanes"]["host-plus-deeplaw"]["useful_context_recall"] == 1.0
    assert report["lanes"]["host-plus-deeplaw"]["false_memory_admission"] == 0.0
    assert report["lanes"]["host-plus-deeplaw"]["stale_decision_inclusion"] == 0.0
    assert report["relative_gain"]["first_correct_action"] == 1.0
    assert report["relative_gain"]["decision_preservation"] == 1.0
    assert all(report["threshold_pass"].values())
    assert report["claim_eligible"] is False
    assert report["competitive_claim_eligible"] is False
    assert report["release_gate_passed"] is False

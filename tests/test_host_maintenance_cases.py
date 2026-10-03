from __future__ import annotations

import copy
import json
from itertools import product

import pytest

from benchmarks.hosts import maintenance_task_cases as cases


def _action(
    session: cases.MaintenanceTaskSession,
    action_id: str,
    kind: str,
    parameters: dict[str, object],
    *,
    observed_state_sha256: str | None = None,
) -> dict[str, object]:
    return {
        "action_id": action_id,
        "observed_state_sha256": observed_state_sha256 or session.state_sha256,
        "kind": kind,
        "parameters": parameters,
    }


def _contains_forbidden_projection_value(value: object) -> bool:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    return any(marker in encoded for marker in ("expected", "oracle", "gold", "score"))


def _source_update_parameters(configuration_id: str) -> dict[str, object]:
    parameters: dict[str, object] = {
        "resource_id": "orchid-archive",
        "version": "v2",
        "source_ref": "source:orchid-v2",
    }
    if configuration_id == "governed_maintenance":
        parameters["experience_id"] = "experience:governed-v2"
    return parameters


def _approve_parameters() -> dict[str, object]:
    return {
        "report_id": "amber-report",
        "status": "approved",
        "support_refs": ["support:archive-alpha", "support:archive-beta"],
    }


def test_matrix_order_and_same_budget() -> None:
    assert cases.CONFIGURATION_ORDER == (
        "no_memory",
        "frozen_unmaintained",
        "governed_maintenance",
    )
    assert cases.SCENARIO_ORDER == (
        "source_update",
        "wrong_experience",
        "independent_support",
        "unknown_action",
        "forget_then_reuse",
    )

    tasks = list(cases.iter_public_tasks())
    assert len(tasks) == 15
    assert [
        (task["configuration_id"], task["scenario_id"])
        for task in tasks
    ] == list(product(cases.CONFIGURATION_ORDER, cases.SCENARIO_ORDER))
    assert {json.dumps(task["budget"], sort_keys=True) for task in tasks} == {
        json.dumps(cases.ACTION_BUDGET, sort_keys=True)
    }
    assert len(cases.FROZEN_INPUT_SHA256) == 64


def test_public_projection_is_detached_and_contains_no_private_oracle() -> None:
    for task in cases.iter_public_tasks():
        assert not _contains_forbidden_projection_value(task)
        assert all(
            f'"{key}":' not in json.dumps(task)
            for key in ("private", "sequences", "parameters", "result", "typed_result")
        )
    projection = cases.public_task_projection("governed_maintenance", "source_update")
    assert not _contains_forbidden_projection_value(projection)
    assert "private" not in projection
    assert "expected_action" not in projection
    assert "expected_parameters" not in projection
    assert "typed_result" not in projection
    assert all("parameters" not in action for action in projection["actions"])
    assert projection["environment"]["experience"]["available_ids"] == [
        "experience:governed-v2"
    ]

    projection["environment"]["resource"]["current_version"] = "tampered"
    projection["actions"].clear()
    fresh = cases.public_task_projection("governed_maintenance", "source_update")
    assert fresh["environment"]["resource"]["current_version"] == "v1"
    assert len(fresh["actions"]) == 5

    source_revisions = fresh["environment"]["resource"]["source_revisions"]
    assert [revision["status"] for revision in source_revisions] == [
        "current",
        "available",
    ]
    assert all(
        revision["content"] == "Orchid archive policy requires amber labels."
        for revision in source_revisions
    )

    frozen = cases.public_task_projection("frozen_unmaintained", "wrong_experience")
    frozen_records = frozen["environment"]["experience"]["records"]
    stale_record = next(
        record
        for record in frozen_records
        if record["experience_id"] == "experience:shortcut-v1"
    )
    assert "v1 archive is final" in stale_record["content"]
    assert stale_record["freshness"] == "stale"
    assert stale_record["governance_status"] == "unmaintained"

    governed = cases.public_task_projection("governed_maintenance", "wrong_experience")
    governed_record = governed["environment"]["experience"]["records"][0]
    assert governed_record["governance_status"] == "governed"
    assert governed_record["freshness"] == "current"
    assert "Orchid archive policy requires amber labels." in governed_record["content"]

    forgotten = cases.public_task_projection("governed_maintenance", "forget_then_reuse")
    assert forgotten["environment"]["experience"]["forgotten_ids"] == [
        "experience:forgotten-shortcut"
    ]
    assert all(
        record["experience_id"] != "experience:forgotten-shortcut"
        for record in forgotten["environment"]["experience"]["records"]
    )
    assert "old shortcut without checking current records" not in json.dumps(forgotten)

    approval_tasks = [
        cases.public_task_projection("no_memory", scenario_id)
        for scenario_id in ("wrong_experience", "independent_support", "forget_then_reuse")
    ]
    assert len({task["task"] for task in approval_tasks}) == 3
    assert all(
        support["completeness"] == "complete"
        and support["independence"] == "independent"
        for support in approval_tasks[1]["environment"]["independent_support"]
    )
    assert approval_tasks[1]["environment"]["scenario_facts"]["support_rule"] == (
        "one_complete_independent_record_is_sufficient"
    )
    assert forgotten["environment"]["scenario_facts"]["forgotten_content_available"] is False


def test_session_is_host_driven_and_does_not_auto_execute() -> None:
    session = cases.open_task("governed_maintenance", "source_update")
    assert session.trace == []
    assert session.state["state_revision"] == 0
    assert session.state["resource"]["version"] == "v1"
    assert session.public_task["task_id"] == "maintenance-governed_maintenance-source_update"


@pytest.mark.parametrize("configuration_id", cases.CONFIGURATION_ORDER)
def test_unknown_operation_can_be_recorded_from_public_task(configuration_id: str) -> None:
    session = cases.open_task(configuration_id, "unknown_action")
    task = session.public_task
    facts = task["environment"]["scenario_facts"]
    operation_name = facts["operation_name"]
    assert operation_name == "legacy_export"
    assert operation_name in task["task"]
    assert operation_name in task["goal"]
    assert "stop" in task["goal"]
    assert facts["operation_status"] == "unknown"
    assert facts["state_change_allowed"] is False
    action = next(action for action in task["actions"] if action["kind"] == "record_unknown")
    assert action["parameter_names"] == ["action_name"]
    before = session.state_sha256
    event = session.submit(
        _action(session, "record-public-operation", action["kind"], {"action_name": operation_name})
    )
    assert event["status"] == "unknown"
    assert session.state_sha256 == before
    score = cases.score_host_trace(configuration_id, "unknown_action", session.trace)
    assert score["safe_termination"] is True
    assert score["passed"] is False


@pytest.mark.parametrize("configuration_id", cases.CONFIGURATION_ORDER)
def test_required_withdrawal_can_be_completed_from_public_task(configuration_id: str) -> None:
    session = cases.open_task(configuration_id, "independent_support")
    task = session.public_task
    environment = task["environment"]
    assert "Withdraw one independent archive record" in task["task"]
    assert "then approve" in task["task"]
    assert "withdrawn" in task["goal"]
    assert environment["scenario_facts"]["withdrawal_required"] is True
    supports = environment["independent_support"]
    withdrawn_support, remaining_support = supports
    assert all(support["status"] == "available" for support in supports)
    withdrawal = next(action for action in task["actions"] if action["kind"] == "withdraw_support")
    approval = next(action for action in task["actions"] if action["kind"] == "approve_report")
    withdrawn = session.submit(
        _action(
            session,
            "withdraw-public-support",
            withdrawal["kind"],
            {"support_id": withdrawn_support["support_id"]},
        )
    )
    assert withdrawn["status"] == "succeeded"
    approved = session.submit(
        _action(
            session,
            "approve-public-report",
            approval["kind"],
            {
                "report_id": environment["report"]["report_id"],
                "status": "approved",
                "support_refs": [remaining_support["support_id"]],
            },
        )
    )
    assert approved["status"] == "succeeded"
    score = cases.score_host_trace(configuration_id, "independent_support", session.trace)
    assert score["goal_state_reached"] is True
    assert score["passed"] is True


def test_source_update_success_and_independent_event_chain() -> None:
    session = cases.open_task("governed_maintenance", "source_update")
    event = session.submit(
        _action(
            session,
            "source-update-1",
            "submit_resource_version",
            _source_update_parameters("governed_maintenance"),
        )
    )
    assert event["status"] == "succeeded"
    assert event["result_code"] == "resource_version_submitted"
    assert event["state_mutated"] is True
    assert event["state"]["resource"]["version"] == "v2"
    assert [
        revision["status"] for revision in event["state"]["resource"]["source_revisions"]
    ] == ["superseded", "current"]
    assert session.state["state_revision"] == 1
    assert session.state["resource"]["source_ref"] == "source:orchid-v2"
    assert cases.verify_event_chain(session.trace) == {
        "valid": True,
        "errors": [],
        "event_count": 1,
        "head_sha256": event["event_sha256"],
    }
    scored = cases.score_host_trace(
        "governed_maintenance", "source_update", session.trace
    )
    assert scored["passed"] is True
    assert scored["event_chain_valid"] is True
    assert scored["state_transition_valid"] is True
    assert scored["expired_reference_used"] == []


def test_stale_duplicate_and_constraint_results_are_typed_and_bounded() -> None:
    session = cases.open_task("no_memory", "source_update")
    initial_state_sha256 = session.state_sha256
    first = session.submit(
        _action(
            session,
            "source-update-1",
            "submit_resource_version",
            _source_update_parameters("no_memory"),
        )
    )
    stale = session.submit(
        _action(
            session,
            "source-update-stale",
            "submit_resource_version",
            _source_update_parameters("no_memory"),
            observed_state_sha256=initial_state_sha256,
        )
    )
    duplicate = session.submit(
        _action(
            session,
            "source-update-1",
            "submit_resource_version",
            _source_update_parameters("no_memory"),
        )
    )
    assert first["status"] == "succeeded"
    assert stale["status"] == "rejected"
    assert stale["result_code"] == "stale_state"
    assert stale["retry_allowed"] is True
    assert duplicate["status"] == "duplicate"
    assert duplicate["result_code"] == "duplicate_action"
    assert [event["state_revision"] for event in session.trace] == [1, 1, 1]
    assert cases.verify_event_chain(session.trace)["valid"] is True
    scored = cases.score_host_trace("no_memory", "source_update", session.trace)
    assert scored["expired_reference_used"] == [2]
    assert scored["duplicate_actions"] == [2, 3]
    assert "expired_reference_used" in scored["failure_codes"]
    assert "duplicate_action" in scored["failure_codes"]
    assert scored["passed"] is False


def test_wrong_experience_and_forgotten_reuse_fail_closed() -> None:
    frozen = cases.open_task("frozen_unmaintained", "wrong_experience")
    wrong = frozen.submit(
        _action(
            frozen,
            "wrong-experience-1",
            "approve_report",
            {
                **_approve_parameters(),
                "experience_id": "experience:shortcut-v1",
            },
        )
    )
    assert wrong["status"] == "rejected"
    assert wrong["result_code"] == "wrong_experience"
    assert wrong["state_mutated"] is False
    wrong_score = cases.score_host_trace(
        "frozen_unmaintained", "wrong_experience", frozen.trace
    )
    assert wrong_score["constraint_violations"] == ["1:wrong_experience"]
    assert wrong_score["passed"] is False

    forgotten = cases.open_task("governed_maintenance", "forget_then_reuse")
    reuse = forgotten.submit(
        _action(
            forgotten,
            "forgotten-reuse-1",
            "approve_report",
            {**_approve_parameters(), "experience_id": "experience:forgotten-shortcut"},
        )
    )
    assert reuse["status"] == "rejected"
    assert reuse["result_code"] == "forgotten_experience_reuse"
    assert reuse["state_mutated"] is False
    reuse_score = cases.score_host_trace(
        "governed_maintenance", "forget_then_reuse", forgotten.trace
    )
    assert reuse_score["expired_reference_used"] == [1]
    assert reuse_score["passed"] is False


def test_independent_support_withdrawal_leaves_one_complete_basis() -> None:
    session = cases.open_task("no_memory", "independent_support")
    withdrawn = session.submit(
        _action(
            session,
            "withdraw-alpha",
            "withdraw_support",
            {"support_id": "support:archive-alpha"},
        )
    )
    assert withdrawn["status"] == "succeeded"
    assert withdrawn["result_code"] == "support_withdrawn"
    support_state = session.state["independent_support"]
    assert [support["status"] for support in support_state] == ["withdrawn", "available"]

    approved = session.submit(
        _action(
            session,
            "approve-beta",
            "approve_report",
            {
                "report_id": "amber-report",
                "status": "approved",
                "support_refs": ["support:archive-beta"],
            },
        )
    )
    assert approved["status"] == "succeeded"
    assert approved["result_code"] == "report_approved"
    assert session.state["report"]["status"] == "approved"
    assert cases.score_host_trace("no_memory", "independent_support", session.trace)[
        "passed"
    ] is True

    reverse = cases.open_task("governed_maintenance", "independent_support")
    reverse.submit(
        _action(
            reverse,
            "withdraw-beta",
            "withdraw_support",
            {"support_id": "support:archive-beta"},
        )
    )
    reverse.submit(
        _action(
            reverse,
            "approve-alpha",
            "approve_report",
            {
                "report_id": "amber-report",
                "status": "approved",
                "support_refs": ["support:archive-alpha"],
            },
        )
    )
    assert cases.score_host_trace(
        "governed_maintenance", "independent_support", reverse.trace
    )["passed"] is True

    incomplete = cases.open_task("no_memory", "independent_support")
    incomplete_event = incomplete.submit(
        _action(
            incomplete,
            "approve-incomplete",
            "approve_report",
            {
                "report_id": "amber-report",
                "status": "approved",
                "support_refs": [],
            },
        )
    )
    assert incomplete_event["status"] == "rejected"
    assert incomplete_event["result_code"] == "support_incomplete"
    assert incomplete_event["state_mutated"] is False

    note = cases.open_task("no_memory", "independent_support")
    note_event = note.submit(
        _action(
            note,
            "note-1",
            "record_report_note",
            {"report_id": "amber-report", "note": "Needs independent review."},
        )
    )
    assert note_event["status"] == "succeeded"
    assert note_event["result_code"] == "report_note_recorded"
    assert note_event["damage_class"] == "benign"
    assert note.state["report"]["status"] == "pending"
    note_score = cases.score_host_trace("no_memory", "independent_support", note.trace)
    assert note_score["benign_damage"] == [1]
    assert note_score["passed"] is False


def test_unknown_action_is_explicit_and_does_not_mutate() -> None:
    unknown = cases.open_task("governed_maintenance", "unknown_action")
    before = unknown.state_sha256
    event = unknown.submit(
        _action(
            unknown,
            "unknown-1",
            "unregistered_operation",
            {"action_name": "legacy_export"},
        )
    )
    assert event["status"] == "unknown"
    assert event["result_code"] == "unknown_action"
    assert event["state_mutated"] is False
    assert unknown.state_sha256 == before

    recorded = cases.open_task("governed_maintenance", "unknown_action")
    recorded.submit(
        _action(
            recorded,
            "unknown-1",
            "record_unknown",
            {"action_name": "legacy_export"},
        )
    )
    scored = cases.score_host_trace(
        "governed_maintenance", "unknown_action", recorded.trace
    )
    assert scored["unknown_outcome"] is True
    assert scored["termination_correct"] is True
    assert scored["safe_termination"] is True
    assert scored["passed"] is False


def test_tampered_event_chain_fails_closed() -> None:
    session = cases.open_task("no_memory", "source_update")
    session.submit(
        _action(
            session,
            "source-update-1",
            "submit_resource_version",
            _source_update_parameters("no_memory"),
        )
    )
    tampered = copy.deepcopy(session.trace)
    tampered[0]["state"]["resource"]["version"] = "v1-tampered"
    verification = cases.verify_event_chain(tampered)
    assert verification["valid"] is False
    scored = cases.score_host_trace("no_memory", "source_update", tampered)
    assert scored["event_chain_valid"] is False
    assert scored["passed"] is False

    rehashed = copy.deepcopy(session.trace)
    rehashed[0]["event_sha256"] = "f" * 64
    assert cases.verify_event_chain(rehashed)["valid"] is False


def test_invalid_case_action_and_budget_boundaries() -> None:
    with pytest.raises(cases.MaintenanceCaseError):
        cases.open_task("missing_configuration", "source_update")
    with pytest.raises(cases.MaintenanceCaseError):
        cases.public_task_projection("no_memory", "missing_scenario")

    session = cases.open_task("no_memory", "source_update")
    with pytest.raises(cases.MaintenanceActionError):
        session.submit({"action_id": "missing"})
    with pytest.raises(cases.MaintenanceActionError):
        session.submit(
            _action(
                session,
                "too-large",
                "record_report_note",
                {"report_id": "amber-report", "note": "x" * 5000},
            )
        )

    session.submit(
        _action(
            session,
            "source-update-1",
            "submit_resource_version",
            _source_update_parameters("no_memory"),
        )
    )
    for ordinal in (2, 3):
        session.submit(
            _action(
                session,
                f"unknown-{ordinal}",
                "unregistered_operation",
                {},
            )
        )
    with pytest.raises(cases.MaintenanceActionError, match="budget"):
        session.submit(
            _action(
                session,
                "unknown-4",
                "unregistered_operation",
                {},
            )
        )


def test_source_goal_accepts_valid_host_choice_without_optional_memory_reference():
    from benchmarks.hosts.maintenance_task_cases import MaintenanceTaskSession, score_host_trace

    session = MaintenanceTaskSession("governed_maintenance", "source_update")
    session.submit({
        "action_id": "host-chosen-source-update",
        "observed_state_sha256": session.state_sha256,
        "kind": "submit_resource_version",
        "parameters": {"resource_id": "orchid-archive", "version": "v2",
                       "source_ref": "source:orchid-v2"},
    })
    score = score_host_trace("governed_maintenance", "source_update", session.trace)
    assert score["goal_state_reached"] is True
    assert score["passed"] is True
    assert score["parameters_correct"] is False  # Exact reference sequence differs.
    assert score["failure_codes"] == []

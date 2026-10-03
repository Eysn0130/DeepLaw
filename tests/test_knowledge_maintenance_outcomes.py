from __future__ import annotations

import copy
import json

import pytest

import benchmarks.v013.run_knowledge_maintenance_outcomes as outcomes


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    workspace = tmp_path_factory.mktemp("knowledge-maintenance") / "workspace"
    return outcomes.build_report(workspace)


def _configuration(report: dict[str, object], configuration_id: str) -> dict[str, object]:
    configurations = report["configurations"]
    assert isinstance(configurations, list)
    configuration = next(
        item
        for item in configurations
        if isinstance(item, dict) and item.get("configuration_id") == configuration_id
    )
    assert isinstance(configuration, dict)
    return configuration


def _case(configuration: dict[str, object], case_id: str) -> dict[str, object]:
    per_case = configuration["per_case"]
    assert isinstance(per_case, list)
    observation = next(
        item for item in per_case if isinstance(item, dict) and item.get("case_id") == case_id
    )
    assert isinstance(observation, dict)
    return observation


def test_report_is_frozen_bounded_and_fail_closed(
    report: dict[str, object],
) -> None:
    assert report["status"] == "executed"
    assert report["evaluation_mode"] == "deterministic_development"
    assert report["claim_eligible"] is False
    assert report["release_gate_passed"] is False
    assert report["independent_holdout"] is False
    assert report["real_host_execution"] is False
    assert report["comparative_superiority_claim_eligible"] is False
    assert report["model_execution"] == "not_executed"
    assert report["network_used"] is False
    assert report["authentication"] == "not_requested"
    assert report["public_surfaces"] == [
        "knowledge_sink.remember",
        "knowledge_sink.forget",
        "knowledge_sink.record_run",
        "knowledge_sink.record_feedback",
        "knowledge_os.context.query_plan_v7",
        "knowledge_support.get",
    ]

    frozen = report["frozen_input"]
    assert isinstance(frozen, dict)
    assert frozen["frozen_before_candidate_execution"] is True
    assert frozen["case_order"] == list(outcomes.CASE_ORDER)
    assert frozen["repeat_count"] == outcomes.REPEAT_COUNT
    assert frozen["case_count"] == len(outcomes.CASES)
    assert frozen["input_sha256"] == outcomes.FROZEN_INPUT_SHA256

    for configuration_id in outcomes.CONFIGURATION_ORDER:
        configuration = _configuration(report, configuration_id)
        assert configuration["execution_status"] == "executed"
        assert configuration["candidate_mode"] == "deterministic_synthetic_policy"
        assert configuration["learned_from_model"] is False
        assert configuration["model_execution"] == "not_executed"
        assert configuration["repeat_count"] == outcomes.REPEAT_COUNT
        assert configuration["case_order"] == list(outcomes.CASE_ORDER)
        token_cost = configuration["token_cost"]
        assert isinstance(token_cost, dict)
        assert all(
            token_cost[field] == "unavailable"
            for field in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "monetary_cost",
            )
        )

    assert outcomes.verify_report(report) == {"valid": True, "errors": []}


def test_context_records_v7_projection_and_observed_budget(
    report: dict[str, object],
) -> None:
    for configuration_id in outcomes.CONFIGURATION_ORDER:
        configuration = _configuration(report, configuration_id)
        per_case = configuration["per_case"]
        assert isinstance(per_case, list)
        for observation in per_case:
            assert isinstance(observation, dict)
            context = observation["context"]
            assert isinstance(context, dict)
            assert context["status"] == "completed"
            assert context["query_plan_version"] == "7"
            budget = context["budget"]
            assert isinstance(budget, dict)
            assert budget["token_count_mode"] == "estimated"
            assert isinstance(budget["selected_tokens"], int)
            assert budget["selected_tokens"] >= 0
            projection = context["knowledge_revision_projection"]
            assert isinstance(projection, list)
            for revision in projection:
                assert isinstance(revision, dict)
                assert isinstance(revision["knowledge_id"], str)
                assert isinstance(revision["revision_id"], str)
                assert "content" in revision
                assert "source_free" in revision
                assert "epistemic_state" in revision


def test_frozen_and_governed_configurations_expose_maintenance_boundaries(
    report: dict[str, object],
) -> None:
    frozen = _configuration(report, "frozen_unmaintained")
    governed = _configuration(report, "governed_maintenance")

    for case_id, field in (
        ("source_change", "source_version"),
        ("status_change", "service_status"),
    ):
        stale = _case(frozen, case_id)
        assert stale["stale_usage"]
        assert any(item["field"] == field for item in stale["stale_usage"])
        assert "stale_memory_used" in stale["failures"]
        maintained = _case(governed, case_id)
        assert maintained["stale_usage"] == []
        assert maintained["constraint_violations"] == []
        assert maintained["score"]["passed"] is True

    for case_id in ("wrong_experience", "repair_reuse", "forget_nonrevive"):
        stale = _case(frozen, case_id)
        assert "known_bad_memory_used" in stale["constraint_violations"]
        assert "constraint_violation" in stale["failures"]
        assert stale["score"]["passed"] is False

    wrong = _case(governed, "wrong_experience")
    assert wrong["memory_selection"]["status"] == "not_selected"
    assert wrong["score"]["passed"] is True
    repair = _case(governed, "repair_reuse")
    assert repair["memory_selection"]["status"] == "selected"
    assert repair["attempts"][0]["memory"]["version"] == "v2"
    assert repair["score"]["passed"] is True
    forgotten = _case(governed, "forget_nonrevive")
    assert forgotten["memory_selection"] == {
        "status": "not_selected",
        "candidate_count": 0,
    }
    assert forgotten["score"]["passed"] is True

    setup = governed["maintenance_actions"]
    assert isinstance(setup, list)
    by_phase = {
        action["phase"]: action
        for action in setup
        if isinstance(action, dict)
        and action.get("phase")
        in {
            "source_change",
            "status_change",
            "wrong_experience",
            "repair_reuse",
            "forget_nonrevive",
        }
    }
    assert set(by_phase) == {
        "source_change",
        "status_change",
        "wrong_experience",
        "repair_reuse",
        "forget_nonrevive",
    }
    source_payload = json.loads(by_phase["source_change"]["request"]["body"])
    assert source_payload["version"] == "v2"
    assert source_payload["action"] == "use_cold_cache"
    status_payload = json.loads(by_phase["status_change"]["request"]["body"])
    assert status_payload["service_status"] == "paused"
    repair_payload = json.loads(by_phase["repair_reuse"]["request"]["body"])
    assert repair_payload["action"] == "verify_checksum"
    assert repair_payload["parameters"] == {"verification": "checksum"}
    assert by_phase["wrong_experience"]["request"]["operation"] == "forget"
    assert by_phase["forget_nonrevive"]["request"]["operation"] == "forget"


def test_failure_and_unknown_are_terminal_without_being_success(
    report: dict[str, object],
) -> None:
    for configuration_id in outcomes.CONFIGURATION_ORDER:
        configuration = _configuration(report, configuration_id)
        for case_id, status, failure_code in (
            ("failed_once", "failed", "environment_failure"),
            ("unknown_once", "unknown", "unknown_result"),
        ):
            observation = _case(configuration, case_id)
            attempts = observation["attempts"]
            assert isinstance(attempts, list)
            assert len(attempts) == 1
            assert observation["environment_result"]["status"] == status
            assert observation["score"]["passed"] is False
            assert observation["score"]["termination_correct"] is True
            assert observation["score"]["retry_correct"] is True
            assert failure_code in observation["failures"]


def test_run_feedback_records_use_verified_capsule_and_typed_observation(
    report: dict[str, object],
) -> None:
    for configuration_id in outcomes.CONFIGURATION_ORDER:
        configuration = _configuration(report, configuration_id)
        per_case = configuration["per_case"]
        assert isinstance(per_case, list)
        for observation in per_case:
            assert isinstance(observation, dict)
            capsule = observation["capsule"]
            verification = observation["capsule_verification"]
            assert isinstance(capsule, dict)
            assert isinstance(verification, dict)
            assert verification["valid"] is True
            assert verification["capsule_digest"] == capsule["capsule_digest"]
            assert outcomes._SHA256.fullmatch(capsule["capsule_digest"])

            environment_result = observation["environment_result"]
            run_record = observation["run_record"]
            assert isinstance(environment_result, dict)
            assert isinstance(run_record, dict)
            assert run_record["status"] == "completed"
            expected_run_status = {
                "succeeded": "succeeded",
                "failed": "failed",
                "unknown": "partial",
            }[environment_result["status"]]
            assert run_record["recorded_status"] == expected_run_status
            assert run_record["requested_status"] == expected_run_status
            assert run_record["input_sha256"] == capsule["capsule_digest"]
            output_digest = outcomes._canonical_digest(environment_result)
            assert run_record["output_sha256"] == output_digest
            assert run_record["tool_results_sha256"] == output_digest
            assert run_record["receipt_id"] == run_record["run_id"]
            assert outcomes._SHA256.fullmatch(run_record["receipt_sha256"])
            if environment_result["status"] in {"failed", "unknown"}:
                assert observation["score"]["passed"] is False
                assert environment_result["status"] != "succeeded"

            actions = observation["run_feedback_actions"]
            assert isinstance(actions, list)
            run_actions = [item for item in actions if item["phase"] == "run_record"]
            assert len(run_actions) == 1
            run_action = run_actions[0]
            request = run_action["request"]
            result = run_action["result"]
            assert request["operation"] == "record_run"
            assert request["task"] == next(
                case["input"]["task"]
                for case in outcomes.CASES
                if case["case_id"] == observation["case_id"]
            )
            assert request["host_id"] == "synthetic-knowledge-maintenance-host"
            assert request.get("model_id") is None
            assert request["input_sha256"] == capsule["capsule_digest"]
            assert request["output_sha256"] == output_digest
            assert request["tool_results_sha256"] == output_digest
            metadata = request["run_metadata"]
            assert metadata["task_kind"] == "synthetic_maintenance_fixture"
            assert metadata["artifact_ids"] == [capsule["capsule_id"]]
            binding = metadata["task_binding"]
            assert binding["schema_version"] == "deeplaw.task-context-binding/v1"
            assert outcomes._SHA256.fullmatch(binding["binding_sha256"])
            assert run_action["status"] == "completed"
            assert result["status"] == expected_run_status
            assert result["input_sha256"] == capsule["capsule_digest"]
            assert result["output_sha256"] == output_digest
            assert result["tool_results_sha256"] == output_digest
            assert result["host_id"] == "synthetic-knowledge-maintenance-host"
            assert result["model_id"] is None

            ledger = observation["ledger_verification"]
            assert ledger["valid"] is True
            assert ledger["failure_codes"] == []
            assert isinstance(ledger["warning_codes"], list)


def test_feedback_is_bound_only_to_selected_knowledge_and_self_report(
    report: dict[str, object],
) -> None:
    for configuration_id in outcomes.CONFIGURATION_ORDER:
        configuration = _configuration(report, configuration_id)
        per_case = configuration["per_case"]
        assert isinstance(per_case, list)
        for observation in per_case:
            assert isinstance(observation, dict)
            attempts = observation["attempts"]
            assert isinstance(attempts, list)
            memory = attempts[0]["memory"]
            feedback = observation["feedback"]
            actions = observation["run_feedback_actions"]
            assert isinstance(feedback, dict)
            assert isinstance(actions, list)
            feedback_actions = [item for item in actions if item["phase"] == "feedback"]
            if isinstance(memory, dict):
                assert feedback["status"] == "completed"
                assert feedback["knowledge_id"] == memory["knowledge_id"]
                assert feedback["revision_id"] == memory["revision_id"]
                assert feedback["run_id"] == observation["run_record"]["run_id"]
                assert feedback["evaluator_type"] == "agent_self_report"
                assert feedback["task_success_authority"] == "self_report_only"
                assert len(feedback_actions) == 1
                request = feedback_actions[0]["request"]
                result = feedback_actions[0]["result"]
                assert request["operation"] == "record_feedback"
                assert request["knowledge_id"] == memory["knowledge_id"]
                assert request["expected_revision_id"] == memory["revision_id"]
                assert request["run_id"] == observation["run_record"]["run_id"]
                assert request["evaluator_type"] == "agent_self_report"
                assert result["task_success_authority"] == "self_report_only"
            else:
                assert feedback["status"] == "not_attempted"
                assert feedback["error_code"] == "no_selected_knowledge"
                assert feedback_actions == []


def test_scorer_uses_exact_typed_values_and_rejects_bad_or_repeated_actions() -> None:
    unknown_expected = next(
        case["expected"] for case in outcomes.CASES if case["case_id"] == "unknown_once"
    )
    assert isinstance(unknown_expected, dict)
    one_unknown = {
        "case_id": "unknown_once",
        "attempts": [
            {
                "ordinal": 1,
                "action": "submit_export",
                "parameters": {"request_id": "export-7"},
                "environment_result": unknown_expected["typed_result"],
            }
        ],
    }
    score = outcomes.score_case_observation(unknown_expected, one_unknown)
    assert score["typed_result_correct"] is True
    assert score["outcome_unknown"] is True
    assert score["passed"] is False
    assert score["retry_correct"] is True

    repeated = copy.deepcopy(one_unknown)
    repeated["attempts"].append(copy.deepcopy(repeated["attempts"][0]))
    repeated["attempts"][1]["ordinal"] = 2
    repeated_score = outcomes.score_case_observation(unknown_expected, repeated)
    assert repeated_score["passed"] is False
    assert repeated_score["repeated_actions"]
    assert "repeated_action" in repeated_score["failure_codes"]
    assert "redo_after_non_success" in repeated_score["failure_codes"]

    wrong_action = copy.deepcopy(one_unknown)
    wrong_action["attempts"][0]["action"] = "verify_checksum"
    wrong_score = outcomes.score_case_observation(unknown_expected, wrong_action)
    assert wrong_score["action_correct"] is False
    assert wrong_score["typed_result_correct"] is True
    assert wrong_score["passed"] is False
    assert "wrong_action" in wrong_score["failure_codes"]


def test_verifier_rejects_score_cost_and_claim_tampering(
    report: dict[str, object],
) -> None:
    score_tampered = copy.deepcopy(report)
    score_tampered["configurations"][0]["per_case"][0]["score"]["passed"] = False
    assert outcomes.verify_report(score_tampered)["valid"] is False

    cost_tampered = copy.deepcopy(report)
    cost_tampered["cost"]["total_tokens"] = 0
    assert outcomes.verify_report(cost_tampered)["valid"] is False

    claim_tampered = copy.deepcopy(report)
    claim_tampered["claim_eligible"] = True
    assert outcomes.verify_report(claim_tampered)["valid"] is False

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.evaluator import score_pass16_host_continuity as scorer
from benchmarks.hosts import pass13_evidence

REPOSITORY = Path(__file__).resolve().parents[1]
TASK_CASES = REPOSITORY / "benchmarks/hosts/pass16-continuity-task-cases-v1.json"
GOLD_SCHEMA = REPOSITORY / "contracts/host-continuity-human-gold.v2.schema.json"
SCORE_SCHEMA = REPOSITORY / "contracts/host-continuity-pass17-run-score.v2.schema.json"
BLIND_REVIEW_SCHEMA = (
    REPOSITORY / "contracts/host-continuity-pass17-blind-review.v2.schema.json"
)
SHA = "a" * 64
CANDIDATE_COMMIT = "1" * 40
CANDIDATE_TREE = "2" * 40
CANDIDATE_WHEEL_SHA256 = "3" * 64
CANDIDATE_WHEEL = Path("candidate.whl")
QUALIFICATION_CONTRACT_SHA256 = hashlib.sha256(
    (REPOSITORY / "contracts/host-continuity-qualification.v2.schema.json").read_bytes()
).hexdigest()
FixtureBundle = tuple[
    Path,
    dict[str, Any],
    dict[str, Any],
    dict[tuple[str, str], dict[str, Any]],
]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _task_value() -> dict[str, Any]:
    return json.loads(TASK_CASES.read_text(encoding="utf-8"))


def _gold() -> dict[str, Any]:
    task = _task_value()
    rows = []
    for case in task["task_cases"]:
        rows.append(
            {
                "task_case": case["task_case"],
                "scenario": case["scenario"],
                "current_decision": case["current_checkpoint"]["decision"],
                "next_action": case["current_checkpoint"]["next_action"],
                "forbidden_markers": [
                    challenge["marker"] for challenge in case["wrong_state_challenges"]
                ],
                "post_forget": case["post_forget_requirement"],
                "rubric": case["required_human_review"],
            }
        )
    return {
        "schema_version": scorer.GOLD_SCHEMA_VERSION,
        "status": "independent_human_gold_frozen",
        "frozen_at": "2026-08-13T06:14:31Z",
        "gold_id": "continuitygold_0123456789abcdef01234567",
        "author_id": "external-human-author",
        "author_is_human": True,
        "independent": True,
        "model_outputs_seen_before_freeze": False,
        "development_tuning_material": False,
        "candidate_visible_when_frozen": False,
        "claim_eligible": False,
        "task_cases_sha256": hashlib.sha256(TASK_CASES.read_bytes()).hexdigest(),
        "candidate_commit": CANDIDATE_COMMIT,
        "candidate_tree": CANDIDATE_TREE,
        "candidate_wheel_sha256": CANDIDATE_WHEEL_SHA256,
        "qualification_contract_version": scorer.QUALIFICATION_SCHEMA_VERSION,
        "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
        "task_cases": rows,
    }


@pytest.fixture(autouse=True)
def _candidate_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scorer,
        "repository_binding",
        lambda repository: {
            "commit": CANDIDATE_COMMIT,
            "tree": CANDIDATE_TREE,
            "worktree_clean": True,
            "package_version": "0.12.0",
        },
    )
    monkeypatch.setattr(scorer, "sha256_file", lambda path: CANDIDATE_WHEEL_SHA256)


def _write_json(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def _boundary(kind: str) -> dict[str, Any]:
    changed = kind != "none"
    return {
        "kind": kind,
        "owner_enabled": changed,
        "read_mcp_write_performed": False,
        "audit_changed": changed,
        "audit_head_before": "1" * 64,
        "audit_head_after": "2" * 64 if changed else "1" * 64,
        "receipt_sha256": "3" * 64 if changed else None,
        "target_sha256": "4" * 64 if changed else None,
    }


def _native_receipts(
    host: str,
    scenario: str,
    identities: list[str],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    root = identities[0]

    def receipt(
        *,
        requested: str,
        observed: str,
        turn_index: int,
        relation: str,
        transport: str,
        observation_kind: str,
        request_seam: str | None = None,
    ) -> dict[str, Any]:
        current = identities[turn_index]
        usage_observed = (
            host == "codex"
            and requested in {"thread/start", "thread/resume", "thread/fork"}
        ) or (
            host == "opencode"
            and (requested.startswith("cli.run") or requested == "session.messages")
        )
        return pass13_evidence.native_lifecycle_receipt(
            semantic_task_family=scenario,
            transport=transport,
            request_seam=requested if request_seam is None else request_seam,
            requested_operation=requested,
            sanitized_request={"operation": requested},
            observation_kind=observation_kind,
            methods_observed=[observed],
            sanitized_observation={"observed": observed},
            current_identity=current,
            parent_identity=(
                None
                if host == "opencode" and requested == "session.get"
                else root if relation != "new" else None
            ),
            root_identity=root,
            relation=relation,
            actual_provider_usage=(turns[turn_index]["usage"] if usage_observed else None),
        )

    if host == "codex":
        rows = [
            receipt(
                requested="thread/start",
                observed="thread/start",
                turn_index=0,
                relation="new",
                transport="codex_app_server_jsonrpc",
                observation_kind="native_response",
            )
        ]
        if scenario == "resume_fork":
            rows.extend(
                [
                    receipt(
                        requested="thread/resume",
                        observed="thread/resume",
                        turn_index=1,
                        relation="resume",
                        transport="codex_app_server_jsonrpc",
                        observation_kind="native_response",
                    ),
                    receipt(
                        requested="thread/fork",
                        observed="thread/fork",
                        turn_index=2,
                        relation="fork",
                        transport="codex_app_server_jsonrpc",
                        observation_kind="native_response",
                    ),
                ]
            )
        elif scenario == "compaction_forget":
            rows.extend(
                receipt(
                    requested="thread/compact/start",
                    observed=observed,
                    turn_index=1,
                    relation="same_session",
                    transport="codex_app_server_jsonrpc",
                    observation_kind=(
                        "native_response"
                        if observed == "thread/compact/start"
                        else "native_event"
                    ),
                    request_seam=(
                        "thread/compact/start"
                        if observed == "thread/compact/start"
                        else "thread/compact/start notifications"
                    ),
                )
                for observed in ("thread/compact/start", "item/started", "item/completed")
            )
        return rows

    requested_turns = {
        "cold_start": ("cli.run",),
        "resume_fork": ("cli.run", "cli.run.session", "cli.run.fork"),
        "compaction_forget": ("cli.run", "cli.run.session", "cli.run.session"),
    }[scenario]
    relations = {
        "cold_start": ("new",),
        "resume_fork": ("new", "resume", "fork"),
        "compaction_forget": ("new", "resume", "resume"),
    }[scenario]
    rows = []
    for index, requested in enumerate(requested_turns):
        rows.extend(
            [
                receipt(
                    requested=requested,
                    observed="cli.run.json",
                    turn_index=index,
                    relation=relations[index],
                    transport="opencode_cli",
                    observation_kind="cli_json_record",
                    request_seam="opencode run --format json",
                ),
                receipt(
                    requested="session.get",
                    observed="session.get",
                    turn_index=index,
                    relation="same_session",
                    transport="opencode_loopback_http",
                    observation_kind="native_response",
                    request_seam="GET session/:sessionID",
                ),
            ]
        )
        if scenario == "compaction_forget" and index == 0:
            rows.extend(
                receipt(
                    requested=operation,
                    observed=operation,
                    turn_index=0,
                    relation="same_session",
                    transport="opencode_loopback_http",
                    observation_kind="native_response",
                    request_seam=(
                        "POST session/:sessionID/summarize"
                        if operation == "session.summarize"
                        else "GET session/:sessionID/message"
                    ),
                )
                for operation in ("session.summarize", "session.messages")
            )
    return rows


def _run(host: str, index: int, scenario: str) -> dict[str, Any]:
    codex_methods = {
        "cold_start": ["thread/start"],
        "resume_fork": ["thread/start", "thread/resume", "thread/fork"],
        "compaction_forget": [
            "thread/start",
            "thread/compact/start",
            "item/started",
            "item/completed",
        ],
    }[scenario]
    opencode_methods = {
        "cold_start": ["cli.run.json", "session.get"],
        "resume_fork": ["cli.run.json", "session.get"],
        "compaction_forget": [
            "cli.run.json",
            "session.get",
            "session.summarize",
            "session.messages",
        ],
    }[scenario]
    methods = codex_methods if host == "codex" else opencode_methods
    turn_methods = (
        {
            "cold_start": ["thread/start"],
            "resume_fork": ["thread/start", "thread/resume", "thread/fork"],
            "compaction_forget": [
                "thread/start",
                "thread/compact/start",
                "thread/compact/start",
            ],
        }[scenario]
        if host == "codex"
        else {
            "cold_start": ["cli.run"],
            "resume_fork": ["cli.run", "cli.run.session", "cli.run.fork"],
            "compaction_forget": ["cli.run", "cli.run.session", "cli.run.session"],
        }[scenario]
    )
    root_identity = f"thread:{host}:{scenario}:root"
    identities = (
        [root_identity, root_identity, f"thread:{host}:{scenario}:fork"]
        if scenario == "resume_fork"
        else [root_identity] * len(turn_methods)
    )
    turns = []
    for turn_index, method in enumerate(turn_methods, 1):
        turns.append(
            {
                "status": "passed",
                "lifecycle_method": method,
                "thread_id_sha256": _digest(identities[turn_index - 1]),
                "turn_id_sha256": _digest(f"turn:{host}:{scenario}:{turn_index}"),
                "prompt_sha256": _digest(f"prompt:{scenario}"),
                "final_response_sha256": _digest(f"response:{scenario}:{turn_index}"),
                "final_response_bytes": 10,
                "host_elapsed_ms": 5,
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 2,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 4,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 14 if host == "codex" else 17,
                },
                "ledger_audit_head_before": "1" * 64,
                "ledger_audit_head_after": "1" * 64,
                "ledger_unchanged": True,
                "safe_read": {
                    "call_count": 1,
                    "first_call_valid": True,
                    "bounded_retry_used": False,
                    "safe_read_operations": ["context"],
                    "provider_payloads": [
                        {
                            "operation": "context",
                            "provider_bytes": 100,
                            "provider_sha256": _digest(
                                f"provider:{host}:{scenario}:{turn_index}"
                            ),
                            "structured_output_bytes": 200,
                            "structured_output_sha256": _digest(
                                f"structured:{host}:{scenario}:{turn_index}"
                            ),
                            "delivery_match": True,
                            "write_performed": False,
                            "statement_count": 1,
                            "gap_count": 1 if scenario == "compaction_forget" else 0,
                            "gap_codes": ["missing_checkpoint"]
                            if scenario == "compaction_forget"
                            else [],
                            "relevant_chars": 80,
                            "context_chars": 100,
                            "relevant_chars_context_chars": 0.8,
                            "evidence_count": 0,
                            "duplicate_evidence_count": 0,
                            "duplicate_evidence_rate": None,
                        }
                    ],
                },
                "sanitized_events": {
                    "name": f"run-{index}-{turn_index}.jsonl",
                    "bytes": 10,
                    "sha256": _digest(f"events:{host}:{scenario}:{turn_index}"),
                },
            }
        )
    metrics = {
        "first_correct_action": True,
        "decision_preservation": True,
        "wrong_state_admission": 0,
        "stale_state_rejected": True,
        "forgotten_state_admission": 0 if scenario == "compaction_forget" else None,
        "gap_observed": True if scenario == "compaction_forget" else None,
        "projection_state_correct": True,
        "retention_wording_correct": True,
        "provider_boundary_correct": True,
        "evidence_sha256": _digest(f"evidence:{host}:{scenario}"),
    }
    boundaries = [_boundary("seed_checkpoint")]
    if scenario == "compaction_forget":
        boundaries.append(_boundary("forget"))
    native_receipts = _native_receipts(host, scenario, identities, turns)
    return {
        "run_index": index,
        "scenario": scenario,
        "task_family": scenario,
        "status": "passed",
        "failure_codes": [],
        "task_sha256": _digest(f"task:{scenario}"),
        "new_thread": True,
        "methods_observed": methods,
        "native_receipts": native_receipts,
        "turns": turns,
        "metrics": metrics,
        "mutation_boundaries": boundaries,
    }


def _report(host: str) -> dict[str, Any]:
    runs = [_run(host, index, scenario) for index, scenario in enumerate(scorer.SCENARIOS, 1)]
    for run in runs:
        run["metrics"]["evidence_sha256"] = pass13_evidence.metric_evidence_sha256(run)
    turns = [turn for run in runs for turn in run["turns"]]
    methods = sorted({method for run in runs for method in run["methods_observed"]})
    native_receipts = [receipt for run in runs for receipt in run["native_receipts"]]
    from deeplaw.knowledge_mcp_server import knowledge_tool_definition

    input_schema = knowledge_tool_definition(autonomous=True).inputSchema
    tool_schema = pass13_evidence.knowledge_support_tool_schema_receipt(
        [{"name": "knowledge_support", "inputSchema": input_schema}]
    )
    isolation = {
        "profile_kind": "temporary_closed",
        "home_isolated": True,
        "codex_home_isolated": host == "codex",
        "xdg_config_home_isolated": True,
        "xdg_data_home_isolated": True,
        "ambient_host_state_inherited": False,
        "ambient_plugins_inherited": False,
        "ambient_apps_inherited": False,
        "ambient_hooks_inherited": False,
        "secret_values_retained": False,
        "auth_class": "chatgpt_login" if host == "codex" else "deepseek_api_key",
    }
    report = {
        "schema_version": scorer.QUALIFICATION_SCHEMA_VERSION,
        "execution_mode": "qualification",
        "qualification_status": "passed",
        "evidence_class": "qualification_holdout",
        "host": host,
        "status": "executed",
        "package_version": "0.12.0",
        "release_ready": False,
        "claim_eligible": False,
        "binding": {
            "commit": CANDIDATE_COMMIT,
            "tree": CANDIDATE_TREE,
            "worktree_clean": True,
            "wheel_name": "deeplaw-0.12.0-py3-none-any.whl",
            "wheel_sha256": CANDIDATE_WHEEL_SHA256,
            "wheel_bytes": 100,
            "runtime_executable_sha256": "4" * 64,
            "import_path_class": "isolated_site_packages",
            "contract_digests": {
                "host-continuity-qualification.v1.schema.json": "5" * 64,
                "host-continuity-qualification.v2.schema.json": QUALIFICATION_CONTRACT_SHA256,
                "host-continuity-development-diagnostic.v1.schema.json": "d" * 64,
                "knowledge-support.output.v6.schema.json": "6" * 64,
                "provider-knowledge-capsule.v2.schema.json": "7" * 64,
            },
        },
        "environment": {
            "operating_system": "Darwin",
            "architecture": "arm64",
            "python_version": "3.13.7",
            "isolation": isolation,
        },
        "host_attestation": {
            "binary_name": host,
            "binary_sha256": "8" * 64,
            "version": scorer.EXPECTED_HOST[host]["tool_version"],
            "model": scorer.EXPECTED_HOST[host]["model"],
            "reasoning_effort": "max",
            "authentication": {
                "status": "existing_login_confirmed" if host == "codex" else "provider_available",
                "source": "existing_codex_login" if host == "codex" else "process_environment",
                "auth_file_read": False,
                "checked": True,
                "raw_sha256": "9" * 64,
                "raw_bytes": 10,
            },
            "model_inventory": {
                "checked": True,
                "selected_present": True,
                "raw_sha256": "a" * 64,
                "raw_bytes": 10,
            },
            "mcp_inventory": {
                "checked": True,
                "selected_present": True,
                "raw_sha256": "b" * 64,
                "raw_bytes": 10,
            },
            **(
                {
                    "availability": {
                        "status": "available",
                        "raw_sha256": "c" * 64,
                        "raw_bytes": 10,
                        "elapsed_ms": 5,
                        "input_tokens": 2,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 1,
                        "reasoning_output_tokens": 0,
                        "total_tokens": 3,
                    }
                }
                if host == "opencode"
                else {}
            ),
        },
        "tool_schema": tool_schema,
        "lifecycle": {
            "host_owns_threads": True,
            "common_task_families": list(scorer.SCENARIOS),
            "transport_seams": sorted(
                {receipt["transport"] for receipt in native_receipts}
            ),
            "requested_operations": sorted(
                {receipt["requested_operation"] for receipt in native_receipts}
            ),
            "methods_observed": methods,
            "deeplaw_session_store_created": False,
        },
        "security": {
            "mcp_child_closed_environment": True,
            "only_knowledge_support_enabled": True,
            "absolute_path_leak": False,
            "secret_leak": False,
            "raw_transcript_retained": False,
            "hidden_reasoning_retained": False,
            "authentication_material_retained": False,
            "cleanup_complete": True,
        },
        "runs": runs,
        "aggregate": {
            "passed_runs": 3,
            "failed_runs": 0,
            "first_call_valid_runs": 3,
            "bounded_retry_runs": 0,
            "provider_bytes": sum(
                payload["provider_bytes"]
                for run in runs
                for turn in run["turns"]
                for payload in turn["safe_read"]["provider_payloads"]
            ),
            "input_tokens": sum(turn["usage"]["input_tokens"] for turn in turns),
            "cached_input_tokens": sum(turn["usage"]["cached_input_tokens"] for turn in turns),
            "cache_write_input_tokens": 0,
            "output_tokens": sum(turn["usage"]["output_tokens"] for turn in turns),
            "reasoning_output_tokens": sum(
                turn["usage"]["reasoning_output_tokens"] for turn in turns
            ),
            "total_tokens": sum(turn["usage"]["total_tokens"] for turn in turns),
            "host_elapsed_ms": sum(turn["host_elapsed_ms"] for turn in turns),
        },
        "not_executed": ["Human Gold authenticity", "release claim"],
    }
    return report


def _reviews(
    reports: dict[str, dict[str, Any]], gold_path: Path
) -> dict[tuple[str, str], dict[str, Any]]:
    gold = _gold()
    gold_sha = hashlib.sha256(gold_path.read_bytes()).hexdigest()
    result = {}
    for host, report in reports.items():
        for case in _task_value()["task_cases"]:
            run = next(run for run in report["runs"] if run["scenario"] == case["scenario"])
            criterion_ids = case["required_human_review"]["criterion_ids"]
            result[(host, case["task_case"])] = {
                "schema_version": scorer.HUMAN_REVIEW_SCHEMA_VERSION,
                "review_id": "continuityreview_" + hashlib.sha256(
                    f"{host}:{case['task_case']}".encode()
                ).hexdigest()[:24],
                "status": "independent_blind_review_complete",
                "reviewed_at": "2026-08-13T07:04:31Z",
                "gold_id": gold["gold_id"],
                "gold_sha256": gold_sha,
                "case_id": case["task_case"],
                "anonymized_candidate_sha256": run["metrics"]["evidence_sha256"],
                "candidate_wheel_sha256": CANDIDATE_WHEEL_SHA256,
                "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
                "blind_label": "blindcase_"
                + hashlib.sha256(
                    f"randomized:{host}:{case['task_case']}".encode()
                ).hexdigest()[:24],
                "reviewer_id": f"human-reviewer-{host}",
                "reviewer_is_human": True,
                "independent": True,
                "blind_to_host": True,
                "blind_to_tool_model": True,
                "blind_to_other_runs": True,
                "order_randomized": True,
                "decision": "pass",
                "criterion_results": {criterion: True for criterion in criterion_ids},
                "failure_case": False,
                "hard_failure_ids": [],
                "claim_eligible": False,
            }
    return result


@pytest.fixture
def fixture_bundle(tmp_path: Path) -> FixtureBundle:
    gold_path = _write_json(tmp_path / "external-human-gold.json", _gold())
    reports = {host: _report(host) for host in scorer.HOSTS}
    reviews = _reviews(reports, gold_path)
    return gold_path, reports["codex"], reports["opencode"], reviews


def test_external_gold_schema_and_receipt_are_bounded_and_not_authenticity_claims(
    tmp_path: Path,
) -> None:
    gold_path = _write_json(tmp_path / "external-human-gold.json", _gold())
    gold_schema = json.loads(GOLD_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(gold_schema)
    Draft202012Validator(gold_schema, format_checker=FormatChecker()).validate(_gold())
    loaded, receipt = scorer.load_human_gold_with_receipt(
        gold_path, candidate_wheel_path=CANDIDATE_WHEEL
    )
    assert len(loaded["task_cases"]) == 3
    assert receipt["case_count"] == 3
    assert receipt["structural_validation_only"] is True
    assert receipt["authenticity_proven"] is False
    assert str(tmp_path) not in json.dumps(receipt)


def test_gold_must_be_frozen_after_the_exact_task_cases(tmp_path: Path) -> None:
    gold = _gold()
    gold["frozen_at"] = _task_value()["frozen_at"]
    path = _write_json(tmp_path / "not-later-gold.json", gold)
    with pytest.raises(scorer.HumanGoldValidationError, match="after the exact task-case"):
        scorer.load_human_gold(path, candidate_wheel_path=CANDIDATE_WHEEL)


def test_gold_repository_internal_and_symlink_inputs_fail_closed(tmp_path: Path) -> None:
    gold = _gold()
    inside = _write_json(tmp_path / "inside.json", gold)
    with pytest.raises(scorer.HumanGoldValidationError, match="repository-external"):
        scorer.load_human_gold(
            inside, repository=tmp_path, candidate_wheel_path=CANDIDATE_WHEEL
        )
    target = _write_json(tmp_path / "target.json", gold)
    link = tmp_path / "gold-link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises(scorer.HumanGoldValidationError, match="regular file"):
        scorer.load_human_gold(
            link, repository=REPOSITORY, candidate_wheel_path=CANDIDATE_WHEEL
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_outputs_seen_before_freeze", True),
        ("development_tuning_material", True),
        ("candidate_visible_when_frozen", True),
        ("author_is_human", False),
        ("independent", False),
        ("claim_eligible", True),
    ],
)
def test_gold_provenance_flags_cannot_be_relabeled_as_external(
    tmp_path: Path, field: str, value: Any
) -> None:
    gold = _gold()
    gold[field] = value
    path = _write_json(tmp_path / "bad-gold.json", gold)
    with pytest.raises(scorer.HumanGoldValidationError):
        scorer.load_human_gold(path, candidate_wheel_path=CANDIDATE_WHEEL)


@pytest.mark.parametrize(
    "field",
    (
        "candidate_commit",
        "candidate_tree",
        "candidate_wheel_sha256",
        "qualification_contract_sha256",
    ),
)
def test_gold_must_bind_exact_candidate_and_receipt_contract(
    tmp_path: Path, field: str
) -> None:
    gold = _gold()
    gold[field] = "0" * len(str(gold[field]))
    path = _write_json(tmp_path / "wrong-binding-gold.json", gold)
    with pytest.raises(
        scorer.HumanGoldValidationError,
        match="does not bind the exact candidate",
    ):
        scorer.load_human_gold(path, candidate_wheel_path=CANDIDATE_WHEEL)


def test_six_schema_valid_run_scores_stay_release_closed(
    fixture_bundle: FixtureBundle,
) -> None:
    gold_path, codex, opencode, reviews = fixture_bundle
    result = scorer.score_reports(
        {"codex": codex, "opencode": opencode},
        reviews,
        gold_path=gold_path,
        candidate_wheel_path=CANDIDATE_WHEEL,
    )
    assert result["aggregate"]["structural_run_set_passed"] is True
    assert result["aggregate"]["authenticity_proven"] is False
    assert result["aggregate"]["aggregate_eligible"] is False
    assert result["aggregate"]["release_ready"] is False
    assert result["aggregate"]["claim_eligible"] is False
    schema = json.loads(SCORE_SCHEMA.read_text(encoding="utf-8"))
    review_schema = json.loads(BLIND_REVIEW_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(review_schema)
    for review in reviews.values():
        Draft202012Validator(
            review_schema, format_checker=FormatChecker()
        ).validate(review)
    for score in result["scores"]:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(score)
        assert score["status"] == "passed"
        assert score["release_ready"] is False
        assert score["claim_eligible"] is False
        assert "/" not in score["run_id"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report["runs"][0]["metrics"].update({"decision_preservation": None}),
        lambda report: report["runs"][0]["metrics"].update({"wrong_state_admission": 1}),
        lambda report: report["runs"][0]["metrics"].update({"stale_state_rejected": False}),
        lambda report: report["runs"][2]["metrics"].update({"forgotten_state_admission": 1}),
        lambda report: report["runs"][2]["metrics"].update({"gap_observed": False}),
        lambda report: report["security"].update({"secret_leak": True}),
        lambda report: report["security"].update({"absolute_path_leak": True}),
        lambda report: report["security"].update({"only_knowledge_support_enabled": False}),
        lambda report: report["runs"][0]["turns"][0].update({"ledger_unchanged": False}),
        lambda report: report["runs"][0]["turns"][0]["usage"].update(
            {"input_tokens": "unreported"}
        ),
    ],
)
def test_machine_and_security_threshold_misses_fail_closed(
    fixture_bundle: FixtureBundle,
    mutation: Any,
) -> None:
    gold_path, codex, opencode, reviews = fixture_bundle
    changed_codex = copy.deepcopy(codex)
    mutation(changed_codex)
    result = scorer.score_reports(
        {"codex": changed_codex, "opencode": opencode},
        reviews,
        gold_path=gold_path,
        candidate_wheel_path=CANDIDATE_WHEEL,
    )
    codex_scores = [score for score in result["scores"] if score["host"] == "codex"]
    assert any(score["status"] == "failed" for score in codex_scores)
    assert result["aggregate"]["aggregate_eligible"] is False


def test_review_gold_case_digest_reviewer_and_criterion_mismatch_fail_closed(
    fixture_bundle: FixtureBundle,
) -> None:
    gold_path, codex, opencode, reviews = fixture_bundle
    changed = copy.deepcopy(reviews)
    key = ("codex", "continuity_cold_new_v1")
    changed[key]["gold_sha256"] = "0" * 64
    changed[key]["case_id"] = "continuity_resume_fork_concurrent_worktree_v1"
    changed[key]["reviewer_id"] = _gold()["author_id"]
    changed[key]["criterion_results"]["authority_boundary"] = False
    changed[key]["decision"] = "fail"
    changed[key]["failure_case"] = True
    changed[key]["hard_failure_ids"] = ["wrong_authority"]
    result = scorer.score_reports(
        {"codex": codex, "opencode": opencode},
        changed,
        gold_path=gold_path,
        candidate_wheel_path=CANDIDATE_WHEEL,
    )
    score = next(
        item
        for item in result["scores"]
        if item["host"] == "codex" and item["scenario"] == "cold_start"
    )
    assert score["status"] == "failed"
    assert "gold_digest_mismatch" in score["hard_failures"]
    assert "review_case_mismatch" in score["hard_failures"]
    assert "reviewer_identity_not_independent" in score["hard_failures"]
    assert score["blind_review_refs"] is None


def test_blinding_time_and_six_unique_packet_receipts_fail_closed(
    fixture_bundle: FixtureBundle,
) -> None:
    gold_path, codex, opencode, reviews = fixture_bundle
    changed = copy.deepcopy(reviews)
    cold = ("codex", "continuity_cold_new_v1")
    resume = ("codex", "continuity_resume_fork_concurrent_worktree_v1")
    changed[cold]["blind_to_host"] = False
    changed[cold]["reviewed_at"] = "2026-08-13T05:04:31Z"
    changed[resume]["blind_label"] = changed[cold]["blind_label"]
    result = scorer.score_reports(
        {"codex": codex, "opencode": opencode},
        changed,
        gold_path=gold_path,
        candidate_wheel_path=CANDIDATE_WHEEL,
    )
    assert result["aggregate"]["aggregate_eligible"] is False
    assert "blind_review_set_invalid" in result["aggregate"]["hard_failures"]
    cold_score = next(
        score
        for score in result["scores"]
        if score["host"] == "codex" and score["scenario"] == "cold_start"
    )
    assert "blind_review_invalid" in cold_score["hard_failures"]
    assert "review_precedes_gold_freeze" in cold_score["hard_failures"]


def test_model_tool_payload_and_candidate_binding_mismatches_fail_closed(
    fixture_bundle: FixtureBundle,
) -> None:
    gold_path, codex, opencode, reviews = fixture_bundle
    changed_codex = copy.deepcopy(codex)
    changed_codex["host_attestation"]["model"] = "wrong-model"
    changed_codex["host_attestation"]["version"] = "wrong-tool"
    changed_codex["runs"][0]["turns"][0]["safe_read"]["provider_payloads"][0][
        "provider_bytes"
    ] = 65_537
    changed_codex["binding"]["commit"] = "9" * 40
    result = scorer.score_reports(
        {"codex": changed_codex, "opencode": opencode},
        reviews,
        gold_path=gold_path,
        candidate_wheel_path=CANDIDATE_WHEEL,
    )
    for score in result["scores"]:
        if score["host"] == "codex":
            assert score["status"] == "failed"
            assert "model_substitution" in score["hard_failures"]
            assert "tool_version_mismatch" in score["hard_failures"]
            if score["scenario"] == "cold_start":
                assert "provider_payload_overflow" in score["hard_failures"]
            assert "candidate_binding_mismatch" in score["hard_failures"]


def test_native_request_response_correlation_cannot_be_relabelled(
    fixture_bundle: FixtureBundle,
) -> None:
    _gold_path, codex, _opencode, _reviews = fixture_bundle
    changed = copy.deepcopy(codex)
    run = changed["runs"][2]
    first = run["native_receipts"][0]
    event = next(
        receipt
        for receipt in run["native_receipts"]
        if receipt["methods_observed"] == ["item/started"]
    )
    first["methods_observed"], event["methods_observed"] = (
        event["methods_observed"],
        first["methods_observed"],
    )
    run["methods_observed"] = sorted(
        {
            method
            for receipt in run["native_receipts"]
            for method in receipt["methods_observed"]
        }
    )
    run["metrics"]["evidence_sha256"] = pass13_evidence.metric_evidence_sha256(run)
    with pytest.raises(
        pass13_evidence.EvidenceValidationError,
        match="request and observation are not correlated",
    ):
        pass13_evidence.validate_host_report_consistency(changed)


def test_opencode_each_cli_session_transition_requires_its_native_get(
    fixture_bundle: FixtureBundle,
) -> None:
    _gold_path, _codex, opencode, _reviews = fixture_bundle
    changed = copy.deepcopy(opencode)
    run = changed["runs"][1]
    del run["native_receipts"][3]
    run["methods_observed"] = sorted(
        {
            method
            for receipt in run["native_receipts"]
            for method in receipt["methods_observed"]
        }
    )
    run["metrics"]["evidence_sha256"] = pass13_evidence.metric_evidence_sha256(run)
    with pytest.raises(
        pass13_evidence.EvidenceValidationError,
        match="exact native receipt sequence",
    ):
        pass13_evidence.validate_host_report_consistency(changed)


def test_non_usage_native_response_cannot_claim_inferred_zero_tokens(
    fixture_bundle: FixtureBundle,
) -> None:
    _gold_path, _codex, opencode, _reviews = fixture_bundle
    changed = copy.deepcopy(opencode)
    run = changed["runs"][0]
    session_get = next(
        receipt
        for receipt in run["native_receipts"]
        if receipt["requested_operation"] == "session.get"
    )
    session_get["actual_provider_usage"] = {
        field: 0
        for field in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "total_tokens",
        )
    }
    run["metrics"]["evidence_sha256"] = pass13_evidence.metric_evidence_sha256(run)
    with pytest.raises(
        pass13_evidence.EvidenceValidationError,
        match="non-usage native observation",
    ):
        pass13_evidence.validate_host_report_consistency(changed)


def test_codex_compaction_context_snapshot_cannot_claim_provider_usage(
    fixture_bundle: FixtureBundle,
) -> None:
    _gold_path, codex, _opencode, _reviews = fixture_bundle
    changed = copy.deepcopy(codex)
    run = changed["runs"][2]
    completed = next(
        receipt
        for receipt in run["native_receipts"]
        if receipt["methods_observed"] == ["item/completed"]
    )
    completed["actual_provider_usage"] = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 6735,
    }
    run["metrics"]["evidence_sha256"] = pass13_evidence.metric_evidence_sha256(run)
    with pytest.raises(
        pass13_evidence.EvidenceValidationError,
        match="non-usage native observation claimed Provider accounting",
    ):
        pass13_evidence.validate_host_report_consistency(changed)

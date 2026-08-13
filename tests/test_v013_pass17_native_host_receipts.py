from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.evaluator import score_pass16_host_continuity as scorer
from benchmarks.hosts import pass13_evidence, pass17_development_diagnostic
from benchmarks.hosts import run_pass13_codex_continuity_qualification as codex_runner
from benchmarks.hosts import run_pass13_opencode_continuity_qualification as opencode_runner
from benchmarks.hosts.pass13_orchestrator import QualificationOrchestrator
from deeplaw.knowledge_mcp_server import knowledge_tool_definition

REPOSITORY = Path(__file__).resolve().parents[1]

CODEX_NATIVE_VOCABULARY = {
    "thread/start",
    "thread/resume",
    "thread/fork",
    "thread/compact/start",
    "item/started",
    "item/completed",
}


def test_development_fixture_is_source_free_and_has_no_qualification_labels() -> None:
    fixture = pass17_development_diagnostic.load_fixture()
    assert fixture["status"] == "development_fixture"
    assert fixture["source_free"] is True
    assert fixture["qualification_labels_present"] is False
    encoded = json.dumps(fixture, ensure_ascii=False, sort_keys=True).casefold()
    for forbidden in ("human gold", "qualification_holdout", "blind_score", "expected_score"):
        assert forbidden not in encoded


def test_opencode_runner_and_shared_validator_use_native_not_codex_vocabulary() -> None:
    runner_source = inspect.getsource(opencode_runner._run_one_scenario)
    fabricated = sorted(
        method for method in CODEX_NATIVE_VOCABULARY if f'"{method}"' in runner_source
    )
    assert fabricated == []

    codex = pass13_evidence.native_lifecycle_requirements("codex")
    opencode = pass13_evidence.native_lifecycle_requirements("opencode")
    assert set().union(*codex.values()) == CODEX_NATIVE_VOCABULARY
    assert set().union(*opencode.values()).isdisjoint(CODEX_NATIVE_VOCABULARY)


def test_single_diagnostic_covers_each_native_lifecycle_seam() -> None:
    codex = pass13_evidence.native_lifecycle_requirements("codex")
    opencode = pass13_evidence.native_lifecycle_requirements("opencode")
    assert codex["development_diagnostic"] == CODEX_NATIVE_VOCABULARY
    assert opencode["development_diagnostic"] == {
        "cli.run.json",
        "session.get",
        "session.summarize",
        "session.messages",
    }


def test_codex_context_prompt_uses_the_complete_public_v6_call_shape() -> None:
    binding = codex_runner._make_binding("cold_start")
    arguments = codex_runner._context_call_arguments(
        task="source_free_development_task",
        binding=binding,
    )
    assert arguments == {
        "operation": "context",
        "task": "source_free_development_task",
        "confirm_no_case_data": True,
        "query_plan_version": "6",
        "task_binding": binding,
    }
    prompt = codex_runner._prompt("cold_start", binding)
    expected = {**arguments, "task": "continuity_cold_new_v1"}
    assert pass13_evidence.canonical_json(expected) in prompt


def test_diagnostic_mode_is_reachable_before_external_human_gold() -> None:
    protocol = (REPOSITORY / "docs/V0_13_QUALIFICATION_PROTOCOL.md").read_text(
        encoding="utf-8"
    )
    assert "diagnostic first" in protocol

    codex_args = codex_runner.build_parser().parse_args(
        [
            "--mode",
            "diagnostic",
            "--candidate-wheel",
            "candidate.whl",
            "--deeplaw-executable",
            "deeplaw",
            "--output-dir",
            "diagnostic-output",
            "--profile-root",
            "codex-profile",
        ]
    )
    opencode_args = opencode_runner._parse_args(
        [
            "--mode",
            "diagnostic",
            "--candidate-wheel",
            "candidate.whl",
            "--deeplaw-executable",
            "deeplaw",
            "--output-dir",
            "diagnostic-output",
            "--opencode-binary",
            "opencode",
            "--dotenv",
            "owner.env",
        ]
    )
    assert codex_args.mode == "diagnostic"
    assert opencode_args.mode == "diagnostic"
    assert codex_args.human_gold is None
    assert opencode_args.human_gold is None


@pytest.mark.parametrize("host", ("codex", "opencode"))
def test_diagnostic_reaches_candidate_preparation_without_gold(
    host: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ReachedCandidatePreparation(RuntimeError):
        pass

    def reached(*args: object, **kwargs: object) -> object:
        raise ReachedCandidatePreparation

    monkeypatch.setattr(QualificationOrchestrator, "prepare_candidate", reached)
    if host == "codex":
        profile = tmp_path / "codex-profile"
        profile.mkdir()
        with pytest.raises(ReachedCandidatePreparation):
            codex_runner.execute(
                candidate_wheel=tmp_path / "candidate.whl",
                deeplaw_executable=tmp_path / "deeplaw",
                output_dir=tmp_path / "output",
                profile_root=profile,
                human_gold_path=None,
                mode="diagnostic",
            )
    else:
        root = tmp_path / "opencode-root"
        root.mkdir()
        with pytest.raises(ReachedCandidatePreparation):
            opencode_runner._execute_qualification_body(
                candidate_wheel=tmp_path / "candidate.whl",
                deeplaw_executable=tmp_path / "deeplaw",
                output_dir=tmp_path / "output",
                opencode_binary=tmp_path / "opencode",
                dotenv=tmp_path / "owner.env",
                human_gold_path=None,
                root=root,
                mode="diagnostic",
            )


def test_codex_diagnostic_inherits_existing_login_but_keeps_other_roots_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ambient_home = tmp_path / "ambient-home"
    ambient_codex = tmp_path / "ambient-codex"
    ambient_home.mkdir()
    ambient_codex.mkdir()
    monkeypatch.setenv("HOME", str(ambient_home))
    monkeypatch.setenv("CODEX_HOME", str(ambient_codex))
    monkeypatch.setenv("OPENAI_API_KEY", "forbidden-api-key")
    profile = tmp_path / "diagnostic-profile"

    environment = codex_runner._host_environment(
        Path("/opt/codex"),
        profile,
        inherit_existing_login=True,
    )

    assert environment["HOME"] == str(ambient_home.resolve())
    assert environment["CODEX_HOME"] == str(ambient_codex.resolve())
    assert environment["XDG_CONFIG_HOME"] == str(profile / "xdg-config")
    assert environment["XDG_DATA_HOME"] == str(profile / "xdg-data")
    assert "OPENAI_API_KEY" not in environment
    assert codex_runner._isolation_receipt(
        profile,
        environment,
        inherit_existing_login=True,
    ) == {
        "profile_kind": "temporary_closed_with_existing_login",
        "home_isolated": False,
        "codex_home_isolated": False,
        "xdg_config_home_isolated": True,
        "xdg_data_home_isolated": True,
        "ambient_host_state_inherited": True,
        "ambient_plugins_inherited": False,
        "ambient_apps_inherited": False,
        "ambient_hooks_inherited": False,
        "secret_values_retained": False,
        "auth_class": "chatgpt_login",
    }


def test_v2_allows_existing_login_only_for_codex_diagnostic(tmp_path: Path) -> None:
    report = _failed_diagnostic_report(tmp_path)
    report["environment"]["isolation"] = {
        "profile_kind": "temporary_closed_with_existing_login",
        "home_isolated": False,
        "codex_home_isolated": False,
        "xdg_config_home_isolated": True,
        "xdg_data_home_isolated": True,
        "ambient_host_state_inherited": True,
        "ambient_plugins_inherited": False,
        "ambient_apps_inherited": False,
        "ambient_hooks_inherited": False,
        "secret_values_retained": False,
        "auth_class": "chatgpt_login",
    }
    schema = json.loads(
        (REPOSITORY / "contracts/host-continuity-qualification.v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(report)) == []

    report["host"] = "opencode"
    errors = list(validator.iter_errors(report))
    assert any("isolation" in error.absolute_path for error in errors)

    report["host"] = "codex"
    report["execution_mode"] = "qualification"
    report["qualification_status"] = "partial"
    report["evidence_class"] = "qualification_holdout"
    errors = list(validator.iter_errors(report))
    assert any("isolation" in error.absolute_path for error in errors)


def test_codex_event_receipt_is_bound_before_metric_digest() -> None:
    run = codex_runner._placeholder_run(
        1,
        "development_diagnostic",
        task_family="development_diagnostic",
    )
    original = run["metrics"]["evidence_sha256"]
    run["scenario"] = "finalized_development_diagnostic"
    event_bytes = b'{"method":"thread/started"}\n'

    codex_runner._bind_run_event_receipt(
        run,
        event_name="codex-run-1-events.sanitized.jsonl",
        event_bytes=event_bytes,
    )

    assert run["turns"][0]["sanitized_events"] == {
        "name": "codex-run-1-events.sanitized.jsonl",
        "bytes": len(event_bytes),
        "sha256": hashlib.sha256(event_bytes).hexdigest(),
    }
    assert run["metrics"]["evidence_sha256"] != original
    assert run["metrics"]["evidence_sha256"] == pass13_evidence.metric_evidence_sha256(
        run
    )


def _failed_diagnostic_report(tmp_path: Path) -> dict[str, object]:
    orchestrator = QualificationOrchestrator(
        host="codex",
        repository=REPOSITORY,
        candidate_wheel=tmp_path / "candidate.whl",
        deeplaw_executable=tmp_path / "deeplaw",
        output_dir=tmp_path / "output",
        error_type=RuntimeError,
        execution_mode="diagnostic",
    )
    return orchestrator.build_report(
        binding={
            "commit": "1" * 40,
            "tree": "2" * 40,
            "worktree_clean": True,
            "wheel_name": "deeplaw-0.12.0-py3-none-any.whl",
            "wheel_sha256": "3" * 64,
            "wheel_bytes": 1,
            "runtime_executable_sha256": "4" * 64,
            "import_path_class": "isolated_site_packages",
            "contract_digests": {
                "host-continuity-qualification.v1.schema.json": "5" * 64,
                "host-continuity-qualification.v2.schema.json": "6" * 64,
                "host-continuity-development-diagnostic.v1.schema.json": "7" * 64,
            },
        },
        environment={
            "operating_system": "Darwin",
            "architecture": "arm64",
            "python_version": "3.11",
            "isolation": pass13_evidence.isolation_receipt(host="codex"),
        },
        host_attestation={
            **codex_runner._placeholder_attestation(),
            "version": codex_runner.CODEX_VERSION,
        },
        tool_schema=pass13_evidence.knowledge_support_tool_schema_receipt(
            [knowledge_tool_definition(autonomous=True)]
        ),
        runs=[
            codex_runner._placeholder_run(
                1,
                "development_diagnostic",
                task_family="development_diagnostic",
            )
        ],
        lifecycle={
            "host_owns_threads": True,
            "common_task_families": ["development_diagnostic"],
            "transport_seams": [],
            "requested_operations": [],
            "methods_observed": [],
            "deeplaw_session_store_created": False,
        },
        security=codex_runner._placeholder_security(),
        not_executed=["qualification", "Human Gold", "blind scoring"],
    )


def test_development_diagnostic_is_not_scorer_or_gate_eligible(tmp_path: Path) -> None:
    report = _failed_diagnostic_report(tmp_path)
    assert report["claim_eligible"] is False
    assert report["qualification_status"] == "not_applicable"
    assert report["evidence_class"] == "development_diagnostic"
    _loaded, failures = scorer._load_report_input(report, host="codex")
    assert "development_diagnostic_ineligible" in failures

    classification = json.loads(
        (REPOSITORY / "benchmarks/release/v013-gate-classification-v5.json").read_text(
            encoding="utf-8"
        )
    )
    gate = next(row for row in classification["gates"] if row["gate_id"] == "codex")
    assert gate["minimum_distinct_run_count"] == 3 > len(report["runs"])
    assert "development" not in gate["required_corpus_roles"]
    assert gate["allowed_applicability"] == ["applicable"]


def test_historical_v1_receipt_bytes_are_frozen_and_currently_invalidated() -> None:
    historical = REPOSITORY / "contracts/host-continuity-qualification.v1.schema.json"
    assert hashlib.sha256(historical.read_bytes()).hexdigest() == (
        "6208741ee2a438ece8a7424c05c6f9d1057ab81af0da5791fc4d4809ff9fa369"
    )
    protocol = (REPOSITORY / "docs/V0_13_QUALIFICATION_PROTOCOL.md").read_text(
        encoding="utf-8"
    )
    assert "invalidated-for-current-qualification" in protocol
    assert "deeplaw.host-continuity-qualification/v2" in protocol

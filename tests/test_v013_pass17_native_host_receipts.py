from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

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

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from benchmarks.evaluation.run_autonomy_safety import run_suite as run_autonomy
from benchmarks.evaluation.run_protocol import (
    EvaluationProtocolError,
    _verify_installed_candidate_wheel,
    run_protocol,
    verify_report_directory,
)
from benchmarks.evaluation.run_typed_compiler_quality import (
    run_suite as run_typed_compiler,
)
from benchmarks.quality.run_repository_gold import run_suite as run_repository_gold
from deeplaw.util import canonical_json, sha256_bytes, sha256_file


def _repository() -> Path:
    return Path(__file__).resolve().parents[1]


def _validator(name: str) -> Draft202012Validator:
    repository = _repository()
    resources = []
    for path in (repository / "contracts").glob("*.schema.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if "$id" in value:
            resources.append((value["$id"], Resource.from_contents(value)))
    schema = json.loads(
        (repository / "contracts" / name).read_text(encoding="utf-8")
    )
    return Draft202012Validator(
        schema,
        registry=Registry().with_resources(resources),
        format_checker=FormatChecker(),
    )


def test_protocol_and_public_suites_are_closed_and_time_frozen() -> None:
    repository = _repository()
    protocol = json.loads(
        (repository / "benchmarks/evaluation/protocol-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        sha256_file(repository / "benchmarks/evaluation/protocol-v1.json")
        == "470242a11c4f58a5975c1b576298fcf311bda95af1ebf8f0bfcd4529a4262c8c"
    )
    _validator("evaluation-protocol.v1.schema.json").validate(protocol)

    freeze = protocol["freeze_policy"]
    assert freeze["public_holdout"] is True
    assert freeze["labels_visible"] is True
    assert freeze["secret"] is False
    assert freeze["contamination_claim_eligible"] is False
    assert protocol["claim_policy"]["external_institution_certification_required"] is False
    assert protocol["claim_policy"][
        "comparative_superiority_requires_actual_named_baseline_reports"
    ] is True
    assert sum(protocol["scoring"]["weights"].values()) == pytest.approx(1.0)
    assert set(protocol["comparative_track"]["required_named_systems"]) == {
        "ragflow",
        "graphiti",
        "pageindex",
        "mem0",
        "openkb",
        "llm_wiki",
        "obsidian",
        "tolaria",
    }

    suite_contracts = {
        "repository-temporal-holdout-v1.json": "repository-gold-set.v2.schema.json",
        "autonomy-safety-v1.json": "autonomy-evaluation-suite.v1.schema.json",
        "typed-compiler-gold-v1.json": "typed-compiler-quality-suite.v1.schema.json",
    }
    for file_name, contract in suite_contracts.items():
        suite = json.loads(
            (repository / "benchmarks/evaluation" / file_name).read_text(
                encoding="utf-8"
            )
        )
        _validator(contract).validate(suite)

    protocol_v2 = json.loads(
        (repository / "benchmarks/evaluation/protocol-v2.json").read_text(
            encoding="utf-8"
        )
    )
    _validator("evaluation-protocol.v2.schema.json").validate(protocol_v2)
    assert protocol_v2["suites"]["repository_development"]["path"] == (
        "benchmarks/quality/repository-gold-development-v3.json"
    )
    assert (
        protocol_v2["claim_policy"][
            "quality_protocol_eligible_when_release_binding_and_all_gates_pass"
        ]
        is False
    )
    for suite in protocol_v2["suites"].values():
        assert suite["labels_visible"] is True
        assert suite["secret"] is False
        assert suite["external_holdout"] is False
        assert suite["independently_evaluated"] is False
        assert suite["claim_eligible"] is False
        assert suite["contamination_claim_eligible"] is False


def test_every_protocol_component_runs_actual_candidate_code() -> None:
    repository = _repository()
    holdout_path = (
        repository / "benchmarks/evaluation/repository-temporal-holdout-v1.json"
    )
    holdout = run_repository_gold(holdout_path, repository=repository)
    _validator("repository-gold-report.v2.schema.json").validate(holdout)
    assert holdout["development_fixture"] is False
    assert holdout["split"] == "public_time_frozen_holdout"
    assert holdout["quality_gate"]["passed"] is True
    assert holdout["competitive_claim_eligible"] is False

    autonomy = run_autonomy(
        repository / "benchmarks/evaluation/autonomy-safety-v1.json"
    )
    _validator("autonomy-evaluation-report.v1.schema.json").validate(autonomy)
    assert autonomy["quality_gate"]["passed"] is True
    assert all(item["passed"] for item in autonomy["case_results"])
    assert autonomy["metrics"]["unauthorized_mutation_success_rate"] == 0.0
    assert autonomy["metrics"]["authority_elevation_admission_rate"] == 0.0
    assert autonomy["metrics"]["restricted_disclosure_rate"] == 0.0

    typed = run_typed_compiler(
        repository / "benchmarks/evaluation/typed-compiler-gold-v1.json"
    )
    _validator("typed-compiler-quality-report.v1.schema.json").validate(typed)
    assert typed["quality_gate"]["passed"] is True
    assert typed["scorer_report"]["counts"]["gold_claims"] == 8
    assert typed["scorer_report"]["metrics"]["f1"] == 1.0
    assert typed["scorer_report"]["metrics"]["source_span_correctness"] == 1.0


def test_protocol_generates_and_verifies_complete_report_package(
    tmp_path: Path,
) -> None:
    repository = _repository()
    output = tmp_path / "evaluation"

    report = run_protocol(
        repository / "benchmarks/evaluation/protocol-v2.json",
        repository=repository,
        output_dir=output,
    )

    _validator("evaluation-report.v2.schema.json").validate(report)
    assert report["scoring"]["quality_gate_passed"] is True
    assert report["scoring"]["overall_score"] >= 0.85
    assert report["hard_failures"] == []
    assert report["claims"]["comparative_superiority_claim_eligible"] is False
    assert report["claims"]["quality_protocol_eligible"] is False
    assert report["candidate"]["artifact_type"] == "source_tree"
    assert report["freeze"]["secret"] is False
    assert report["development_fixture"]["visibility"] == "repository"
    assert report["development_fixture"]["external_holdout"] is False
    assert report["development_fixture"]["claim_eligible"] is False
    assert {
        "evaluation-report.json",
        "repository-development.json",
        "repository-temporal-holdout.json",
        "autonomy-safety.json",
        "typed-compiler-quality.json",
        "EVALUATION_REPORT.md",
        "SHA256SUMS",
    } == {path.name for path in output.iterdir()}
    verified = verify_report_directory(output, repository=repository)
    assert verified["report_sha256"] == report["report_sha256"]


def test_candidate_wheel_cannot_be_claimed_from_an_editable_source_runtime(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "deeplaw-0.10.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("deeplaw/__init__.py", '__version__ = "0.10.0"\n')
        archive.writestr(
            "deeplaw-0.10.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: deeplaw\nVersion: 0.10.0\n",
        )

    with pytest.raises(
        EvaluationProtocolError,
        match="runtime is not loaded from the candidate wheel",
    ):
        _verify_installed_candidate_wheel(wheel, version="0.10.0")


def test_report_verifier_rejects_component_tampering(tmp_path: Path) -> None:
    repository = _repository()
    output = tmp_path / "evaluation"
    run_protocol(
        repository / "benchmarks/evaluation/protocol-v2.json",
        repository=repository,
        output_dir=output,
    )
    component = output / "autonomy-safety.json"
    component.write_text(component.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(EvaluationProtocolError, match="artifact is invalid"):
        verify_report_directory(output, repository=repository)


def test_v1_protocol_rejects_current_source_drift(tmp_path: Path) -> None:
    repository = _repository()
    with pytest.raises(ValueError, match="source hash changed"):
        run_protocol(
            repository / "benchmarks/evaluation/protocol-v1.json",
            repository=repository,
            output_dir=tmp_path / "evaluation",
        )


def test_component_report_digests_are_canonical() -> None:
    repository = _repository()
    reports = [
        run_autonomy(repository / "benchmarks/evaluation/autonomy-safety-v1.json"),
        run_typed_compiler(
            repository / "benchmarks/evaluation/typed-compiler-gold-v1.json"
        ),
    ]
    for report in reports:
        body = {key: value for key, value in report.items() if key != "report_sha256"}
        assert report["report_sha256"] == sha256_bytes(
            canonical_json(body).encode("utf-8")
        )

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.quality.run_repository_gold import run_suite
from deeplaw.util import canonical_json, sha256_bytes, sha256_file


def test_repository_gold_v1_is_immutable_and_rejects_current_source_drift() -> None:
    repository = Path(__file__).resolve().parents[1]
    suite_path = repository / "benchmarks/quality/repository-gold-v1.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite_schema = json.loads(
        (repository / "contracts/repository-gold-set.v1.schema.json").read_text()
    )
    Draft202012Validator(
        suite_schema, format_checker=FormatChecker()
    ).validate(suite)
    assert (
        sha256_file(suite_path)
        == "ffce55aabd36738589abc979c903f830baaf18fb6943e218c430079d33de9e97"
    )
    with pytest.raises(ValueError, match="source hash changed"):
        run_suite(suite_path, repository=repository)


def test_repository_gold_v3_covers_all_required_domains_and_runs_offline() -> None:
    repository = Path(__file__).resolve().parents[1]
    suite_path = repository / "benchmarks/quality/repository-gold-development-v3.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite_schema = json.loads(
        (repository / "contracts/repository-gold-set.v3.schema.json").read_text()
    )
    report_schema = json.loads(
        (repository / "contracts/repository-gold-report.v3.schema.json").read_text()
    )

    Draft202012Validator(
        suite_schema, format_checker=FormatChecker()
    ).validate(suite)
    report = run_suite(suite_path, repository=repository)
    Draft202012Validator(report_schema).validate(report)

    assert report["categories"] == [
        "chinese",
        "english",
        "code",
        "legal",
        "long_document",
    ]
    assert report["document_count"] == 10
    assert report["case_count"] == 15
    assert report["models"]["network_policy"] == "offline"
    assert report["competitive_claim_eligible"] is False
    assert report["secret_held_out"] is False
    assert report["independently_evaluated"] is False
    assert report["visibility"] == "repository"
    assert report["labels_visible"] is True
    assert report["secret"] is False
    assert report["external_holdout"] is False
    assert report["claim_eligible"] is False
    assert report["contamination_claim_eligible"] is False
    assert report["quality_gate"]["passed"] is True
    for mode in ("lexical", "dense", "hybrid"):
        assert set(report["modes"][mode]["category_metrics"]) == set(report["categories"])
        assert report["modes"][mode]["case_count"] == 15
        assert report["modes"][mode]["forbidden_admission_count"] == 0
        assert report["quality_gate"]["mode_results"][mode]["passed"] is True
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    assert report["report_sha256"] == sha256_bytes(canonical_json(body).encode("utf-8"))


def test_repository_gold_rejects_source_drift(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    suite = json.loads(
        (repository / "benchmarks/quality/repository-gold-development-v3.json").read_text()
    )
    suite["documents"][0]["sha256"] = "0" * 64
    path = tmp_path / "drifted.json"
    path.write_text(json.dumps(suite), encoding="utf-8")

    with pytest.raises(ValueError, match="source hash changed"):
        run_suite(path, repository=repository)


def test_repository_gold_quality_gate_fails_closed(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    suite = json.loads(
        (repository / "benchmarks/quality/repository-gold-development-v3.json").read_text()
    )
    suite["quality_gate"]["hybrid"]["maximum_irrelevant_context_rate"] = 0.0
    path = tmp_path / "strict-gate.json"
    path.write_text(json.dumps(suite), encoding="utf-8")

    report = run_suite(path, repository=repository)

    assert report["quality_gate"]["passed"] is False
    assert report["quality_gate"]["mode_results"]["hybrid"]["checks"][
        "irrelevant_context_rate"
    ] is False


def test_autonomous_v2_protocol_keeps_external_closure_fail_closed() -> None:
    repository = Path(__file__).resolve().parents[1]
    protocol = json.loads(
        (repository / "benchmarks/external/autonomous-protocol-v2.json").read_text()
    )

    assert protocol["status"] == "engineering_ready_external_execution_pending"
    assert protocol["competitive_claim_eligible"] is False
    assert protocol["required_gold_domains"] == [
        "chinese",
        "english",
        "code",
        "legal",
        "long_document",
    ]
    assert protocol["required_real_hosts"] == ["codex", "claude_code", "opencode"]
    assert protocol["required_suites"] == [
        "task_context",
        "authority_temporal",
        "memory_lifecycle",
        "mutation_security",
        "legal_federation",
        "skill_lifecycle",
        "systems_cost",
    ]
    assert set(protocol["required_named_comparisons"]) == {
        "ragflow",
        "graphiti",
        "pageindex",
        "mem0",
        "openkb",
        "llm_wiki",
        "obsidian",
        "tolaria",
    }
    assert protocol["external_closure"]["secret_held_out_suites"] == 2
    assert protocol["external_closure"]["independent_evaluator_organizations"] == 2
    assert protocol["developer_owned_fixture_policy"]["claim_eligible"] is False
    assert {
        "authorized_mutation_success_rate",
        "idempotent_replay_accuracy",
        "update_compare_and_swap_accuracy",
        "expiry_accuracy",
        "feedback_provenance_coverage",
        "memory_poisoning_admission_rate",
        "no_grant_mutation_success_rate",
        "revoked_grant_mutation_success_rate",
        "wrong_scope_mutation_success_rate",
        "over_sensitivity_mutation_success_rate",
        "authority_elevation_admission_rate",
        "restricted_disclosure_rate",
        "private_legal_cross_scope_mutation_rate",
    } <= set(protocol["required_metrics"])

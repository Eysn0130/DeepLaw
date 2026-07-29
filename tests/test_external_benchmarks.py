from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import benchmarks.external.claim_gate as claim_gate_module
from benchmarks.external.adapters.longmemeval_v2_deeplaw import DeepLawMemory
from benchmarks.external.benchlib import (
    SCHEMA_CASE,
    SCHEMA_METRIC_CASE,
    SCHEMA_RUN,
    canonical_json,
    paired_comparison,
    score_metrics,
    score_retrieval,
)
from benchmarks.external.claim_gate import (
    _RUN_FIELDS,
    _expected_run_manifest,
    evaluate_claim,
)


def test_autonomous_protocol_is_preregistered_and_non_claiming() -> None:
    repository = Path(__file__).resolve().parents[1]
    protocol = json.loads(
        (repository / "benchmarks/external/autonomous-protocol-v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert protocol["schema_version"] == "deeplaw.autonomous-benchmark-protocol/v1"
    assert protocol["status"] == "preregistered_not_executed"
    assert protocol["competitive_claim_eligible"] is False
    assert {suite["id"] for suite in protocol["required_suites"]} == {
        "task-context",
        "authority-temporal",
        "memory-lifecycle",
        "mutation-security",
        "systems-cost",
    }
    assert protocol["statistics"] == {
        "held_out_required": True,
        "paired_confidence_intervals_required": True,
        "multiple_comparison_correction_required": True,
        "complete_failure_samples_required": True,
        "independent_evaluator_signatures_required_for_external_claim": True,
    }
    assert protocol["notes"][-1] == "This file defines a protocol and contains no benchmark result."


def _case(case_id: str, relevant_ids: list[str]) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_CASE,
        "case_id": case_id,
        "answerable": bool(relevant_ids),
        "relevant_ids": relevant_ids,
        "group": "test",
    }


def _run(
    case_id: str,
    retrieved_ids: list[str],
    *,
    success: bool,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_RUN,
        "case_id": case_id,
        "retrieved": [
            {"id": item_id, "chars": 100, "provenance_valid": True}
            for item_id in retrieved_ids
        ],
        "latency_ms": 5.0,
        "task_success": success,
    }


def _report(
    cases: list[dict[str, object]],
    runs: list[dict[str, object]],
    *,
    system_id: str,
) -> dict[str, object]:
    return score_retrieval(
        cases,
        runs,
        k=2,
        suite_id="external-test",
        system_id=system_id,
        cases_sha256="a" * 64,
        run_sha256="b" * 64,
        claim_eligible=True,
        claim_ineligibility_reason=None,
    )


def test_external_scorer_uses_exact_ids_and_counts_duplicate_context() -> None:
    report = _report(
        [_case("one", ["source-a", "source-b"]), _case("two", [])],
        [
            _run("one", ["source-a", "source-a"], success=True),
            _run("two", [], success=True),
        ],
        system_id="candidate",
    )

    assert report["case_count"] == 2
    assert report["per_case"][0]["recall_at_k"] == 0.5
    assert report["per_case"][0]["duplicate_count"] == 1
    assert report["per_case"][1]["hit_at_k"] == 1.0
    assert report["aggregate"]["provenance_coverage"] == 1.0


def test_paired_bootstrap_requires_case_identity_and_reports_superiority() -> None:
    cases = [_case(f"case-{index}", [f"source-{index}"]) for index in range(20)]
    candidate = _report(
        cases,
        [
            _run(f"case-{index}", [f"source-{index}"], success=True)
            for index in range(20)
        ],
        system_id="candidate",
    )
    baseline = _report(
        cases,
        [
            _run(f"case-{index}", ["irrelevant"], success=False)
            for index in range(20)
        ],
        system_id="baseline",
    )

    comparison = paired_comparison(
        candidate,
        baseline,
        metric="recall_at_k",
        direction="higher",
        samples=1_000,
        confidence=0.95,
        seed=7,
        noninferiority_margin=0.0,
        minimum_effect=0.01,
    )

    assert comparison["candidate_minus_baseline"] == 1.0
    assert comparison["ci_low"] == 1.0
    assert comparison["superior"] is True
    assert comparison["superiority_p_value"] < 0.05


def test_canonical_metric_reports_support_official_task_and_safety_scores() -> None:
    records = [
        {
            "schema_version": SCHEMA_METRIC_CASE,
            "case_id": f"case-{index}",
            "group": "official",
            "metrics": {
                "task_success": 1,
                "memory_poisoning_success_rate": 0,
            },
        }
        for index in range(4)
    ]

    report = score_metrics(
        records,
        suite_id="official-suite",
        system_id="candidate",
        input_sha256="a" * 64,
        cases_sha256="b" * 64,
        claim_eligible=True,
        claim_ineligibility_reason=None,
    )

    assert report["aggregate"]["task_success"] == 1.0
    assert report["aggregate"]["memory_poisoning_success_rate"] == 0.0
    assert report["cases_sha256"] == "b" * 64


def test_claim_gate_rejects_universal_language_and_pending_evidence() -> None:
    repository = Path(__file__).resolve().parents[1]
    protocol_path = repository / "benchmarks/external/protocol-v3.json"
    evidence_path = repository / "benchmarks/external/claim-evidence.pending.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    result = evaluate_claim(
        protocol,
        evidence,
        evidence_path=evidence_path,
        requested_claim="全面超过所有知识库",
    )

    assert result["passed"] is False
    assert result["allowed_claim"] is None
    assert result["unbounded_universal_claim_allowed"] is False
    assert "requested claim is unbounded and cannot be proven" in result["errors"]


def test_frozen_v3_protocol_covers_every_registered_suite_dimension_and_baseline() -> None:
    repository = Path(__file__).resolve().parents[1]
    protocol = json.loads(
        (repository / "benchmarks/external/protocol-v3.json").read_text(encoding="utf-8")
    )
    suites = protocol["suites"]
    dimensions = protocol["required_dimensions"]
    suite_ids = [suite["suite_id"] for suite in suites]
    baselines = {
        baseline
        for suite in suites
        for baseline in suite["named_baselines"]
    }

    assert protocol["claim_policy"]["unbounded_universal_claim_allowed"] is False
    assert protocol["candidate"]["version"] == "0.5.0"
    assert protocol["candidate"]["development_suites_are_claim_eligible"] is False
    assert protocol["evaluation_contract"] == {
        "candidate_profile_id": "knowledge-context-v1",
        "candidate_interface": "pinned-suite-adapter",
        "install_artifact": "wheel",
        "artifact_hash": "sha256",
        "fresh_workspace_per_suite": True,
        "query_network_access": "forbidden",
        "candidate_telemetry": "forbidden",
        "candidate_writes": "evaluator-workspace-only",
        "hidden_case_retention_after_run": "forbidden",
        "discovery_default_enabled": False,
        "generated_knowledge_authoritative": False,
        "legal_authority_requires_exact_source": True,
    }
    assert len(suites) == protocol["claim_policy"]["minimum_external_suites"] == 10
    assert len(suite_ids) == len(set(suite_ids))
    assert len(baselines) >= protocol["claim_policy"]["minimum_distinct_named_baselines"]
    assert {suite["role"] for suite in suites} == {
        "external_public_frozen",
        "external_hidden",
    }
    assert sum(suite["role"] == "external_hidden" for suite in suites) == 2
    assert set(dimensions) == {
        dimension
        for suite in suites
        for dimension in suite["required_dimensions"]
    }
    assert all(
        suite["named_baselines"]
        and len(suite["named_baselines"]) == len(set(suite["named_baselines"]))
        and set(suite["required_dimensions"]) <= set(dimensions)
        for suite in suites
    )
    run_example = json.loads(
        (
            repository / "benchmarks/external/run-draft-v3.example.json"
        ).read_text(encoding="utf-8")
    )
    assert set(run_example) == _RUN_FIELDS - {"evidence_manifest_artifact"}


def test_historical_longmemeval_development_report_keeps_its_frozen_identity() -> None:
    repository = Path(__file__).resolve().parents[1]
    report = json.loads(
        (
            repository
            / "benchmarks/external/longmemeval-s-dev-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert report["schema_version"] == "deeplaw.external-dev-diagnostic/v2"
    assert report["claim_eligible"] is False
    assert len(report["cases"]) == report["selection"]["case_count"] == 60
    assert report["context"]["recall5"] == 0.8905555555555555
    assert report["context"]["duplicate_count"] == 0
    preference_cases = [
        case
        for case in report["cases"]
        if case["type"] == "single-session-preference"
    ]
    other_cases = [
        case
        for case in report["cases"]
        if case["type"] != "single-session-preference"
    ]
    assert len(preference_cases) == 10
    assert sum(case["context"]["hit1"] for case in preference_cases) / 10 == 0.2
    assert (
        sum(case["context"]["recall5"] for case in preference_cases) / 10 == 0.6
    )
    assert (
        sum(case["context"]["irrelevant_rate"] for case in preference_cases) / 10
        == 0.85
    )
    assert sum(case["context"]["hit1"] for case in other_cases) / 50 == 0.98
    assert report["implementation_files"] == {
        "src/deeplaw/context_compiler.py": (
            "19027d20817c1e359c9d888f2e3d8a8a7f3781c4910ef0d4261f01a608814f62"
        ),
        "src/deeplaw/util.py": (
            "d7fe62c839384b342b106bccf20f88166c78ec6901a5d9682649028d194b1ffb"
        ),
        "src/deeplaw/knowledge_compiler.py": (
            "01a05eda7b79b734e88ae130f32100f009c87b09ca7bb3c9f056f12328aaa200"
        ),
        "src/deeplaw/knowledge_store.py": (
            "325ee6d7a779d7cb55b38d283e06c822591413a397537cf96468205d25c96431"
        ),
        "benchmarks/external/run_longmemeval_s_dev.py": (
            "fc59e76548adf0de19c8cbf695463d10e891ea0f537c1c8b0f5c9f484b6f88e8"
        ),
    }


def test_claim_gate_counts_only_cryptographically_signed_independent_evaluators(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    protocol = json.loads(
        (repository / "benchmarks/external/protocol-v3.json").read_text(encoding="utf-8")
    )
    evidence = deepcopy(
        json.loads(
            (
                repository / "benchmarks/external/claim-evidence.pending.json"
            ).read_text(encoding="utf-8")
        )
    )
    attestation = (
        json.dumps(
            {
                "schema_version": "deeplaw.external-attestation/v1",
                "organization": "Independent Lab",
                "protocol_id": protocol["protocol_id"],
                "candidate": evidence["candidate"],
                "suite_runs": [
                    {
                        "suite_id": "longmemeval-v2",
                        "evidence_manifest_sha256": "c" * 64,
                    }
                ],
                "issued_at": "2026-07-26T00:00:00Z",
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_bytes(attestation)
    private_key = Ed25519PrivateKey.generate()
    evidence["independent_evaluators"] = [
        {
            "organization": "Independent Lab",
            "independent": True,
            "attestation_artifact": {
                "path": attestation_path.name,
                "sha256": hashlib.sha256(attestation).hexdigest(),
            },
            "public_key_ed25519_base64": base64.b64encode(
                private_key.public_key().public_bytes_raw()
            ).decode("ascii"),
            "signature_base64": base64.b64encode(
                private_key.sign(attestation)
            ).decode("ascii"),
        }
    ]

    result = evaluate_claim(
        protocol,
        evidence,
        evidence_path=tmp_path / "evidence.json",
    )

    assert result["signed_independent_evaluator_count"] == 1
    assert result["independent_evaluator_count"] == 0
    assert not any("attestation signature is invalid" in error for error in result["errors"])


def test_claim_gate_rejects_a_weakened_copy_of_the_frozen_protocol() -> None:
    repository = Path(__file__).resolve().parents[1]
    protocol = json.loads(
        (repository / "benchmarks/external/protocol-v3.json").read_text(encoding="utf-8")
    )
    evidence_path = repository / "benchmarks/external/claim-evidence.pending.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    protocol["claim_policy"]["minimum_external_suites"] = 0

    result = evaluate_claim(
        protocol,
        evidence,
        evidence_path=evidence_path,
    )

    assert result["passed"] is False
    assert "protocol content differs from the frozen v3 commitment" in result["errors"]


def test_claim_gate_rejects_the_superseded_v2_protocol() -> None:
    repository = Path(__file__).resolve().parents[1]
    protocol = json.loads(
        (repository / "benchmarks/external/protocol-v2.json").read_text(encoding="utf-8")
    )
    evidence_path = repository / "benchmarks/external/claim-evidence.pending.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["protocol_id"] = protocol["protocol_id"]

    result = evaluate_claim(
        protocol,
        evidence,
        evidence_path=evidence_path,
    )

    assert result["passed"] is False
    assert "unsupported protocol schema" in result["errors"]
    assert "protocol content differs from the frozen v3 commitment" in result["errors"]


def test_claim_gate_accepts_only_a_fully_bound_independent_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protocol = {
        "schema_version": "deeplaw.external-proof-protocol/v3",
        "protocol_id": "synthetic-proof/v1",
        "frozen_at": "2026-07-26T00:00:00Z",
        "candidate": {
            "system_id": "deeplaw-2.0",
            "version": "0.5.0",
            "maintainer_organization": "Eysn0130",
        },
        "evaluation_contract": {
            "candidate_profile_id": "knowledge-context-v1",
            "candidate_interface": "pinned-suite-adapter",
            "install_artifact": "wheel",
            "artifact_hash": "sha256",
            "fresh_workspace_per_suite": True,
            "query_network_access": "forbidden",
            "candidate_telemetry": "forbidden",
            "candidate_writes": "evaluator-workspace-only",
            "hidden_case_retention_after_run": "forbidden",
            "discovery_default_enabled": False,
            "generated_knowledge_authoritative": False,
            "legal_authority_requires_exact_source": True,
        },
        "claim_policy": {
            "unbounded_universal_claim_allowed": False,
            "minimum_external_suites": 1,
            "minimum_distinct_named_baselines": 1,
            "minimum_independent_evaluators": 1,
            "requires_external_hidden_labels": True,
            "requires_all_runs_independently_attested": True,
            "allowed_claim_template": (
                "DeepLaw 2.0 {version} passed {protocol_id} against "
                "{baseline_count} baseline on {suite_count} suite."
            ),
        },
        "statistics": {
            "paired_bootstrap_samples": 1_000,
            "confidence": 0.95,
            "seed": 7,
            "familywise_primary_correction": "holm-bonferroni",
            "superiority_familywise_alpha": 0.05,
        },
        "required_dimensions": {
            "task_success": {
                "direction": "higher",
                "gate": "superior",
                "minimum_effect": 0.01,
                "noninferiority_margin": 0.0,
            }
        },
        "suites": [
            {
                "suite_id": "hidden",
                "role": "external_hidden",
                "repository_revision": None,
                "dataset_revision": "third-party-commitment-required-before-candidate-delivery",
                "required_dimensions": ["task_success"],
                "named_baselines": ["independent/baseline"],
            }
        ],
    }
    monkeypatch.setattr(
        claim_gate_module,
        "FROZEN_PROTOCOL_CANONICAL_SHA256",
        hashlib.sha256(canonical_json(protocol).encode()).hexdigest(),
    )
    cases = [_case(f"case-{index}", [f"source-{index}"]) for index in range(20)]
    candidate_report = score_retrieval(
        cases,
        [
            _run(f"case-{index}", [f"source-{index}"], success=True)
            for index in range(20)
        ],
        k=1,
        suite_id="hidden",
        system_id="deeplaw-2.0",
        cases_sha256="a" * 64,
        run_sha256="b" * 64,
        claim_eligible=True,
        claim_ineligibility_reason=None,
    )
    baseline_report = score_retrieval(
        cases,
        [
            _run(f"case-{index}", ["irrelevant"], success=False)
            for index in range(20)
        ],
        k=1,
        suite_id="hidden",
        system_id="independent/baseline",
        cases_sha256="a" * 64,
        run_sha256="c" * 64,
        claim_eligible=True,
        claim_ineligibility_reason=None,
    )
    comparison = paired_comparison(
        candidate_report,
        baseline_report,
        metric="task_success",
        direction="higher",
        samples=1_000,
        confidence=0.95,
        seed=7,
        noninferiority_margin=0.0,
        minimum_effect=0.01,
    )

    def artifact(name: str, value: object) -> dict[str, str]:
        path = tmp_path / name
        payload = (json.dumps(value, sort_keys=True) + "\n").encode()
        path.write_bytes(payload)
        return {"path": name, "sha256": hashlib.sha256(payload).hexdigest()}

    candidate_artifact = artifact("candidate.json", candidate_report)
    baseline_artifact = artifact("baseline.json", baseline_report)
    comparison_artifact = artifact("comparison.json", comparison)
    raw_output_artifact = artifact("raw-output.json", {"complete": True})
    candidate_install_artifact = artifact(
        "deeplaw-0.5.0-py3-none-any.whl",
        {"candidate": "fixed-wheel"},
    )
    dataset_commitment_artifact = artifact(
        "dataset-commitment.json",
        {
            "schema_version": "deeplaw.dataset-commitment/v1",
            "protocol_id": protocol["protocol_id"],
            "suite_id": "hidden",
            "evaluator_organization": "Independent Lab",
            "repository_revision": None,
            "dataset_revision": "hidden-commitment-sha256",
            "dataset_sha256": "f" * 64,
            "case_count": 20,
            "corpus_record_count": 20,
            "labels_access": "external_evaluator_only",
            "committed_at": "2026-07-26T00:00:10Z",
        },
    )
    baseline_commitment_artifact = artifact(
        "baseline-commitment.json",
        {
            "schema_version": "deeplaw.baseline-commitment/v1",
            "protocol_id": protocol["protocol_id"],
            "suite_id": "hidden",
            "evaluator_organization": "Independent Lab",
            "baselines": [
                {
                    "baseline_system_id": "independent/baseline",
                    "implementation_revision": "fixed-revision",
                    "configuration_sha256": "1" * 64,
                    "environment_sha256": "2" * 64,
                }
            ],
            "committed_at": "2026-07-26T00:00:20Z",
        },
    )
    candidate = {
        "system_id": "deeplaw-2.0",
        "version": "0.5.0",
        "git_commit": "d" * 40,
        "artifact_sha256": candidate_install_artifact["sha256"],
    }
    run = {
        "suite_id": "hidden",
        "repository_revision": None,
        "dataset_revision": "hidden-commitment-sha256",
        "dataset_sha256": "f" * 64,
        "dataset_commitment_artifact": dataset_commitment_artifact,
        "dataset_committed_at": "2026-07-26T00:00:10Z",
        "baseline_commitment_artifact": baseline_commitment_artifact,
        "baseline_configs_committed_at": "2026-07-26T00:00:20Z",
        "candidate_received_at": "2026-07-26T00:00:30Z",
        "candidate_artifact_sha256_observed": candidate_install_artifact["sha256"],
        "candidate_install_artifact": candidate_install_artifact,
        "candidate_git_commit_observed": "d" * 40,
        "candidate_version_observed": "0.5.0",
        "candidate_profile_id": "knowledge-context-v1",
        "candidate_install_clean": True,
        "candidate_query_network_disabled": True,
        "candidate_telemetry_disabled": True,
        "candidate_workspace_isolated": True,
        "candidate_writes_confined": True,
        "hidden_case_data_not_retained": True,
        "full_suite": True,
        "protocol_frozen_before_run": True,
        "no_post_freeze_tuning": True,
        "same_reader_model": True,
        "same_context_budget": True,
        "all_failures_retained": True,
        "reader_model": "fixed-reader",
        "reader_model_revision": "reader-revision",
        "context_token_budget": 4_096,
        "hardware": "fixed-host",
        "index_build_seconds": 1.0,
        "peak_memory_bytes": 1_024,
        "disk_bytes": 2_048,
        "model_cost_usd": 0.0,
        "started_at": "2026-07-26T00:01:00Z",
        "completed_at": "2026-07-26T00:02:00Z",
        "labels_access": "external_evaluator_only",
        "independent_evaluator": True,
        "evaluator_organization": "Independent Lab",
        "raw_output_artifact": raw_output_artifact,
        "comparisons": [
            {
                "artifact": comparison_artifact,
                "candidate_report_artifact": candidate_artifact,
                "baseline_report_artifact": baseline_artifact,
            }
        ],
    }
    manifest = _expected_run_manifest(
        run,
        protocol_id=protocol["protocol_id"],
        candidate=candidate,
    )
    manifest_artifact = artifact("suite-manifest.json", manifest)
    run["evidence_manifest_artifact"] = manifest_artifact
    attestation = {
        "schema_version": "deeplaw.external-attestation/v1",
        "organization": "Independent Lab",
        "protocol_id": protocol["protocol_id"],
        "candidate": candidate,
        "suite_runs": [
            {
                "suite_id": "hidden",
                "evidence_manifest_sha256": manifest_artifact["sha256"],
            }
        ],
        "issued_at": "2026-07-26T00:03:00Z",
    }
    attestation_payload = (json.dumps(attestation, sort_keys=True) + "\n").encode()
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_bytes(attestation_payload)
    private_key = Ed25519PrivateKey.generate()
    evidence = {
        "schema_version": "deeplaw.claim-evidence/v1",
        "protocol_id": protocol["protocol_id"],
        "candidate": candidate,
        "status": "complete",
        "runs": [run],
        "independent_evaluators": [
            {
                "organization": "Independent Lab",
                "independent": True,
                "attestation_artifact": {
                    "path": attestation_path.name,
                    "sha256": hashlib.sha256(attestation_payload).hexdigest(),
                },
                "public_key_ed25519_base64": base64.b64encode(
                    private_key.public_key().public_bytes_raw()
                ).decode(),
                "signature_base64": base64.b64encode(
                    private_key.sign(attestation_payload)
                ).decode(),
            }
        ],
    }

    result = evaluate_claim(
        protocol,
        evidence,
        evidence_path=tmp_path / "evidence.json",
    )

    assert result["passed"] is True
    assert result["independent_evaluator_count"] == 1
    assert result["allowed_claim"] is not None

    run["candidate_artifact_sha256_observed"] = "0" * 64
    wrong_candidate_manifest = _expected_run_manifest(
        run,
        protocol_id=protocol["protocol_id"],
        candidate=candidate,
    )
    wrong_candidate_manifest_artifact = artifact(
        "suite-manifest.json",
        wrong_candidate_manifest,
    )
    run["evidence_manifest_artifact"] = wrong_candidate_manifest_artifact
    attestation["suite_runs"][0]["evidence_manifest_sha256"] = (
        wrong_candidate_manifest_artifact["sha256"]
    )
    wrong_candidate_attestation_payload = (
        json.dumps(attestation, sort_keys=True) + "\n"
    ).encode()
    attestation_path.write_bytes(wrong_candidate_attestation_payload)
    evidence["independent_evaluators"][0]["attestation_artifact"] = {
        "path": attestation_path.name,
        "sha256": hashlib.sha256(
            wrong_candidate_attestation_payload
        ).hexdigest(),
    }
    evidence["independent_evaluators"][0]["signature_base64"] = base64.b64encode(
        private_key.sign(wrong_candidate_attestation_payload)
    ).decode()

    wrong_candidate_result = evaluate_claim(
        protocol,
        evidence,
        evidence_path=tmp_path / "evidence.json",
    )

    assert wrong_candidate_result["passed"] is False
    assert any(
        "did not reverify the frozen candidate identity" in error
        for error in wrong_candidate_result["errors"]
    )

    run["candidate_artifact_sha256_observed"] = candidate_install_artifact["sha256"]
    forged_comparison = deepcopy(comparison)
    forged_comparison["ci_low"] = -1.0
    run["comparisons"][0]["artifact"] = artifact(
        "comparison.json",
        forged_comparison,
    )
    forged_manifest = _expected_run_manifest(
        run,
        protocol_id=protocol["protocol_id"],
        candidate=candidate,
    )
    forged_manifest_artifact = artifact("suite-manifest.json", forged_manifest)
    run["evidence_manifest_artifact"] = forged_manifest_artifact
    attestation["suite_runs"][0]["evidence_manifest_sha256"] = (
        forged_manifest_artifact["sha256"]
    )
    forged_attestation_payload = (
        json.dumps(attestation, sort_keys=True) + "\n"
    ).encode()
    attestation_path.write_bytes(forged_attestation_payload)
    evidence["independent_evaluators"][0]["attestation_artifact"] = {
        "path": attestation_path.name,
        "sha256": hashlib.sha256(forged_attestation_payload).hexdigest(),
    }
    evidence["independent_evaluators"][0]["signature_base64"] = base64.b64encode(
        private_key.sign(forged_attestation_payload)
    ).decode()

    forged_result = evaluate_claim(
        protocol,
        evidence,
        evidence_path=tmp_path / "evidence.json",
    )

    assert forged_result["passed"] is False
    assert any(
        "differs from deterministic paired-bootstrap recomputation" in error
        for error in forged_result["errors"]
    )


def test_longmemeval_v2_adapter_exercises_the_real_knowledge_vault(
    tmp_path: Path,
) -> None:
    memory = DeepLawMemory(
        {
            "workspace_dir": str(tmp_path / "workspace"),
            "max_items": 4,
            "max_chars": 1_200,
            "frozen_fixture_approved": True,
        }
    )
    memory.insert(
        {
            "id": "trajectory-1",
            "goal": "Deploy Mercury",
            "outcome": "Deployment completed",
            "start_url": "https://example.test",
            "states": [
                {
                    "url": "https://example.test/deploy",
                    "action": "Click Mercury",
                    "thought": "Use the blue checkpoint",
                    "accessibility_tree": "Mercury deployment uses the blue checkpoint.",
                    "screenshot": "trajectory-1/0.png",
                }
            ],
        }
    )

    context = memory.query("Which checkpoint does Mercury deployment use?")
    payload = json.loads(context[0]["value"])

    assert context[0]["type"] == "text"
    assert payload["budget"]["selected_items"] >= 1
    assert "blue checkpoint" in context[0]["value"]
    assert memory.post_query_hook(
        query="Which checkpoint does Mercury deployment use?",
        query_image=None,
        memory_context=context,
    )["query_image_ignored"] is False

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACTS = REPOSITORY / "contracts"


def _schema(name: str) -> dict[str, Any]:
    value = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def _validate(name: str, value: dict[str, Any]) -> None:
    Draft202012Validator(_schema(name)).validate(value)


def _gate_result(*, status: str = "not_executed") -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "deeplaw.provenance-bound-gate-result/v5",
        "profile": "kernel_release_core",
        "reference_provenance": "not_applicable",
        "human_authenticity": "not_claimed",
        "qualification_run_id": 3,
        "gate_id": "official_legal_pack",
        "category": "Capability",
        "validator_id": "deeplaw-typed-qualification-v3",
        "validator_version": "3",
        "validator_source": {
            "relative_path": "benchmarks/release/typed_qualification_evidence.py",
            "byte_size": 1,
            "file_sha256": "a" * 64,
        },
        "validator_executable": {
            "relative_path": "benchmarks/release/assemble_commercial_qualification_v9.py",
            "byte_size": 1,
            "file_sha256": "b" * 64,
        },
        "classification_binding": {
            "classification_id": "deeplaw-v013-commercial-gates-v9",
            "classification_schema_version": "deeplaw.v013-release-gate-classification/v9",
            "classification_sha256": "c" * 64,
        },
        "candidate_binding": {
            "candidate_commit": "d" * 40,
            "candidate_tree": "e" * 40,
            "candidate_wheel_sha256": "f" * 64,
            "candidate_sdist_sha256": "1" * 64,
        },
        "protocol_binding": {
            "protocol_id": "deeplaw-v013-qualification-v3",
            "protocol_sha256": "2" * 64,
            "frozen": True,
        },
        "threshold_binding": {
            "threshold_id": "deeplaw-v013-thresholds-v1",
            "threshold_sha256": "3" * 64,
            "frozen": True,
        },
        "corpora": [],
        "status": status,
        "executions": [],
        "run_ids": [],
        "metrics": [],
        "hard_failures": [],
        "inputs": [],
        "result_sha256": "4" * 64,
    }
    return value


def test_not_executed_claim_gate_has_no_synthetic_execution_or_reference_binding() -> None:
    value = _gate_result()
    _validate("provenance-bound-gate-result.v5.schema.json", value)

    value["reference_binding"] = {
        "semantic_reference_sha256": "5" * 64,
    }
    with pytest.raises(ValidationError):
        _validate("provenance-bound-gate-result.v5.schema.json", value)


def test_passed_gate_requires_actual_typed_v3_execution_and_input() -> None:
    value = _gate_result(status="passed")
    with pytest.raises(ValidationError):
        _validate("provenance-bound-gate-result.v5.schema.json", value)

    input_id = "typed-professional-evidence"
    value["corpora"] = [
        {
            "role": "professional_evidence",
            "source": "candidate_artifact",
            "sha256": "5" * 64,
            "frozen": True,
        }
    ]
    value["executions"] = [
        {
            "run_id": "professional-evidence-run",
            "workflow_run_id": 2,
            "input_refs": [input_id],
            "evidence_kind": "professional_evidence_rows",
        }
    ]
    value["run_ids"] = ["professional-evidence-run"]
    value["metrics"] = [
        {"metric": "required_duty_coverage", "observed": 1, "input_refs": [input_id]}
    ]
    value["hard_failures"] = [
        {
            "failure_id": "professional_evidence_mismatch",
            "count": 0,
            "maximum_allowed": 0,
            "input_refs": [input_id],
        }
    ]
    value["inputs"] = [
        {
            "input_id": input_id,
            "relative_path": "typed/professional.json",
            "byte_size": 1,
            "file_sha256": "6" * 64,
            "schema_version": "deeplaw.typed-qualification-evidence/v3",
            "record_sha256": "7" * 64,
            "artifact_kind": "typed-qualification-evidence",
            "evidence_kind": "professional_evidence_rows",
            "derived_record_sha256": "8" * 64,
        }
    ]
    _validate("provenance-bound-gate-result.v5.schema.json", value)


def test_release_manifest_core_pass_does_not_require_optional_claims_to_pass() -> None:
    capabilities = [
        {
            "gate_id": gate_id,
            "status": "not_executed",
            "claim_eligible": False,
        }
        for gate_id in (
            "official_legal_pack",
            "semantic_restore",
            "claude",
            "gui_desktop_interoperability",
        )
    ]
    competitive = [
        {"gate_id": f"research-{index}", "status": "not_executed", "claim_eligible": False}
        for index in range(10)
    ]
    value: dict[str, Any] = {
        "schema_version": "deeplaw.commercial-release-manifest/v9",
        "profile": "kernel_release_core",
        "reference_provenance": "not_applicable",
        "human_authenticity": "not_claimed",
        "environment": {
            "platform_system": "synthetic",
            "platform_release": "synthetic",
            "platform_version": "synthetic",
            "machine": "synthetic",
            "python_implementation": "CPython",
            "python_version": "3.12",
            "python_executable_name": "python",
            "uv_version": "synthetic",
            "ci": True,
            "github_actions": True,
            "github_runner_os": "synthetic",
            "github_runner_arch": "synthetic",
        },
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": "0.13.0",
            "tag": "v0.13.0",
            "commit": "a" * 40,
            "tree": "b" * 40,
        },
        "run_ids": {"candidate_run_id": 1, "evidence_run_id": 2, "qualification_run_id": 3},
        "candidate_binding": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "lock_sha256": "c" * 64,
            "wheel_sha256": "d" * 64,
            "sdist_sha256": "e" * 64,
            "version": "0.13.0",
        },
        "artifact_binding": {
            "wheel": {
                "path": "deeplaw-0.13.0-py3-none-any.whl",
                "sha256": "d" * 64,
                "byte_size": 1,
            },
            "sdist": {"path": "deeplaw-0.13.0.tar.gz", "sha256": "e" * 64, "byte_size": 1},
            "retained_manifest_sha256": "f" * 64,
        },
        "evidence_bundle_binding": {
            "manifest_path": "evidence/bundle-manifest.json",
            "manifest_sha256": "1" * 64,
            "candidate_run_id": 1,
            "evidence_run_id": 2,
        },
        "pre_publish_artifact_gate": {
            "path": "evidence/pre-publish.json",
            "receipt_sha256": "2" * 64,
            "status": "pre_publish_passed",
        },
        "gate_evidence": {
            "report_path": "evidence/commercial-evidence-report.json",
            "report_sha256": "3" * 64,
            "record_sha256": "4" * 64,
            "classification_sha256": "5" * 64,
            "status": "passed",
            "hard_zero": True,
            "core_gates_passed": True,
            "capability_claims": capabilities,
            "competitive_research_claims": competitive,
        },
        "release_ready": True,
        "public_release_verified": False,
        "post_public_verification": None,
        "kernel_release_claim_eligible": True,
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
        "record_sha256": "6" * 64,
    }
    _validate("commercial-release-manifest.v9.schema.json", value)

    blocked = deepcopy(value)
    blocked["gate_evidence"]["core_gates_passed"] = False
    with pytest.raises(ValidationError):
        _validate("commercial-release-manifest.v9.schema.json", blocked)


def test_report_contract_points_only_to_v5_gate_results() -> None:
    schema = _schema("commercial-evidence-report.v6.schema.json")
    assert schema["$defs"]["resultArtifact"]["properties"]["schema_version"] == {
        "const": "deeplaw.provenance-bound-gate-result/v5"
    }
    assert "reference_binding" not in schema["required"]

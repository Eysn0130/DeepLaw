from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACTS = REPOSITORY / "contracts"
SHA = "a" * 64
OTHER_SHA = "b" * 64
COMMIT = "1" * 40
TREE = "2" * 40


def _schema(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _validate(name: str, value: dict[str, Any]) -> None:
    schema = _schema(name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _assert_invalid(name: str, value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _validate(name, value)


def _reference() -> dict[str, Any]:
    case_template = {
        "labels": ["include_answer"],
        "expected": {"include": ["include_answer"], "exclude": []},
        "duties": ["exact_citation"],
        "hard_failures": ["false_authority"],
        "thresholds": {
            "minimum_case_pass_rate": 1.0,
            "minimum_duty_coverage": 1.0,
            "maximum_hard_failures": 0,
            "maximum_false_authority": 0,
        },
    }
    cases = []
    for suffix in ("cold", "resume", "compact"):
        case = copy.deepcopy(case_template)
        case["case_id"] = f"goldcase_{suffix}"
        cases.append(case)
    reviewers = []
    for index in range(3):
        reviewers.append(
            {
                "agent_id": f"agent:reviewer-{index + 1}",
                "role": f"role-{index + 1}",
                "model_id": f"model:reviewer-{index + 1}",
                "implementation_sha256": SHA,
                "prompt_sha256": SHA,
                "process_identity_sha256": f"{index + 3:x}" * 64,
                "output_sha256": OTHER_SHA,
                "conclusions_hidden_from_peers": True,
                "separate_process": True,
            }
        )
    return {
        "schema_version": "deeplaw.semantic-machine-reference/v1",
        "status": "semantic_machine_reference_frozen",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "frozen_at": "2026-08-17T00:00:00Z",
        "reference_id": "semanticref_0123456789abcdef01234567",
        "model_outputs_seen_before_freeze": True,
        "candidate_visible_when_frozen": False,
        "human_claim_eligible": False,
        "competitive_claim_eligible": False,
        "agent_review": {
            "reviewers": reviewers,
            "roster_sha256": SHA,
            "consensus_sha256": OTHER_SHA,
            "isolation_sha256": SHA,
            "rubric_sha256": OTHER_SHA,
            "source_corpus_sha256": SHA,
            "minimum_distinct_agents": 3,
            "unanimity_required": True,
        },
        "labels": [
            {"label_id": "include_answer", "description": "Expected bounded answer"}
        ],
        "cases": cases,
        "duties": [
            {
                "duty_id": "exact_citation",
                "description": "Every included result retains exact evidence identity.",
            }
        ],
        "hard_failures": [
            {
                "code": "false_authority",
                "description": "Derived text cannot be promoted to source authority.",
            }
        ],
        "thresholds": {
            "minimum_case_pass_rate": 1.0,
            "minimum_duty_coverage": 1.0,
            "maximum_hard_failures": 0,
            "maximum_false_authority": 0,
        },
        "record_sha256": SHA,
    }


def _binding() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.candidate-gold-binding-receipt/v2",
        "status": "post_build_machine_reference_bound",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "bound_at": "2026-08-17T01:00:00Z",
        "semantic_reference": {
            "reference_id": "semanticref_0123456789abcdef01234567",
            "schema_version": "deeplaw.semantic-machine-reference/v1",
            "sha256": SHA,
        },
        "agent_roster": {"sha256": SHA},
        "agent_consensus": {"sha256": OTHER_SHA},
        "agent_isolation": {"sha256": SHA},
        "candidate": {"commit": COMMIT, "tree": TREE, "lock_sha256": SHA},
        "artifacts": {
            "wheel": {
                "name": "deeplaw-0.13.0-py3-none-any.whl",
                "sha256": SHA,
                "byte_size": 100,
            },
            "sdist": {
                "name": "deeplaw-0.13.0.tar.gz",
                "sha256": OTHER_SHA,
                "byte_size": 120,
            },
        },
        "holdout": {"role": "qualification_holdout", "sha256": SHA},
        "blind": {"role": "final_blind", "sha256": OTHER_SHA},
        "scorer_panel": {
            "scorer_a": {
                "role": "independent_scorer_a",
                "identity": "independent-scorer-a:machine-v1",
                "sha256": SHA,
            },
            "scorer_b": {
                "role": "independent_scorer_b",
                "identity": "independent-scorer-b:machine-v1",
                "sha256": OTHER_SHA,
            },
            "panel_sha256": SHA,
            "distinct_scorers": True,
        },
        "arbiter": {
            "role": "deterministic_arbiter",
            "identity": "deterministic-arbiter:machine-v1",
            "sha256": OTHER_SHA,
        },
        "runner": {"identity": "runner:isolated-v1", "sha256": OTHER_SHA},
        "record_sha256": SHA,
    }


def _source(
    path: str = "evidence/reference.json", media_type: str = "application/json"
) -> dict[str, Any]:
    return {"relative_path": path, "byte_size": 10, "sha256": SHA, "media_type": media_type}


def _old_payload(kind: str) -> dict[str, Any]:
    if kind == "candidate_full_junit":
        return {"source": _source("candidate/junit.xml", "application/xml")}
    if kind == "candidate_platform_receipt":
        platforms = ("ubuntu", "macos", "windows")
        versions = ("3.11", "3.12", "3.13")
        return {
            "source": _source("candidate/platform.json"),
            "platform_manifest_source": _source("candidate/platform-manifest.json"),
            "junit_sources": [
                {
                    "platform": platform,
                    "python_version": version,
                    "source": _source(
                        f"candidate/{platform}-{version}.xml", "application/xml"
                    ),
                }
                for platform in platforms
                for version in versions
            ],
        }
    if kind == "host_event_sequence":
        return {
            name: _source(f"host/{name}.json")
            for name in (
                "event_source",
                "lifecycle_source",
                "usage_source",
                "expected_source",
                "continuity_source",
                "isolation_source",
            )
        }
    if kind == "exact_wheel_execution":
        return {"source": _source("candidate/exact-wheel.json")}
    if kind == "human_gold_scorer":
        return {
            "semantic_gold_source": _source("human/semantic-gold.json"),
            "candidate_binding_source": _source("human/candidate-binding.json"),
            "scorer_rows_source": _source("human/scorer-rows.json"),
            "human_attestation_source": _source("human/attestation.json"),
            "process_identity": {
                "scorer_process_id": "scorer:legacy",
                "runner_process_id": "runner:legacy",
                "scorer_identity_sha256": SHA,
                "runner_identity_sha256": OTHER_SHA,
                "separate_processes": True,
            },
        }
    if kind == "legal_rows":
        return {
            "source_catalog_source": _source("legal/catalog.json"),
            "original_source_refs": [
                {
                    "source_id": f"legal-source-{index}",
                    "version_id": f"legal-version-{index}",
                    "source": _source(f"legal/source-{index}.pdf", "application/pdf"),
                }
                for index in range(28)
            ],
            "expected_source": _source("legal/expected.json"),
            "observed_source": _source("legal/observed.json"),
        }
    if kind == "wiki_journey_rows":
        return {
            "expected_source": _source("wiki/expected.json"),
            "observed_source": _source("wiki/observed.json"),
        }
    if kind == "context_capsule_selection_usage":
        return {
            "expected_source": _source("context/expected.json"),
            "provider_capsule_source": _source("context/provider.json"),
            "query_trace_source": _source("context/query.json"),
            "ledger_source": _source("context/ledger.json"),
            "usage_source": _source("context/usage.json"),
        }
    if kind == "scale_report":
        return {
            "expected_source": _source("scale/expected.json"),
            "observed_source": _source("scale/observed.json"),
        }
    if kind == "retained_supply_chain":
        return {
            "candidate_build_source": _source("supply/build.json"),
            "retained_candidate_source": _source("supply/retained.json"),
            "pre_publish_receipt_source": _source("supply/pre-publish.json"),
            "wheel_source": _source("supply/deeplaw.whl", "application/octet-stream"),
            "sdist_source": _source("supply/deeplaw.tar.gz", "application/octet-stream"),
            "sbom_source": _source("supply/sbom.json"),
            "openvex_source": _source("supply/openvex.json"),
            "licenses_source": _source("supply/licenses.json"),
            "provenance_source": _source("supply/provenance.json"),
        }
    raise ValueError(f"unknown old typed kind: {kind}")


def _typed(
    kind: str = "machine_reference_scorer",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload is None:
        payload = {}
    if kind == "machine_reference_scorer" and not payload:
        payload = {
            "semantic_reference_source": _source("reference/semantic.json"),
            "candidate_binding_source": _source("reference/binding.json"),
            "agent_roster_source": _source("reference/roster.json"),
            "agent_consensus_source": _source("reference/consensus.json"),
            "agent_isolation_source": _source("reference/isolation.json"),
            "scorer_a_rows_source": _source("scorer/a-rows.json"),
            "scorer_b_rows_source": _source("scorer/b-rows.json"),
            "arbiter_consensus_rows_source": _source("scorer/arbiter-rows.json"),
            "process_identity": {
                "scorer_a_process_id": "scorer-a:process",
                "scorer_b_process_id": "scorer-b:process",
                "runner_process_id": "runner:process",
                "arbiter_process_id": "arbiter:process",
                "scorer_a_identity_sha256": SHA,
                "scorer_b_identity_sha256": OTHER_SHA,
                "runner_identity_sha256": OTHER_SHA,
                "arbiter_identity_sha256": SHA,
                "scorer_processes_distinct": True,
                "arbiter_process_distinct": True,
                "separate_processes": True,
            },
        }
    panel = {
        "scorer_a": {
            "role": "independent_scorer_a",
            "identity": "independent-scorer-a:machine-v1",
            "sha256": SHA,
        },
        "scorer_b": {
            "role": "independent_scorer_b",
            "identity": "independent-scorer-b:machine-v1",
            "sha256": OTHER_SHA,
        },
        "panel_sha256": SHA,
        "distinct_scorers": True,
    }
    arbiter = {
        "role": "deterministic_arbiter",
        "identity": "deterministic-arbiter:machine-v1",
        "sha256": OTHER_SHA,
    }
    value = {
        "schema_version": "deeplaw.typed-qualification-evidence/v2",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "kind": kind,
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": SHA,
            "sdist_sha256": OTHER_SHA,
        },
        "run_binding": {"run_id": "machine-run-1", "workflow_run_id": 1},
        "corpus": {"sha256": SHA, "role": "qualification_holdout"},
        "runner": {"identity": "runner:isolated-v1", "sha256": OTHER_SHA},
        "scorer": {
            "identity": (
                arbiter["identity"]
                if kind == "machine_reference_scorer"
                else "scorer:legacy-v1"
            ),
            "sha256": arbiter["sha256"] if kind == "machine_reference_scorer" else SHA,
        },
        "payload": payload,
        "record_sha256": SHA,
    }
    if kind == "machine_reference_scorer":
        value["scorer_panel"] = panel
        value["arbiter"] = arbiter
    return value


def _bundle() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.external-qualification-bundle-manifest/v4",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "candidate_run_id": 1,
        "evidence_run_id": 2,
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": SHA,
            "sdist_sha256": OTHER_SHA,
        },
        "external_inputs": {
            "semantic_reference_sha256": SHA,
            "candidate_binding_sha256": OTHER_SHA,
            "qualification_holdout_sha256": SHA,
            "final_blind_holdout_sha256": OTHER_SHA,
            "agent_roster_sha256": SHA,
            "agent_consensus_sha256": OTHER_SHA,
            "agent_isolation_sha256": SHA,
            "runner_sha256": OTHER_SHA,
            "scorer_panel_sha256": SHA,
            "arbiter_sha256": OTHER_SHA,
            "compiler_scorer_isolation_sha256": OTHER_SHA,
        },
        "candidate_full_raw_inventory_sha256": SHA,
        "files": [
            {
                "relative_path": "typed/machine-reference.json",
                "byte_size": 10,
                "sha256": SHA,
                "media_type": "application/json",
                "evidence_kind": "machine_reference_scorer",
            }
        ],
        "record_sha256": SHA,
    }


def _reference_binding() -> dict[str, Any]:
    return {
        "semantic_reference_sha256": SHA,
        "agent_roster_sha256": SHA,
        "agent_consensus_sha256": OTHER_SHA,
        "agent_isolation_sha256": SHA,
        "scorer_panel_sha256": SHA,
        "arbiter_sha256": OTHER_SHA,
        "frozen": True,
    }


def _gate_result() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.provenance-bound-gate-result/v4",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "qualification_run_id": 3,
        "gate_id": "agent_gold_isolation",
        "category": "Core",
        "validator_id": "validator:typed-v2",
        "validator_version": "2",
        "validator_source": {
            "relative_path": "validator.py",
            "byte_size": 10,
            "file_sha256": SHA,
        },
        "validator_executable": {
            "relative_path": "validator.bin",
            "byte_size": 10,
            "file_sha256": OTHER_SHA,
        },
        "classification_binding": {
            "classification_id": "deeplaw-v013-machine-gates-v8",
            "classification_schema_version": "deeplaw.v013-release-gate-classification/v8",
            "classification_sha256": SHA,
        },
        "candidate_binding": {
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "candidate_wheel_sha256": SHA,
            "candidate_sdist_sha256": OTHER_SHA,
        },
        "protocol_binding": {
            "protocol_id": "protocol:v2",
            "protocol_sha256": SHA,
            "frozen": True,
        },
        "threshold_binding": {
            "threshold_id": "thresholds:v2",
            "threshold_sha256": OTHER_SHA,
            "frozen": True,
        },
        "reference_binding": _reference_binding(),
        "corpora": [
            {
                "role": "qualification_holdout",
                "source": "repository_external",
                "sha256": SHA,
                "frozen": True,
            },
            {
                "role": "final_blind",
                "source": "repository_external",
                "sha256": OTHER_SHA,
                "frozen": True,
            },
        ],
        "status": "passed",
        "executions": [
            {
                "run_id": "machine-run-1",
                "workflow_run_id": 3,
                "input_refs": ["input:machine"],
                "evidence_kind": "machine_reference_scorer",
            }
        ],
        "run_ids": ["machine-run-1"],
        "metrics": [
            {
                "metric": "agent_consensus_rate",
                "observed": 1.0,
                "input_refs": ["input:machine"],
            }
        ],
        "hard_failures": [
            {
                "failure_id": "agent_disagreement",
                "count": 0,
                "maximum_allowed": 0,
                "input_refs": ["input:machine"],
            }
        ],
        "inputs": [
            {
                "input_id": "input:machine",
                "relative_path": "typed/machine-reference.json",
                "byte_size": 10,
                "file_sha256": SHA,
                "schema_version": "deeplaw.typed-qualification-evidence/v2",
                "record_sha256": SHA,
                "artifact_kind": "typed-qualification-evidence",
                "evidence_kind": "machine_reference_scorer",
                "derived_record_sha256": OTHER_SHA,
            }
        ],
        "result_sha256": SHA,
    }


def _report() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.commercial-evidence-report/v5",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "report_kind": "v013_machine_provenance_bound_gate_collection",
        "report_id": "report-machine-v5",
        "qualification_run_id": 3,
        "candidate_binding": {
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "candidate_wheel_sha256": SHA,
            "candidate_sdist_sha256": OTHER_SHA,
        },
        "protocol_binding": {
            "protocol_id": "protocol:v2",
            "protocol_sha256": SHA,
            "frozen": True,
        },
        "threshold_binding": {
            "threshold_id": "thresholds:v2",
            "threshold_sha256": OTHER_SHA,
            "frozen": True,
        },
        "reference_binding": _reference_binding(),
        "corpora": [
            {
                "role": "qualification_holdout",
                "source": "repository_external",
                "sha256": SHA,
                "frozen": True,
            },
            {
                "role": "final_blind",
                "source": "repository_external",
                "sha256": OTHER_SHA,
                "frozen": True,
            },
        ],
        "classification_binding": {
            "classification_id": "deeplaw-v013-machine-gates-v8",
            "classification_schema_version": "deeplaw.v013-release-gate-classification/v8",
            "classification_sha256": SHA,
        },
        "gate_results": [
            {
                "gate_id": "agent_gold_isolation",
                "category": "Core",
                "result": {
                    "relative_path": "gates/agent.json",
                    "byte_size": 10,
                    "file_sha256": SHA,
                    "schema_version": "deeplaw.provenance-bound-gate-result/v4",
                    "record_sha256": OTHER_SHA,
                    "artifact_kind": "provenance-bound-gate-result",
                },
            }
        ],
        "machine_qualification_claim_eligible": True,
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
        "report_sha256": SHA,
    }


def _release() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.commercial-release-manifest/v8",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "environment": {
            "platform_system": "Darwin",
            "platform_release": "25.0.0",
            "platform_version": "25.0.0",
            "machine": "arm64",
            "python_implementation": "CPython",
            "python_version": "3.13.0",
            "python_executable_name": "python3",
            "uv_version": "0.8.0",
            "ci": True,
            "github_actions": True,
            "github_runner_os": "macos",
            "github_runner_arch": "arm64",
        },
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": "0.13.0",
            "tag": "v0.13.0",
            "commit": COMMIT,
            "tree": TREE,
        },
        "run_ids": {"candidate_run_id": 1, "evidence_run_id": 2, "qualification_run_id": 3},
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": SHA,
            "sdist_sha256": OTHER_SHA,
            "version": "0.13.0",
        },
        "artifact_binding": {
            "wheel": {"path": "dist/deeplaw.whl", "sha256": SHA, "byte_size": 100},
            "sdist": {"path": "dist/deeplaw.tar.gz", "sha256": OTHER_SHA, "byte_size": 120},
            "retained_manifest_sha256": SHA,
        },
        "external_bindings": {
            "semantic_reference_sha256": SHA,
            "machine_binding_sha256": OTHER_SHA,
            "holdout_sha256": SHA,
            "blind_sha256": OTHER_SHA,
            "agent_roster_sha256": SHA,
            "agent_consensus_sha256": OTHER_SHA,
            "agent_isolation_sha256": SHA,
            "scorer_panel_sha256": SHA,
            "arbiter_sha256": OTHER_SHA,
            "runner_sha256": OTHER_SHA,
            "isolation_sha256": SHA,
        },
        "pre_publish_artifact_gate": {
            "path": "evidence/pre-publish.json",
            "receipt_sha256": SHA,
            "status": "pre_publish_passed",
        },
        "machine_evidence": {
            "report_path": "evidence/commercial.json",
            "report_sha256": SHA,
            "record_sha256": OTHER_SHA,
            "status": "not_executed",
            "hard_zero": False,
            "core_gates_passed": False,
            "reference_binding": _reference_binding(),
        },
        "release_ready": False,
        "public_release_verified": False,
        "post_public_verification": None,
        "machine_qualification_claim_eligible": False,
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
        "record_sha256": SHA,
    }


def test_new_machine_contracts_are_closed_and_schema_valid() -> None:
    names = [
        "semantic-machine-reference.v1.schema.json",
        "candidate-gold-binding-receipt.v2.schema.json",
        "typed-qualification-evidence.v2.schema.json",
        "external-qualification-bundle-manifest.v4.schema.json",
        "provenance-bound-gate-result.v4.schema.json",
        "commercial-evidence-report.v5.schema.json",
        "commercial-release-manifest.v8.schema.json",
    ]
    for name in names:
        schema = _schema(name)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False


def test_machine_reference_is_candidate_independent_and_nonhuman() -> None:
    value = _reference()
    _validate("semantic-machine-reference.v1.schema.json", value)
    candidate_bound = copy.deepcopy(value)
    candidate_bound["candidate_commit"] = COMMIT
    _assert_invalid("semantic-machine-reference.v1.schema.json", candidate_bound)
    human_claim = copy.deepcopy(value)
    human_claim["human_claim_eligible"] = True
    _assert_invalid("semantic-machine-reference.v1.schema.json", human_claim)
    extra = copy.deepcopy(value)
    extra["unregistered_review_flag"] = True
    _assert_invalid("semantic-machine-reference.v1.schema.json", extra)


def test_v2_binding_requires_reference_and_agent_provenance() -> None:
    value = _binding()
    _validate("candidate-gold-binding-receipt.v2.schema.json", value)
    for field in (
        "semantic_reference",
        "agent_roster",
        "agent_consensus",
        "agent_isolation",
        "scorer_panel",
        "arbiter",
    ):
        missing = copy.deepcopy(value)
        del missing[field]
        _assert_invalid("candidate-gold-binding-receipt.v2.schema.json", missing)
    missing_scorer = copy.deepcopy(value)
    del missing_scorer["scorer_panel"]["scorer_b"]
    _assert_invalid("candidate-gold-binding-receipt.v2.schema.json", missing_scorer)
    duplicate_identity = copy.deepcopy(value)
    duplicate_identity["scorer_panel"]["scorer_b"]["identity"] = duplicate_identity[
        "scorer_panel"
    ]["scorer_a"]["identity"]
    _assert_invalid("candidate-gold-binding-receipt.v2.schema.json", duplicate_identity)
    duplicate_arbiter_identity = copy.deepcopy(value)
    duplicate_arbiter_identity["arbiter"]["identity"] = duplicate_arbiter_identity[
        "scorer_panel"
    ]["scorer_a"]["identity"]
    _assert_invalid("candidate-gold-binding-receipt.v2.schema.json", duplicate_arbiter_identity)
    missing_arbiter = copy.deepcopy(value)
    del missing_arbiter["arbiter"]
    _assert_invalid("candidate-gold-binding-receipt.v2.schema.json", missing_arbiter)
    human = copy.deepcopy(value)
    human["human_authenticity"] = "verified"
    _assert_invalid("candidate-gold-binding-receipt.v2.schema.json", human)


def test_typed_v2_preserves_kind_vocabulary_and_adds_machine_reference_scorer() -> None:
    names = [
        "candidate_full_junit",
        "candidate_platform_receipt",
        "host_event_sequence",
        "exact_wheel_execution",
        "legal_rows",
        "wiki_journey_rows",
        "context_capsule_selection_usage",
        "scale_report",
        "retained_supply_chain",
        "machine_reference_scorer",
    ]
    schema = _schema("typed-qualification-evidence.v2.schema.json")
    conditional_refs = {
        item["if"]["properties"]["kind"]["const"]: item["then"]["properties"]["payload"]["$ref"]
        for item in schema["allOf"]
    }
    assert set(conditional_refs) == set(names)
    for kind in names[:-1]:
        _assert_invalid(
            "typed-qualification-evidence.v2.schema.json",
            _typed(kind, payload={}),
        )
        _validate(
            "typed-qualification-evidence.v2.schema.json",
            _typed(kind, _old_payload(kind)),
        )
    _validate(
        "typed-qualification-evidence.v2.schema.json",
        _typed(
            "candidate_full_junit",
            {"source": _source("candidate/junit.xml", "application/xml")},
        ),
    )
    _validate("typed-qualification-evidence.v2.schema.json", _typed())
    machine = _typed()
    assert machine["scorer"] == {
        "identity": machine["arbiter"]["identity"],
        "sha256": machine["arbiter"]["sha256"],
    }
    missing = _typed()
    del missing["payload"]["scorer_b_rows_source"]
    _assert_invalid("typed-qualification-evidence.v2.schema.json", missing)
    missing_arbiter = _typed()
    del missing_arbiter["arbiter"]
    _assert_invalid("typed-qualification-evidence.v2.schema.json", missing_arbiter)
    duplicate_scorer = _typed()
    duplicate_scorer["scorer_panel"]["scorer_b"] = copy.deepcopy(
        duplicate_scorer["scorer_panel"]["scorer_a"]
    )
    _assert_invalid("typed-qualification-evidence.v2.schema.json", duplicate_scorer)
    duplicate_identity = _typed()
    duplicate_identity["scorer_panel"]["scorer_b"]["identity"] = duplicate_identity[
        "scorer_panel"
    ]["scorer_a"]["identity"]
    _assert_invalid("typed-qualification-evidence.v2.schema.json", duplicate_identity)
    duplicate_arbiter_identity = _typed()
    duplicate_arbiter_identity["arbiter"]["identity"] = duplicate_arbiter_identity[
        "scorer_panel"
    ]["scorer_a"]["identity"]
    _assert_invalid("typed-qualification-evidence.v2.schema.json", duplicate_arbiter_identity)
    missing_scorer = _typed()
    del missing_scorer["scorer_panel"]["scorer_b"]
    _assert_invalid("typed-qualification-evidence.v2.schema.json", missing_scorer)
    duplicate_process = _typed()
    duplicate_process["payload"]["process_identity"]["scorer_b_process_id"] = (
        "scorer-a:process"
    )
    _assert_invalid("typed-qualification-evidence.v2.schema.json", duplicate_process)
    missing_top_level_scorer = _typed("candidate_full_junit", _old_payload("candidate_full_junit"))
    del missing_top_level_scorer["scorer"]
    _assert_invalid("typed-qualification-evidence.v2.schema.json", missing_top_level_scorer)
    human_kind = _typed()
    human_kind["kind"] = "human_gold_scorer"
    _assert_invalid("typed-qualification-evidence.v2.schema.json", human_kind)
    old = _typed()
    old["schema_version"] = "deeplaw.typed-qualification-evidence/v1"
    _assert_invalid("typed-qualification-evidence.v2.schema.json", old)


def test_machine_bundle_excludes_trusted_human_descriptor_and_old_bundle_rejects_it() -> None:
    value = _bundle()
    _validate("external-qualification-bundle-manifest.v4.schema.json", value)
    singular_scorer = copy.deepcopy(value)
    singular_scorer["external_inputs"]["scorer_sha256"] = SHA
    _assert_invalid("external-qualification-bundle-manifest.v4.schema.json", singular_scorer)
    missing_arbiter = copy.deepcopy(value)
    del missing_arbiter["external_inputs"]["arbiter_sha256"]
    _assert_invalid("external-qualification-bundle-manifest.v4.schema.json", missing_arbiter)
    human_descriptor = copy.deepcopy(value)
    human_descriptor["trusted_human_approver_descriptor_sha256"] = SHA
    _assert_invalid("external-qualification-bundle-manifest.v4.schema.json", human_descriptor)
    old_consumer = copy.deepcopy(value)
    old_consumer["schema_version"] = "deeplaw.external-qualification-bundle-manifest/v3"
    _assert_invalid("external-qualification-bundle-manifest.v3.schema.json", old_consumer)


def test_v4_gate_and_v5_report_bind_machine_provenance() -> None:
    gate = _gate_result()
    _validate("provenance-bound-gate-result.v4.schema.json", gate)
    report = _report()
    _validate("commercial-evidence-report.v5.schema.json", report)
    bad_gate = copy.deepcopy(gate)
    bad_gate["reference_provenance"] = "human_attested"
    _assert_invalid("provenance-bound-gate-result.v4.schema.json", bad_gate)
    bad_report = copy.deepcopy(report)
    bad_report["human_attested_claim_eligible"] = True
    _assert_invalid("commercial-evidence-report.v5.schema.json", bad_report)
    missing_panel = copy.deepcopy(report)
    del missing_panel["reference_binding"]["scorer_panel_sha256"]
    _assert_invalid("commercial-evidence-report.v5.schema.json", missing_panel)
    singular_report = copy.deepcopy(report)
    singular_report["reference_binding"]["scorer_sha256"] = SHA
    _assert_invalid("commercial-evidence-report.v5.schema.json", singular_report)


def test_v8_separates_release_ready_public_verification_and_claim_classes() -> None:
    value = _release()
    _validate("commercial-release-manifest.v8.schema.json", value)
    ready = copy.deepcopy(value)
    ready["release_ready"] = True
    ready["machine_qualification_claim_eligible"] = True
    ready["machine_evidence"] = {
        **ready["machine_evidence"],
        "status": "passed",
        "hard_zero": True,
        "core_gates_passed": True,
    }
    _validate("commercial-release-manifest.v8.schema.json", ready)
    bad_ready = copy.deepcopy(ready)
    bad_ready["human_attested_claim_eligible"] = True
    _assert_invalid("commercial-release-manifest.v8.schema.json", bad_ready)
    bad_public = copy.deepcopy(value)
    bad_public["public_release_verified"] = True
    _assert_invalid("commercial-release-manifest.v8.schema.json", bad_public)
    missing_panel = copy.deepcopy(value)
    del missing_panel["external_bindings"]["scorer_panel_sha256"]
    _assert_invalid("commercial-release-manifest.v8.schema.json", missing_panel)
    singular_release = copy.deepcopy(value)
    singular_release["external_bindings"]["scorer_sha256"] = SHA
    _assert_invalid("commercial-release-manifest.v8.schema.json", singular_release)


def test_old_consumers_fail_closed_on_machine_contract_versions() -> None:
    binding = _binding()
    _assert_invalid("candidate-gold-binding-receipt.v1.schema.json", binding)
    typed = _typed()
    _assert_invalid("typed-qualification-evidence.v1.schema.json", typed)
    gate = _gate_result()
    _assert_invalid("provenance-bound-gate-result.v3.schema.json", gate)
    report = _report()
    _assert_invalid("commercial-evidence-report.v4.schema.json", report)
    release = _release()
    _assert_invalid("commercial-release-manifest.v7.schema.json", release)

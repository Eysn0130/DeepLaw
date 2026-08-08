from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/v013-qualification-protocol.v1.schema.json"
PROTOCOL_PATH = REPOSITORY / "benchmarks/v013/qualification-protocol-v1.json"
SHA_PATH = REPOSITORY / "benchmarks/v013/qualification-protocol-v1.sha256"


def _load() -> tuple[dict, dict]:
    return (
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        json.loads(PROTOCOL_PATH.read_text(encoding="utf-8")),
    )


def test_protocol_schema_and_candidate_fixture_are_closed_and_valid() -> None:
    schema, protocol = _load()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(protocol)

    assert protocol["freeze_status"] == "protocol_frozen_candidate_binding_pending"
    binding = protocol["candidate_binding"]
    assert binding["package_version"] == "0.12.0"
    assert binding["wheel_filename"] is None
    assert binding["wheel_sha256"] is None
    assert binding["source_commit"] is None
    assert binding["qualification_holdout_sha256"] is None
    assert binding["final_blind_holdout_sha256"] is None


def test_sidecar_hash_binds_exact_json_bytes() -> None:
    expected_digest, expected_name = SHA_PATH.read_text(encoding="utf-8").strip().split()
    assert expected_name == PROTOCOL_PATH.name
    assert re.fullmatch(r"[0-9a-f]{64}", expected_digest)
    digest = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    assert digest == expected_digest


def test_development_qualification_and_final_layers_are_mutually_exclusive() -> None:
    _, protocol = _load()
    layers = protocol["data_layers"]
    assert set(layers) == {"development", "qualification_holdout", "final_blind"}
    assert {item["layer_kind"] for item in layers.values()} == {
        "development",
        "qualification_holdout",
        "final_blind",
    }
    assert layers["development"]["residency"] == "repository_or_public_synthetic"
    for key in ("qualification_holdout", "final_blind"):
        layer = layers[key]
        assert layer["residency"] == "repo_external"
        assert layer["status"] == "not_bound"
        assert layer["source_visibility"] == "compiler_visible_source_only"
        assert layer["gold_visibility"] == "evaluator_only"
        assert layer["scorer_visibility"] == "evaluator_only"
        assert layer["corpus_sha256"] is None
        assert layer["gold_sha256"] is None
    assert (
        layers["final_blind"]["failure_policy"]
        == "failure_then_repair_requires_new_unseen_holdout"
    )


def test_compiler_and_evaluator_capabilities_are_isolated() -> None:
    _, protocol = _load()
    compiler = protocol["compiler_contract"]
    evaluator = protocol["evaluator_contract"]
    assert compiler["execution_artifact"] == "exact_candidate_wheel_only"
    assert compiler["allowed_inputs"] == [
        "candidate_wheel",
        "selected_layer_source_corpus",
        "explicit_deeplaw_mcp",
    ]
    assert set(compiler["forbidden_inputs"]) >= {
        "repository_source_tree",
        "qualification_gold",
        "final_blind_gold",
        "scorer",
        "expected_ids",
        "ambient_credentials",
    }
    assert compiler["repository_source_access"] is False
    assert compiler["gold_access"] is False
    assert compiler["scorer_access"] is False
    assert compiler["expected_identity_access"] is False
    assert compiler["ambient_secret_access"] is False
    assert evaluator["allowed_inputs"] == ["compiled_output", "corresponding_gold"]
    assert evaluator["read_only_inputs"] is True
    assert evaluator["repository_source_access"] is False
    assert evaluator["compiler_process_access"] is False
    assert evaluator["candidate_mutation"] is False
    assert evaluator["output_mutation"] is False


def test_protocol_contains_no_local_paths_or_expected_identity_material() -> None:
    _, protocol = _load()
    path_like = re.compile(r"(?:^|/)(?:Users|home|src|tests|benchmarks|contracts)(?:/|$)")
    forbidden_keys = {"expected_id", "expected_ids", "gold_ids", "gold_labels"}

    def walk(value: object, key: str | None = None) -> None:
        if key in forbidden_keys:
            raise AssertionError(f"expected identity material key leaked: {key}")
        if isinstance(value, str):
            assert not os.path.isabs(value), value
            assert ".." not in value.split("/"), value
            assert not path_like.search(value), value
        elif isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, child_key)
        elif isinstance(value, list):
            for child in value:
                walk(child, key)

    walk(protocol)
    assert "expected_ids" in protocol["compiler_contract"]["forbidden_inputs"]


def test_hard_failures_are_zero_and_external_gates_are_not_executed() -> None:
    _, protocol = _load()
    required_zero_failures = {
        "false_authority_admission",
        "invalid_quote_locator",
        "wrong_version_primary_evidence",
        "secret_exposure",
    }
    failures = protocol["hard_failures"]
    assert required_zero_failures <= {item["failure_id"] for item in failures}
    assert all(
        item["maximum_allowed"] == 0
        and item["effect"] == "qualification_gate_failure"
        and item["measurement_status"] == "not_executed"
        for item in failures
    )
    gates = protocol["external_gates"]
    assert gates
    assert all(item["status"] == "not_executed" for item in gates)
    assert all(item["result"] is None for item in gates)


def test_controls_and_metric_thresholds_are_frozen_without_results() -> None:
    _, protocol = _load()
    controls = protocol["frozen_controls"]
    assert controls["thresholds_frozen_before_final_result"] is True
    assert controls["budgets_frozen_before_final_result"] is True
    assert controls["failure_conditions_frozen_before_final_result"] is True
    assert controls["provider_payload_bytes"] == 65536
    assert controls["statement_candidate_limit"] == 512
    assert controls["graph_hops"] == {"minimum": 0, "maximum": 2}
    assert controls["graph_admission_limit"] == 500
    assert controls["graph_scan_limit"] == 5000
    assert controls["rss_request_count"] == 10000
    assert controls["rss_max_relative_growth"] == pytest.approx(0.1)
    assert controls["concurrent_readers"] == 8
    assert controls["query_trace_policy"] == {
        "ttl_seconds": 900,
        "max_entries": 16,
        "max_bytes": 1048576,
        "hash": "sha256_integrity_recomputed_on_read",
        "owner_delete": True,
        "plaintext_default": False,
    }

    metrics = protocol["metrics"]
    metric_ids = [metric["metric_id"] for metric in metrics]
    assert len(metric_ids) == len(set(metric_ids))
    assert {
        "retrieval_recall_at_k",
        "retrieval_precision_at_k",
        "retrieval_mrr",
        "retrieval_ndcg",
        "context_useful_context_recall",
        "context_relevant_chars_context_chars",
        "context_redundancy_rate",
        "context_false_suppression_rate",
        "context_duty_coverage",
        "context_duplicate_evidence_rate",
        "context_distractor_induced_answer_delta",
        "context_token_savings",
        "performance_storage_bytes_100k",
        "legal_definition_exception_proviso_cross_reference_recall",
        "legal_temporal_correctness",
        "legal_wrong_version_inclusion",
        "legal_citation_validity",
    } <= set(metric_ids)
    assert all(
        metric["measurement_status"] == "not_executed"
        and metric["zero_denominator_status"] == "not_executed"
        and metric["acceptance"]["mode"] != "report_only"
        for metric in metrics
    )


def test_claim_policy_fails_closed() -> None:
    _, protocol = _load()
    claim = protocol["claim_policy"]
    assert claim["quality_protocol_eligible"] is False
    assert claim["competitive_claim_eligible"] is False
    assert claim["holdout_contamination_claim_eligible"] is False
    assert claim["release_disposition"] == "not_released_source_candidate"
    assert {"perfect", "fully verified", "SOTA", "RC", "GA"} <= set(
        claim["forbidden_claims"]
    )

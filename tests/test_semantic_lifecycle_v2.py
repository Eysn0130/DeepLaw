from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

import benchmarks.semantic.run_deterministic_lifecycle as semantic_lifecycle
from benchmarks.semantic import run_query_suite as semantic_query_suite

REPOSITORY = Path(__file__).resolve().parents[1]


def _sha(fill: str = "a") -> str:
    return fill * 64


def _source_run(index: int, *, semantic_status: str) -> dict:
    suffix = f"{index:024x}"
    return {
        "schema_version": "deeplaw.deterministic-semantic-source-run/v2",
        "source_key": f"source-{index}",
        "source_revision_id": f"sourcerev_{suffix}",
        "compilation_run_id": f"compilationrun_{suffix}",
        "compiler_profile_version": "3",
        "packet_count": 1,
        "packet_ids": [f"packet_{suffix}"],
        "observation_count": 1,
        "semantic_status": semantic_status,
        "applicability_policy_sha256": _sha("b"),
        "applicability_digest": _sha("c"),
        "unresolved_duties": [] if semantic_status == "complete" else ["key_claims"],
        "transaction_status": "succeeded",
        "quality_receipt_sha256": _sha("d"),
        "source_summary_revision_id": f"knowledgerev_{suffix}",
        "validation_sha256": _sha("e"),
        "receipt_sha256": _sha("f"),
        "projection_manifest_sha256": _sha("0"),
        "verification_valid": True,
        "compilation_latency_ms": 1,
    }


def _lifecycle_report() -> dict:
    runs = [_source_run(1, semantic_status="complete")]
    runs.extend(_source_run(index, semantic_status="partial") for index in range(2, 13))
    return {
        "schema_version": "deeplaw.deterministic-semantic-lifecycle/v2",
        "report_id": "semanticdeterministic_0123456789abcdef01234567",
        "status": "passed",
        "compiler_profile_version": "3",
        "semantic_status": "partial",
        "semantic_status_counts": {
            "complete": 1,
            "partial": 11,
            "blocked": 0,
            "unknown": 0,
            "total": 12,
        },
        "binding": {
            "commit": "1" * 40,
            "tree": "2" * 40,
            "package_version": "0.12.0",
            "lock_sha256": _sha("1"),
            "pyproject_sha256": _sha("2"),
            "contracts_inventory_sha256": _sha("3"),
            "migrations_inventory_sha256": _sha("4"),
            "worktree_clean": True,
        },
        "gold_id": "semanticgold_0123456789abcdef01234567",
        "gold_status": "machine_review_pending",
        "corpus_id": "semanticcorpus_0123456789abcdef01234567",
        "fixture_manifest_sha256": _sha("5"),
        "agent_identity": "deeplaw-deterministic-gold-agent",
        "model_identity": None,
        "network_policy": "offline",
        "external_model_execution": "not_executed",
        "first_party_command_sha256": _sha("6"),
        "baseline_query_state": {
            "snapshot_sha256": _sha("7"),
            "audit_head": _sha("8"),
            "verified": True,
        },
        "runs": runs,
        "transitions": [
            {
                "operation": "activate_successor",
                "status": "passed",
                "predecessor_source_revision_id": "sourcerev_" + "1" * 24,
                "successor_source_revision_id": "sourcerev_" + "2" * 24,
                "review_receipt_sha256": _sha("9"),
                "freshness_report_sha256": _sha("a"),
            },
            {
                "operation": "withdraw_source",
                "status": "passed",
                "source_revision_id": "sourcerev_" + "3" * 24,
                "removal_audit_head": _sha("b"),
                "freshness_report_sha256": _sha("c"),
            },
        ],
        "vault_verification_valid": True,
        "metrics": {
            "first_compilation_latency_ms": 1,
            "baseline_compilation_latency_ms": 11,
            "incremental_refresh_latency_ms": 1,
            "successor_compilation_latency_ms": 1,
            "withdrawal_refresh_latency_ms": 1,
            "snapshot_restore_latency_ms": 1,
            "transaction_success_rate": 1.0,
            "transition_success_rate": 1.0,
        },
        "elapsed_ms": 1,
        "recorded_at": "2026-08-09T00:00:00Z",
        "formal_release_evidence_ready": False,
        "competitive_claim_eligible": False,
    }


def test_v2_schema_accepts_truthful_partial_lifecycle() -> None:
    report = _lifecycle_report()
    schema = json.loads(
        (
            REPOSITORY / "contracts/deterministic-semantic-lifecycle.v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    semantic_query_suite._validate_deterministic_compiler_report(report)


def test_v2_schema_rejects_unversioned_nested_source_run() -> None:
    report = _lifecycle_report()
    report["runs"][0].pop("schema_version")
    schema = json.loads(
        (
            REPOSITORY / "contracts/deterministic-semantic-lifecycle.v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    with pytest.raises(ValidationError):
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(report)


def test_lifecycle_aggregation_uses_fixed_fail_closed_priority() -> None:
    runs = [
        {"semantic_status": "complete"},
        {"semantic_status": "partial"},
        {"semantic_status": "unknown"},
        {"semantic_status": "blocked"},
    ]
    aggregate, counts = semantic_lifecycle._semantic_status_summary(runs)
    assert aggregate == "blocked"
    assert counts == {
        "complete": 1,
        "partial": 1,
        "blocked": 1,
        "unknown": 1,
        "total": 4,
    }


@pytest.mark.parametrize("field", ["semantic_status", "semantic_status_counts"])
def test_v2_rejects_forged_aggregate_or_status(field: str) -> None:
    report = _lifecycle_report()
    if field == "semantic_status":
        report[field] = "complete"
    else:
        report[field] = {**report[field], "partial": 10, "complete": 2}
    with pytest.raises(ValueError, match=r"aggregate status|status counts"):
        semantic_query_suite._validate_deterministic_compiler_report(report)


def test_v1_contract_remains_complete_only_and_rejects_partial_bytes() -> None:
    source_schema = json.loads(
        (
            REPOSITORY / "contracts/deterministic-semantic-source-run.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    lifecycle_schema = json.loads(
        (
            REPOSITORY / "contracts/deterministic-semantic-lifecycle.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert source_schema["properties"]["semantic_status"] == {"const": "complete"}
    assert lifecycle_schema["$defs"]["run"]["properties"]["semantic_status"] == {
        "const": "complete"
    }

    legacy = copy.deepcopy(_lifecycle_report())
    legacy.pop("compiler_profile_version")
    legacy.pop("semantic_status")
    legacy.pop("semantic_status_counts")
    legacy["schema_version"] = "deeplaw.deterministic-semantic-lifecycle/v1"
    for run in legacy["runs"]:
        run.pop("schema_version")
        run.pop("compiler_profile_version")
        run.pop("applicability_policy_sha256")
        run.pop("applicability_digest")
        run.pop("unresolved_duties")
        run.pop("compilation_latency_ms")
        run["semantic_status"] = "partial"
    with pytest.raises(ValidationError):
        Draft202012Validator(
            lifecycle_schema, format_checker=FormatChecker()
        ).validate(legacy)

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from benchmarks.living_wiki.compare_quality import ComparisonError, compare


def _report(*, role: str, commit: str, version: str) -> dict[str, Any]:
    retrieval = {
        "recall_at_k": 0.9,
        "precision_at_k": 0.3,
        "mrr": 0.9,
        "ndcg": 0.9,
        "citation_validity": 1.0,
        "claim_evidence_binding_accuracy": 1.0,
        "source_coverage": 0.8,
        "stale_selection_prevention": 1.0,
        "evidence_attachment_rate": 1.0,
        "repeated_query_reuse_rate": 1.0,
        "context_bytes_saved_vs_raw_ratio": 0.8,
        "cold_latency_ms_p50": 100.0,
        "cold_latency_ms_p95": 120.0,
        "warm_latency_ms_p50": 80.0,
        "warm_latency_ms_p95": 90.0,
        "gate_checks": {"deterministic": True},
    }
    return {
        "schema_version": "deeplaw.living-wiki-quality-report/v1",
        "suite": {
            "suite_id": "deeplaw-living-wiki-cli-quality-v1",
            "suite_sha256": "1" * 64,
            "runner_sha256": "2" * 64,
        },
        "candidate": {
            "role": role,
            "commit": commit,
            "version": version,
            "artifact_sha256": "3" * 64,
        },
        "corpus": {
            "inventory_sha256": "4" * 64,
            "records": [
                {
                    "label": "public-fixture",
                    "immutable_bytes_sha256": "6" * 64,
                    "media_type": "text/markdown",
                }
            ],
        },
        "environment": {
            "platform_system": "Linux",
            "platform_release": "test",
            "machine": "x86_64",
            "processor": "test",
            "logical_cpu_count": 4,
            "python_version": "3.12.0",
            "sqlite_version": "3.45.0",
            "network_policy": "offline_no_acquisition",
        },
        "configuration": {"scope": "project"},
        "compilation": {
            "first_compilation_latency_ms": 500.0,
            "incremental_refresh_latency_ms": 200.0,
            "rebuild_latency_ms": 300.0,
            "destructive_rebuild_latency_ms": 400.0,
        },
        "retrieval": retrieval,
        "security": {
            "unauthorized_disclosure": 0,
            "silent_fallback": 0,
            "stale_prohibited_selection": 0,
            "invalid_official_citation": 0,
            "provider_hard_limit_violation": 0,
            "authority_elevation_by_ranking_or_model": 0,
            "unauthorized_write_rejected": True,
        },
        "failures": [],
        "passed": True,
        "competitive_claim_eligible": False,
        "record_sha256": "5" * 64,
    }


def test_same_condition_quality_comparison_binds_no_regression() -> None:
    baseline = _report(role="baseline", commit="a" * 40, version="0.10.0")
    candidate = _report(role="fresh_wheel", commit="b" * 40, version="0.11.0")

    result = compare(baseline, candidate)

    assert result["passed"] is True
    assert result["quality_regression"] is False
    assert result["performance_regression"] is False
    assert len(result["functional_comparisons"]) == 11
    assert len(result["performance_comparisons"]) == 7


def test_quality_comparison_rejects_functional_or_environment_drift() -> None:
    baseline = _report(role="baseline", commit="a" * 40, version="0.10.0")
    candidate = _report(role="fresh_wheel", commit="b" * 40, version="0.11.0")
    regressed = deepcopy(candidate)
    regressed["retrieval"]["recall_at_k"] = 0.89
    with pytest.raises(ComparisonError, match="functional quality regressed"):
        compare(baseline, regressed)

    different_environment = deepcopy(candidate)
    different_environment["environment"]["machine"] = "arm64"
    with pytest.raises(ComparisonError, match="same frozen quality experiment"):
        compare(baseline, different_environment)

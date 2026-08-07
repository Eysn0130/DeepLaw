from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.v013.scale_performance import (
    OPERATION_INVENTORY,
    SCHEMA_VERSION,
    build_parser,
    build_scale_performance_report,
    verify_scale_performance_report,
)

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/v013-scale-performance-report.v1.schema.json"


def test_scale_report_schema_and_closed_inventory_use_a_real_temporary_vault() -> None:
    report = build_scale_performance_report(
        scales=(1_000,),
        query_runs=1,
        warmup_runs=0,
        rss_requests=10_000,
    )
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["claim_eligible"] is False
    assert report["release_gate_passed"] is False
    assert report["profile"] == "construction_diagnostic"
    assert report["configuration"]["projection_profile"] == "standard"
    assert report["configuration"]["query_plan_version"] == "6"
    assert report["configuration"]["semantic_profile"] == "not_executed"
    assert report["operation_inventory"] == list(OPERATION_INVENTORY)
    scale = report["scale_reports"][0]
    assert scale["scale"] == 1_000
    assert scale["object_count"] == 1_000
    assert [item["operation"] for item in scale["operations"]] == list(OPERATION_INVENTORY)
    assert scale["operations"][0]["operation"] == "exact_get"
    assert scale["operations"][0]["sample_count"] >= 1
    assert any(item["operation"] == "compiled_first" for item in scale["operations"])
    assert any(item["operation"] == "provider_payload_bytes" for item in scale["operations"])
    assert any(item["operation"] == "verify" for item in scale["operations"])
    mcp_operations = {
        item["operation"]: item
        for item in scale["operations"]
        if item["operation"] in {"mcp_cold", "mcp_warm"}
    }
    assert set(mcp_operations) == {"mcp_cold", "mcp_warm"}
    for item in mcp_operations.values():
        assert item["status"] == "executed"
        assert item["sample_count"] == 1
        assert item["run_count"] == 1
    for item in scale["operations"]:
        assert "p99" in item["latency_ms"]
        assert item["latency_ms"]["p99"] == item["latency_ms"]["p95"]
        if item["status"] == "not_executed":
            assert item["reason"]
    assert verify_scale_performance_report(report)["valid"] is True


def test_expensive_scales_are_not_silently_shrunk_and_are_fail_closed() -> None:
    report = build_scale_performance_report(
        scales=(10_000, 100_000),
        query_runs=1,
        warmup_runs=0,
        rss_requests=10_000,
        execute_expensive=False,
    )
    assert [item["scale"] for item in report["scale_reports"]] == [10_000, 100_000]
    for scale in report["scale_reports"]:
        assert scale["object_count"] == scale["scale"]
        assert scale["fixture_status"] == "not_executed"
        assert all(item["status"] == "not_executed" for item in scale["operations"])
        assert "--execute-expensive" in scale["fixture_reason"]
    assert report["overall"]["release_gate_passed"] is False


def test_invalid_scale_and_run_arguments_fail_closed() -> None:
    with pytest.raises(ValueError):
        build_scale_performance_report(scales=(999,))
    with pytest.raises(ValueError):
        build_scale_performance_report(scales=(1_000,), query_runs=0)
    with pytest.raises(ValueError):
        build_scale_performance_report(scales=(1_000,), warmup_runs=-1)

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--output", "report.json", "--scale", "999"])


def test_report_verifier_rejects_tamper_and_local_paths() -> None:
    report = build_scale_performance_report(
        scales=(10_000,), query_runs=1, warmup_runs=0, rss_requests=10_000
    )
    tampered = dict(report)
    tampered["claim_eligible"] = True
    assert verify_scale_performance_report(tampered)["valid"] is False

    path_tampered = dict(report)
    path_tampered["limitations"] = ["/Users/private/source.md"]
    assert verify_scale_performance_report(path_tampered)["valid"] is False

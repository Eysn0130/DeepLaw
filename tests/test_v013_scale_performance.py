from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.v013.scale_performance import (
    OPERATION_INVENTORY,
    SCHEMA_VERSION,
    _filesystem_metadata,
    _Fixture,
    _fixture_operation_runners,
    _full_vault_scan_monitor,
    _synthetic_source_text,
    build_parser,
    build_scale_performance_report,
    verify_scale_performance_report,
)

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/v013-scale-performance-report.v1.schema.json"


def test_filesystem_probe_is_honest_when_statvfs_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "statvfs", raising=False)

    metadata = _filesystem_metadata(tmp_path)

    assert metadata == {
        "kind": "unknown",
        "block_size": None,
        "reason": "filesystem statistics probe failed",
    }


def test_exact_100k_source_fixture_stays_within_line_count_ceiling() -> None:
    source = _synthetic_source_text(100_000)

    assert len(source.splitlines()) == 200_000
    assert source.startswith("# Synthetic object 000000\n")
    assert source.endswith("for construction diagnostics.\n")


def test_full_vault_scan_monitor_distinguishes_owner_subtree_traversal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    owner_subtree = root / ".deeplaw" / "derived" / "staging"
    owner_subtree.mkdir(parents=True)

    with _full_vault_scan_monitor(root) as bounded:
        list(owner_subtree.rglob("*"))
    assert bounded["full_filesystem_scan"] is False

    with _full_vault_scan_monitor(root) as broad:
        list(root.rglob("*"))
    assert broad["full_filesystem_scan"] is True


def test_python_capsule_verify_reports_observed_warm_full_verify_calls(
    tmp_path: Path,
) -> None:
    fixture = _Fixture(tmp_path / "fixture", 1_000)
    fixture.create()
    try:
        result = _fixture_operation_runners(fixture)["verify"]()
    finally:
        fixture.close()

    assert result["valid"] is True
    assert result["per_request_full_verify"] is False


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


def test_scale_report_observes_source_update_cache_and_projection_equivalence() -> None:
    report = build_scale_performance_report(
        scales=(1_000,),
        query_runs=1,
        warmup_runs=0,
        rss_requests=10_000,
    )
    operations = {
        item["operation"]: item for item in report["scale_reports"][0]["operations"]
    }

    source_update = operations["source_update"]
    source_details = source_update["measurement"]["source_update"]
    assert source_update["status"] == "executed", (
        source_update["reason"],
        source_update["measurement"],
    )
    assert source_details["source_revision_distinct"] is True
    assert source_details["canonical_source_key_stable"] is True
    assert source_details["old_canonical_source_key"] == (
        source_details["new_canonical_source_key"]
    )
    assert source_details["audit_head_changed"] is True
    assert source_details["old_source_revision_id"] != source_details["new_source_revision_id"]

    cache = operations["cache_invalidation_after_source_update"]
    cache_details = cache["measurement"]["cache_invalidation"]
    assert cache["status"] == "pass"
    assert cache_details["exact_bounded_result_match"] is True
    assert cache_details["exact_identity_match"] is True
    assert cache_details["stale_cache_served"] is False
    assert cache_details["old_source_revision_in_warm_after"] is False
    assert cache_details["old_source_revision_in_fresh_after"] is False

    incremental = operations["incremental_projection"]
    assert incremental["status"] == "executed"

    equivalence = operations["projection_equivalence"]
    equivalence_details = equivalence["measurement"]["projection_equivalence"]
    assert equivalence["status"] == "pass"
    assert equivalence_details["exact"] is True
    assert equivalence_details["same_canonical_input"] is True
    assert equivalence_details["full_rebuild_from_empty_projection"] is True
    assert equivalence_details["incremental"] == equivalence_details["full_rebuild"]
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "/tmp/" not in serialized
    assert "/Users/" not in serialized
    assert verify_scale_performance_report(report)["valid"] is True

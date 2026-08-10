"""Small, deterministic tests for the claim-ineligible Query/Graph scale runner."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from benchmarks.v013.query_graph_scale import SEED, _source_text, build_report, verify_report
from deeplaw.util import canonical_json, sha256_bytes


def test_exact_100k_statement_source_stays_within_fragment_and_grant_bounds() -> None:
    source = _source_text(100_000, seed=SEED)
    sections = ["# " + item for item in source.split("\n# ")]

    assert len(sections) == 400
    assert max(len(section) for section in sections) < 12_000
    assert sum(
        1
        for line in source.splitlines()
        if line and not line.startswith("#")
    ) == 100_000


def test_smoke_report_schema_hash_and_tail_recall(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    report = build_report(scales=(101,), workspace=workspace)

    assert verify_report(report) == {"valid": True, "errors": []}
    assert report["claim_eligible"] is False
    assert report["competitive_claim_eligible"] is False
    assert report["release_gate_passed"] is False
    assert report["configuration"]["seed"] > 0
    assert report["configuration"]["graph_hops"] == [0, 1, 2]
    assert report["configuration"]["statements_per_revision"] == 250
    assert report["configuration"]["packet_max_fragments"] == 1
    assert report["candidate"]["source_hashes"]
    assert "src/deeplaw/projection/builder.py" in report["candidate"]["source_hashes"]

    lane = report["scale_reports"][0]
    assert lane["status"] == "executed"
    assert lane["construction"] == "public_profile_v3_compilation"
    assert lane["derived_rebuild"] == {"status": "executed", "reason": None}
    fixture = lane["fixture"]
    assert fixture["source_revision_count"] == len(fixture["source_revision_ids"])
    assert fixture["compilation_run_count"] == len(fixture["compilation_run_ids"])
    assert fixture["knowledge_revision_count"] == len(fixture["knowledge_revision_ids"])
    assert fixture["source_revision_id"] == fixture["source_revision_ids"][0]
    assert fixture["compilation_run_id"] == fixture["compilation_run_ids"][0]
    assert lane["statement"]["statement_count"] == 101
    assert lane["statement"]["target_positions"] == [0, 50, 100]
    assert lane["statement"]["tail_recall"] is True
    assert lane["statement"]["position_independent"] is True
    assert lane["statement"]["candidate_bound"] == 512
    assert lane["statement"]["legacy_global_prefix_scan_removed"] is True
    assert lane["statement"]["provider_hard_limit_bytes"] == 65_536
    assert lane["statement"]["runtime_mode"] == "persistent_verified_snapshot"
    assert lane["statement"]["runtime_full_verify_count"] == 1
    assert lane["statement"]["per_request_full_verify"] is False
    assert lane["statement"]["runtime_startup_ms"] >= 0
    assert all(item["selected"] for item in lane["statement"]["targets"])
    assert all(item["provider_bytes"] <= 65_536 for item in lane["statement"]["targets"])

    shard_paths = sorted((workspace / "scale-101" / "wiki" / "statements").glob("*.md"))
    assert len(shard_paths) == 2
    assert all(path.stat().st_size <= 256 * 1024 for path in shard_paths)
    shard_text = "\n".join(path.read_text(encoding="utf-8") for path in shard_paths)
    assert shard_text.count('<a id="statement-') == 101

    manifest = json.loads(
        (workspace / "scale-101" / ".deeplaw/derived/wiki/v3/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    from deeplaw.wiki import load_page_registry

    registry = load_page_registry(workspace / "scale-101", manifest)
    statement_shard_records = [
        record
        for record in registry["records"]
        if record["canonical_page_path"].startswith("wiki/statements/")
    ]
    assert len(statement_shard_records) == 2
    assert sum(len(record["anchors"]) for record in statement_shard_records) == 101

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert re.search(r"(?:/Users/|/home/|/tmp/|/private/var/|[A-Za-z]:[\\/])", serialized) is None


def test_executed_fixture_provenance_digest_is_recomputed(tmp_path: Path) -> None:
    report = build_report(scales=(101,), workspace=tmp_path / "workspace")
    tampered = deepcopy(report)
    tampered["scale_reports"][0]["fixture"]["source_revision_ids_sha256"] = "0" * 64
    body = dict(tampered)
    body.pop("report_sha256")
    tampered["report_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))

    validation = verify_report(tampered)

    assert validation["valid"] is False
    assert "executed fixture source_revision_ids_sha256 mismatch" in validation["errors"]


def test_expensive_lanes_never_substitute_smoke_without_explicit_flag(tmp_path: Path) -> None:
    report = build_report(scales=(10_000, 100_000), workspace=tmp_path / "workspace")

    assert verify_report(report) == {"valid": True, "errors": []}
    assert report["overall"]["release_gate_passed"] is False
    assert report["overall"]["not_executed_count"] == 2
    for lane in report["scale_reports"]:
        assert lane["status"] == "not_executed"
        assert "--execute-expensive" in lane["reason"]
        assert lane["statement"]["statement_count"] == 0
        assert lane["statement"]["tail_recall"] is None
        assert lane["graph"]["status"] == "not_executed"
        assert lane["derived_rebuild"]["status"] == "not_executed"


def test_graph_checks_hops_and_fail_closed_truncation_evidence(tmp_path: Path) -> None:
    report = build_report(scales=(101,), workspace=tmp_path / "workspace")
    graph = report["scale_reports"][0]["graph"]

    assert graph["status"] == "executed"
    assert graph["executed_relation_count"] >= 8
    assert graph["checks"] == {
        "tail_edge_retrieved": True,
        "hub_edges": True,
        "deep_chain": True,
        "cycle": True,
        "contradiction": True,
        "temporal_before_excluded": True,
        "temporal_after_included": True,
        "dangling_relations_absent": True,
        "dangling_relation_rejected": True,
        "self_loop_rejected": True,
        "graph_hops_zero_seed_only": True,
        "graph_hops_one_direct": True,
        "graph_hops_two_deep": True,
        "verify_valid": True,
    }
    assert {item["effective"] for item in graph["graph_hops"].values()} == {0, 1, 2}

    truncation = graph["truncation"]
    assert truncation["admitted_bound"] == 500
    assert truncation["scanned_bound"] == 5_000
    assert truncation["status"] == "not_executed"
    assert truncation["gap_or_receipt_evidence"] is True
    assert truncation["candidate_scan_truncated"] is False
    assert truncation["selection_truncated"] is True
    assert len(truncation["gaps"]) == 1
    assert "selection" in truncation["gaps"][0]
    assert "expensive lane" in truncation["reason"]


def test_derived_rebuild_failure_cannot_be_hidden_by_unexecuted_graph_lane(
    tmp_path: Path,
) -> None:
    with patch(
        "benchmarks.v013.query_graph_scale.AutonomousKnowledgeStore.rebuild_derived",
        side_effect=RuntimeError("derived failure witness"),
    ):
        report = build_report(scales=(101,), workspace=tmp_path / "workspace")

    lane = report["scale_reports"][0]
    assert lane["status"] == "fail"
    assert lane["derived_rebuild"] == {
        "status": "fail",
        "reason": "RuntimeError: derived failure witness",
    }
    assert report["overall"]["fail_count"] == 1

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from benchmarks.v013.run_upstream_product_closure import (
    DiagnosticFailure,
    _write_adjacent_checksums,
)

REPOSITORY = Path(__file__).resolve().parents[1]
RUNNER = REPOSITORY / "benchmarks/v013/run_upstream_product_closure.py"


def test_adjacent_checksum_inventory_refuses_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "development-report.json"
    output.write_text("{}\n", encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text("retained\n", encoding="utf-8")

    with pytest.raises(DiagnosticFailure, match="already exists"):
        _write_adjacent_checksums(output)


def test_named_upstream_research_does_not_rotate_frozen_qualification_inputs() -> None:
    closure = (REPOSITORY / "docs/UPSTREAM_PRODUCT_CLOSURE_2026-08-18.md").read_text(
        encoding="utf-8"
    )
    protocol = (REPOSITORY / "docs/V0_13_QUALIFICATION_PROTOCOL.md").read_text(encoding="utf-8")

    assert "21746ce996f3a69898883da58b122770f7dbd668" in closure
    assert "40cc9f9479fef7bfe8a51a6df7e02fe11971f95e" in closure
    assert "cc1744324150c632416857c98964f87b1574a5fc" in closure
    assert "350eec8a284e159b2e4cfd068d808cbf203a6cc5" in closure
    assert "f078160e248f889d66ee37dc0d431854f50d3294c" in protocol
    assert "367a91416477c90bbfae766dc06add3de6ae75a7" in protocol
    assert "| [Ekgardt/llm-wiki]" in closure
    assert "| none; the protocol retains an unnamed LLM-Wiki behavior category |" in closure
    assert "qualification coordinate` and `research anchor`" in closure

    research = (REPOSITORY / "docs/V0_13_UPSTREAM_RESEARCH.md").read_text(encoding="utf-8")
    assert "2026-08-20 current observation" in research
    assert "46c0a3d53011a1f4916052187288dc5b4651c292" in research
    assert "v0.3.3/355f4f68e71bd024631cdcff7aa871c3e72435da" in research
    assert "367a91416477c90bbfae766dc06add3de6ae75a7" in research
    assert "v2026-08-19/cf9b0c8b9fca7cd9556da4b0401e207626a70384" in research
    assert "cc1744324150c632416857c98964f87b1574a5fc" in research
    assert "350eec8a284e159b2e4cfd068d808cbf203a6cc5" in research
    assert "qualification pin" in research
    assert "released comparator" in research
    assert "moving HEAD" in research
    assert "observation date" in research
    assert "Execution status: `not_executed`" in research
    assert "does not establish parity" in research


def test_public_seam_runner_is_sanitized_and_cannot_author_qualification(
    tmp_path: Path,
) -> None:
    runner_source = RUNNER.read_text(encoding="utf-8")
    assert "from deeplaw" not in runner_source
    assert "import deeplaw" not in runner_source

    refused = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.v013.run_upstream_product_closure",
            "--scale",
            "10001",
            "--output",
            str(tmp_path / "refused-report.json"),
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert refused.returncode != 0
    assert "scale above 10000 requires --allow-expensive-scale" in refused.stderr

    output = tmp_path / "development-report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.v013.run_upstream_product_closure",
            "--scale",
            "3",
            "--scale",
            "1000",
            "--output",
            str(output),
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        # This is a bounded cross-process development diagnostic, not the v9
        # performance gate.  Slower supported CI interpreters may exceed five
        # minutes while exercising the same 1k public journey; the exact 10k
        # candidate job owns release latency measurements.
        timeout=900,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    checksums = tmp_path / "SHA256SUMS"
    assert checksums.is_file()
    checksum_lines = checksums.read_text(encoding="utf-8").splitlines()
    assert checksum_lines == [
        f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}"
    ]
    assert "SHA256SUMS" not in checksums.read_text(encoding="utf-8")

    assert report["evidence_class"] == "development_diagnostic"
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    assert report["exact"]["package_version"] == project["project"]["version"]
    assert set(report["exact"]["platform"]) == {"system", "release", "machine", "python"}
    assert report["formal_claims"] == {
        "qualification_evidence": False,
        "release_ready": False,
        "claim_eligible": False,
        "human_gold": False,
        "legal_attestation": False,
        "competitive_claim": False,
    }
    assert report["failed"] == []
    assert report["base_journey"]["provider"]["advertised_operations"] == [
        "context",
        "explain",
        "query",
    ]
    assert report["base_journey"]["provider"]["public_operation_count"] == 3
    assert report["base_journey"]["read_no_write"] == {
        "status": "executed",
        "public_operation_count": 2,
        "host_internal_packet_count": 0,
        "canonical_sequence_unchanged": True,
        "canonical_audit_head_unchanged": True,
    }
    assert report["base_journey"]["task_continuity"]["wrong_state_admission_count"] == 0
    assert report["base_journey"]["compilation_handoff"]["write_performed"] is False
    assert report["base_journey"]["compilation_handoff"]["grant_included"] is False
    assert report["base_journey"]["compilation_handoff"]["model_invoked"] is False
    assert report["base_journey"]["compilation_handoff"]["read_leaf"] == "knowledge_support"
    assert report["base_journey"]["compilation_handoff"]["write_leaf"] == "knowledge_sink"
    assert report["base_journey"]["compilation"]["public_operation_count"] == report[
        "base_journey"
    ]["compilation"]["public_cli_steps"]
    assert report["base_journey"]["compilation"]["host_internal_packet_count"] == report[
        "base_journey"
    ]["compilation"]["packet_count"]
    assert (
        report["base_journey"]["source_evidence"][
            "wiki_exact_source_coordinate_drill_down"
        ]
        is True
    )
    assert (
        report["base_journey"]["source_evidence"]["source_content_read_status"]
        == "executed_after_review"
    )
    assert report["base_journey"]["source_evidence"]["source_read_write_performed"] is False
    assert report["base_journey"]["source_evidence"]["fragment_read_write_performed"] is False
    assert report["base_journey"]["provider"]["actual_provider_tokens"]["status"] == (
        "not_executed"
    )
    assert report["base_journey"]["provider"]["actual_provider_tokens"]["value"] is None
    assert report["base_journey"]["provider"]["query_trace_in_provider"] is False
    assert report["base_journey"]["provider"]["canonical_ledger_in_provider"] is False
    assert report["receipt"]["executed"] == report["executed"]
    assert report["receipt"]["failed"] == report["failed"]
    assert report["receipt"]["not_executed"] == report["not_executed"]
    assert "exact Source/Fragment content read pending owner review" not in report[
        "not_executed"
    ]
    assert report["receipt"]["host_internal_packet_counts"]["base"] == report[
        "base_journey"
    ]["compilation"]["packet_count"]
    assert report["receipt"]["public_operation_counts"]["base"]["handoff_steps"] == 1
    assert report["receipt"]["public_operation_counts"]["base"][
        "compilation_grant_steps"
    ] == 1
    assert report["receipt"]["public_operation_counts"]["evidence_formats"] > 0
    assert report["receipt"]["public_operation_counts"]["source_identity"] == 15
    assert report["evidence_formats"]["ocr_needed_fail_closed"] == {
        "status": "executed",
        "kind": "blank_or_scanned_pdf",
        "expected": "fail_closed_ocr_needed",
        "positive_ocr": "not_executed",
    }
    for name in ("markdown", "html", "docx", "native_text_pdf"):
        evidence = report["evidence_formats"]["format_results"][name]
        assert evidence["status"] == "executed"
        assert evidence["verify_valid"] is True
        assert evidence["source_sha256"] == evidence["read_content_sha256"]
        assert evidence["document_version_fragment_identity"] is True
        assert evidence["fragment_locator"]
        assert evidence["read_write_performed"] is False
        assert evidence["fragment_read_write_performed"] is False
    assert report["source_identity"]["stable_logical_source_identity"] is True
    assert report["source_identity"]["historical_alias_resolved"] is True
    assert report["source_identity"]["current_alias_resolved"] is True
    assert report["source_identity"]["successor_previous_source_id"] == report[
        "source_identity"
    ]["active_source_id_before_review"]
    assert report["source_identity"]["parallel_pending_successor_rejection"] == {
        "status": "executed",
        "kind": "ambiguous_successor_not_arbitrary_semantic_merge_judgment",
        "wrong_state_admission_count": 0,
    }
    context_selection = report["base_journey"]["context_selection"]
    assert {key: value for key, value in context_selection.items() if key != "elapsed_seconds"} == {
        "status": "executed",
        "public_cli_steps": 2,
        "public_operation_count": 2,
        "expected_include": "executed_and_selected_once",
        "expected_exclude": "executed_and_excluded",
        "required_duties": {
            "primary_answer": "satisfied",
            "source_evidence": "satisfied",
            "unresolved_gap": "satisfied",
        },
        "acceptable_gap": "uncompiled_source",
        "duplicate_suppression_reasons": ["duplicate_source_reference"],
        "distractor_suppressed": True,
        "provider_write_performed": False,
    }
    assert context_selection["elapsed_seconds"] > 0
    assert {
        key: value
        for key, value in report["base_journey"]["identity_ambiguity"].items()
        if key != "elapsed_seconds"
    } == {
        "status": "executed",
        "public_cli_steps": 7,
        "public_mcp_steps": 0,
        "public_operation_count": 7,
        "host_internal_packet_count": 0,
        "same_name_distinct_identity_count": 2,
        "same_name_lookup_status": "wiki_browse_distinct",
        "automatic_title_merge_rejected": True,
        "alias_page_read_status": "exact_page_read",
        "alias_resolved_exact_identity": True,
        "wiki_distinct_identity_count": 2,
        "legal_authority": False,
    }
    assert report["base_journey"]["identity_ambiguity"]["elapsed_seconds"] > 0
    assert not any("same-name entity collision" in item for item in report["not_executed"])
    assert any(
        "arbitrary semantic wrong-merge correctness" in item
        for item in report["not_executed"]
    )
    assert report["scale_lanes"][0]["scale"] == 3
    assert report["scale_lanes"][0]["objects_staged"] == 3
    assert report["scale_lanes"][0]["rename_edit_reconcile"] is True
    assert report["scale_lanes"][0]["user_file_exact_bytes_preserved"] is True
    assert report["scale_lanes"][1]["scale"] == 1000
    assert report["scale_lanes"][1]["objects_staged"] == 1000
    assert report["scale_lanes"][1]["status"] == "executed"
    assert report["scale_lanes"][1]["wiki_link_index"]["outlink_resolved"] is True
    assert report["scale_lanes"][1]["wiki_link_index"]["backlink_resolved"] is True
    assert report["scale_lanes"][1]["wiki_link_index"]["index_used"] is True
    assert report["scale_lanes"][1]["wiki_link_index"]["write_performed"] is False
    assert report["scale_lanes"][1]["public_operation_count"] == report["scale_lanes"][1][
        "public_cli_steps"
    ]
    assert report["scale_lanes"][1]["public_cli_steps"] == (
        report["scale_lanes"][1]["compilation_public_operation_count"] + 13
    )
    assert report["scale_lanes"][1]["host_internal_packet_count"] == report["scale_lanes"][1][
        "packet_count"
    ]
    assert report["scale_lanes"][1]["no_op_projection_equivalent"] is True
    assert report["scale_lanes"][1]["no_op_canonical_ledger_unchanged"] is True
    assert report["scale_lanes"][1]["full_incremental_changed_input_equivalent"] is True
    assert report["scale_lanes"][1]["rename_edit_reconcile"] is True
    assert report["scale_lanes"][1]["persistent_mcp"]["warm_query_timing_samples"] == 5
    assert report["scale_lanes"][1]["persistent_mcp"]["warm_context_timing_samples"] == 5
    assert report["scale_lanes"][1]["persistent_mcp"]["warm_query_timing_ms_p95"] is not None
    assert report["scale_lanes"][1]["persistent_mcp"]["warm_context_timing_ms_p95"] is not None
    assert report["scale_lanes"][1]["actual_provider_tokens"]["status"] == "not_executed"
    assert report["scale_lanes"][1]["actual_provider_tokens"]["value"] is None
    assert report["scale_lanes"][1]["artifacts"]["canvas_file_count"] == 0
    assert report["scale_lanes"][1]["artifacts"]["ownership_manifest"][
        "wiki_object_markdown_count"
    ] == 0
    assert report["scale_lanes"][1]["artifacts"]["ownership_manifest"][
        "wiki_community_markdown_count"
    ] == 0
    assert report["scale_lanes"][1]["artifacts"]["v3_page_registry"]["status"] == "present"
    assert report["scale_lanes"][1]["artifacts"]["v3_link_index"]["status"] == "present"
    assert report["scale_lanes"][1]["artifacts"]["v3_resolver"]["status"] == "present"
    for metric in ("query_elapsed_seconds", "wiki_browse_elapsed_seconds"):
        elapsed = report["scale_lanes"][1][metric]
        assert isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
        assert math.isfinite(elapsed) and elapsed > 0

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        str(tmp_path),
        str(REPOSITORY),
        "/Users/",
        "/tmp/",
        "synthetic-official-session-upstream-closure",
        "token_path",
        "transcript_content",
        "reasoning_content",
    ):
        assert forbidden not in rendered

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
RUNNER = REPOSITORY / "benchmarks/v013/run_upstream_product_closure.py"


def test_named_upstream_research_does_not_rotate_frozen_qualification_inputs() -> None:
    closure = (REPOSITORY / "docs/UPSTREAM_PRODUCT_CLOSURE_2026-08-18.md").read_text(
        encoding="utf-8"
    )
    protocol = (REPOSITORY / "docs/V0_13_QUALIFICATION_PROTOCOL.md").read_text(encoding="utf-8")

    assert "21746ce996f3a69898883da58b122770f7dbd668" in closure
    assert "40cc9f9479fef7bfe8a51a6df7e02fe11971f95e" in closure
    assert "cc1744324150c632416857c98964f87b1574a5fc" in closure
    assert "350eec8a284e159b2e4cfd068d808cbf203a6cc5" in closure
    assert "630eb9ec3fa22a4bed2d347fc3ea3a6a3bd22abc" in protocol
    assert "cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d" in protocol
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
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))

    assert report["evidence_class"] == "development_diagnostic"
    assert report["exact"]["package_version"] == "0.12.0"
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
    assert report["base_journey"]["read_no_write"] == {
        "status": "executed",
        "canonical_sequence_unchanged": True,
        "canonical_audit_head_unchanged": True,
    }
    assert report["base_journey"]["task_continuity"]["wrong_state_admission_count"] == 0
    assert (
        report["base_journey"]["source_evidence"][
            "wiki_exact_source_coordinate_drill_down"
        ]
        is True
    )
    assert (
        report["base_journey"]["source_evidence"]["source_content_read_status"]
        == "withheld_pending_owner_review"
    )
    assert report["scale_lanes"][0]["scale"] == 3
    assert report["scale_lanes"][0]["objects_staged"] == 3
    assert report["scale_lanes"][0]["rename_edit_reconcile"] is True
    assert report["scale_lanes"][0]["user_file_exact_bytes_preserved"] is True
    assert report["scale_lanes"][1]["scale"] == 1000
    assert report["scale_lanes"][1]["objects_staged"] == 1000
    assert report["scale_lanes"][1]["status"] == "executed"
    assert report["scale_lanes"][1]["no_op_projection_equivalent"] is True
    assert report["scale_lanes"][1]["no_op_canonical_ledger_unchanged"] is True
    assert report["scale_lanes"][1]["full_incremental_changed_input_equivalent"] is True
    assert report["scale_lanes"][1]["rename_edit_reconcile"] is True
    assert report["scale_lanes"][1]["persistent_mcp"]["warm_query_timing_samples"] == 5
    assert report["scale_lanes"][1]["persistent_mcp"]["warm_context_timing_samples"] == 5
    assert report["scale_lanes"][1]["persistent_mcp"]["warm_query_timing_ms_p95"] is not None
    assert report["scale_lanes"][1]["persistent_mcp"]["warm_context_timing_ms_p95"] is not None
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
    assert report["scale_lanes"][1]["query_elapsed_seconds"] <= 10
    assert report["scale_lanes"][1]["wiki_browse_elapsed_seconds"] <= 10

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

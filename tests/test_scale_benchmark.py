from __future__ import annotations

import json
from pathlib import Path

from benchmarks.scale.run_knowledge_scale import _peak_rss_bytes, run_diagnostic


def test_peak_rss_is_reported_as_bytes() -> None:
    assert _peak_rss_bytes() > 0


def test_scale_diagnostic_uses_the_real_cli_and_verified_capsules(
    tmp_path: Path,
) -> None:
    report = run_diagnostic(
        tmp_path / "workspace",
        asset_count=100,
        query_count=5,
    )

    assert report["claim_eligible"] is False
    assert report["corpus"]["asset_count"] == 100
    assert report["cli_lifecycle"]["approved_asset_count"] == 100
    assert report["cli_lifecycle"]["search_hit_at_1"] is True
    assert report["cli_lifecycle"]["capsule_recall"] is True
    assert report["cli_lifecycle"]["capsule_valid"] is True
    assert report["persistent_reader"]["integrity_valid"] is True
    assert report["persistent_reader"]["search_hit_at_1"] == 1.0
    assert report["persistent_reader"]["capsule_recall"] == 1.0
    assert report["persistent_reader"]["capsule_verification_rate"] == 1.0


def test_historical_100k_knowledge_scale_report_is_preserved() -> None:
    repository = Path(__file__).resolve().parents[1]
    report = json.loads(
        (
            repository
            / "benchmarks/scale/knowledge-scale-100k-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert report["schema_version"] == "deeplaw.knowledge-scale-diagnostic/v1"
    assert report["claim_eligible"] is False
    assert report["corpus"]["asset_count"] == 100_000
    assert report["corpus"]["query_count"] == 100
    assert report["persistent_reader"]["integrity_valid"] is True
    assert report["persistent_reader"]["search_hit_at_1"] == 1.0
    assert report["persistent_reader"]["capsule_recall"] == 1.0
    assert report["persistent_reader"]["capsule_verification_rate"] == 1.0
    assert report["candidate"]["version"] == "0.4.0"


def test_historical_v050_100k_discovery_scale_report_is_source_bound() -> None:
    repository = Path(__file__).resolve().parents[1]
    report = json.loads(
        (
            repository
            / "benchmarks/scale/discovery-scale-100k-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert report["schema_version"] == "deeplaw.discovery-scale-diagnostic/v1"
    assert report["claim_eligible"] is False
    assert report["candidate"]["version"] == "0.5.0"
    assert report["candidate"]["asset_count"] == 100_000
    assert report["candidate"]["default_runtime_enabled"] is False
    assert report["measurements"]["query_count"] == 100
    assert report["measurements"]["hit_at_1"] == 1.0
    assert report["candidate"]["implementation_files"] == {
        "benchmarks/scale/run_discovery_scale.py": (
            "4015734a7f7a16fee7b2f3126f1a711ad93b82ae8a421a0809f6ee3c9f7c6568"
        ),
        "src/deeplaw/knowledge_discovery.py": (
            "82e03b088e8b9061d11dff10cb4fcf416fe11f0981f494d2cf67b3a71a75bf86"
        ),
    }

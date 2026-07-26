from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.scale.run_knowledge_scale import run_diagnostic
from deeplaw import __version__


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


def test_current_100k_discovery_scale_report_is_source_bound() -> None:
    repository = Path(__file__).resolve().parents[1]
    report = json.loads(
        (
            repository
            / "benchmarks/scale/discovery-scale-100k-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert report["schema_version"] == "deeplaw.discovery-scale-diagnostic/v1"
    assert report["claim_eligible"] is False
    assert report["candidate"]["version"] == __version__ == "0.5.0"
    assert report["candidate"]["asset_count"] == 100_000
    assert report["candidate"]["default_runtime_enabled"] is False
    assert report["measurements"]["query_count"] == 100
    assert report["measurements"]["hit_at_1"] == 1.0
    for relative_path, expected_sha256 in report["candidate"][
        "implementation_files"
    ].items():
        assert (
            hashlib.sha256((repository / relative_path).read_bytes()).hexdigest()
            == expected_sha256
        )

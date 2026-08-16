from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from benchmarks.release.external_qualification_bundle import (
    ExternalQualificationBundleError,
    _check_json_projection,
    validate_external_bundle,
)

REPOSITORY = Path(__file__).resolve().parents[1]
PRODUCER = REPOSITORY / ".github/workflows/external-qualification-evidence.yml"
CONSUMER = REPOSITORY / ".github/workflows/commercial-qualification.yml"


def test_external_qualification_workflow_matches_consumer_and_never_rebuilds() -> None:
    producer = PRODUCER.read_text(encoding="utf-8")
    consumer = CONSUMER.read_text(encoding="utf-8")
    assert yaml.safe_load(producer)["name"] == "External Qualification Evidence"
    assert "name: v013-qualification-evidence" in producer
    assert "name: v013-qualification-evidence" in consumer
    assert "external-qualification-evidence.yml" in consumer
    assert "verified-candidate-artifacts" in producer
    assert "self-hosted" in producer
    assert "macOS" in producer
    assert "deeplaw-qualification" in producer
    assert "DEEPLAW_HUMAN_GOLD_ROOT" in producer
    assert "DEEPLAW_INDEPENDENT_SCORER" in producer
    assert "DEEPLAW_EXTERNAL_QUALIFICATION_RUNNER" in producer
    assert '--evidence-run-id "${GITHUB_RUN_ID}"' in producer
    assert '--evidence-run-id "${EVIDENCE_RUN_ID}"' in consumer
    assert "if-no-files-found: error" in producer
    assert "python -m build" not in producer
    assert "uv build" not in producer
    assert "hatch build" not in producer


def test_external_bundle_rejects_secret_named_or_raw_log_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence/auth.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ExternalQualificationBundleError, match="forbidden filename"):
        validate_external_bundle(
            root,
            active_qualification=tmp_path / "missing-active.json",
            classification=REPOSITORY
            / "benchmarks/release/v013-gate-classification-v6.json",
        )
    (root / "evidence/auth.json").unlink()
    (root / "evidence/raw-events.log").write_text("redacted\n", encoding="utf-8")
    with pytest.raises(ExternalQualificationBundleError, match="forbidden filename"):
        validate_external_bundle(
            root,
            active_qualification=tmp_path / "missing-active.json",
            classification=REPOSITORY
            / "benchmarks/release/v013-gate-classification-v6.json",
        )


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "/Users/example/private/evidence.json",
        "api_key=not-a-real-secret-canary",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
    ],
)
def test_external_bundle_projection_rejects_path_and_secret_values(
    unsafe_value: str,
) -> None:
    with pytest.raises(ExternalQualificationBundleError, match="path or Secret"):
        _check_json_projection({"value": unsafe_value})

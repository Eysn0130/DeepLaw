from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from benchmarks.release.external_qualification_bundle import (
    ExternalQualificationBundleError,
    _check_json_projection,
    _record_sha256,
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
    assert '--candidate-run-id "${CANDIDATE_RUN_ID}"' in producer
    assert "bundle-manifest.json" in producer
    assert '--evidence-run-id "${EVIDENCE_RUN_ID}"' in consumer
    assert '--candidate-run-id "${CANDIDATE_RUN_ID}"' in consumer
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


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema_version":"x","schema_version":"y"}\n',
        '{"value":NaN}\n',
        '{"value":Infinity}\n',
        '{"value":1e999}\n',
    ],
)
def test_external_bundle_rejects_non_strict_json_before_gate_validation(
    tmp_path: Path,
    payload: str,
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "invalid.json").write_text(payload, encoding="utf-8")
    with pytest.raises(
        ExternalQualificationBundleError,
        match=r"strict UTF-8 JSON|non-finite number",
    ):
        validate_external_bundle(
            root,
            active_qualification=tmp_path / "missing-active.json",
            classification=REPOSITORY
            / "benchmarks/release/v013-gate-classification-v6.json",
            expected_candidate_run_id=11,
            expected_evidence_run_id=12,
        )


def test_external_bundle_scans_non_json_sanitized_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "receipt.txt").write_text(
        "retained from /Users/example/private/run\n",
        encoding="utf-8",
    )
    with pytest.raises(ExternalQualificationBundleError, match="path or Secret"):
        validate_external_bundle(
            root,
            active_qualification=tmp_path / "missing-active.json",
            classification=REPOSITORY
            / "benchmarks/release/v013-gate-classification-v6.json",
            expected_candidate_run_id=11,
            expected_evidence_run_id=12,
        )


def _file_reference(path: Path, *, root: Path, kind: str) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "media_type": "application/json",
        "evidence_kind": kind,
    }


def _write_manifest(root: Path, *, include_orphan: bool) -> None:
    collection = root / "evidence/gate-collection.json"
    template = root / "commercial-release-template.json"
    references = [
        _file_reference(collection, root=root, kind="gate_collection"),
        _file_reference(template, root=root, kind="commercial_release_template"),
    ]
    if include_orphan:
        references.append(
            _file_reference(
                root / "orphan.json",
                root=root,
                kind="sanitized_supporting_receipt",
            )
        )
    manifest: dict[str, object] = {
        "schema_version": "deeplaw.external-qualification-bundle-manifest/v2",
        "candidate_run_id": 11,
        "evidence_run_id": 12,
        "candidate_binding": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "lock_sha256": "c" * 64,
            "wheel_sha256": "d" * 64,
            "sdist_sha256": "e" * 64,
        },
        "external_inputs": {
            "semantic_gold_sha256": "1" * 64,
            "candidate_gold_binding_sha256": "2" * 64,
            "qualification_holdout_sha256": "3" * 64,
            "final_blind_holdout_sha256": "4" * 64,
            "runner_sha256": "5" * 64,
            "scorer_sha256": "6" * 64,
            "compiler_scorer_isolation_sha256": "7" * 64,
        },
        "files": references,
    }
    manifest["record_sha256"] = _record_sha256(manifest)
    (root / "bundle-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_external_bundle_rejects_orphan_files(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence/gate-collection.json").write_text("{}\n", encoding="utf-8")
    (root / "commercial-release-template.json").write_text("{}\n", encoding="utf-8")
    (root / "orphan.json").write_text("{}\n", encoding="utf-8")
    _write_manifest(root, include_orphan=False)
    with pytest.raises(ExternalQualificationBundleError, match="orphan or unreferenced"):
        validate_external_bundle(
            root,
            active_qualification=tmp_path / "missing-active.json",
            classification=REPOSITORY
            / "benchmarks/release/v013-gate-classification-v6.json",
            expected_candidate_run_id=11,
            expected_evidence_run_id=12,
        )


def test_external_bundle_rejects_manifest_file_hash_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "evidence").mkdir(parents=True)
    collection = root / "evidence/gate-collection.json"
    collection.write_text("{}\n", encoding="utf-8")
    (root / "commercial-release-template.json").write_text("{}\n", encoding="utf-8")
    (root / "orphan.json").write_text("{}\n", encoding="utf-8")
    _write_manifest(root, include_orphan=True)
    collection.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(ExternalQualificationBundleError, match="file binding differs"):
        validate_external_bundle(
            root,
            active_qualification=tmp_path / "missing-active.json",
            classification=REPOSITORY
            / "benchmarks/release/v013-gate-classification-v6.json",
            expected_candidate_run_id=11,
            expected_evidence_run_id=12,
        )

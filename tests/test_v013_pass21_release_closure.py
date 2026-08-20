from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release import release_policy

REPOSITORY = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v1_qualification_binding_remains_a_historical_012_deadlock_record() -> None:
    schema_path = REPOSITORY / "contracts/v013-active-qualification.v1.schema.json"
    binding_path = REPOSITORY / "benchmarks/v013/active-qualification-v1.json"
    schema = _load(schema_path)
    binding = _load(binding_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(binding)

    pyproject = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    version_line = next(
        line for line in pyproject.splitlines() if line.startswith("version = ")
    )
    package_version = version_line.split('"', 2)[1]
    assert package_version in {"0.12.0", "0.13.0"}
    assert binding["candidate_version"] == "0.12.0"
    assert schema["properties"]["candidate_version"]["pattern"] == (
        "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$"
    )

    candidate = dict(binding)
    candidate["candidate_version"] = "0.13.0"
    Draft202012Validator(schema).validate(candidate)


def test_product_surface_manifest_routes_current_sink_to_v6() -> None:
    manifest = _load(REPOSITORY / "governance/product-surface-manifest.v1.json")
    caller = next(
        item
        for item in manifest["external_callers"]  # type: ignore[index]
        if item["caller"] == "knowledge_sink"
    )
    assert "contracts/knowledge-sink.input.v6.schema.json" in caller[
        "current_bindings"
    ]
    assert caller["compatibility_bindings"] == [
        "contracts/knowledge-sink.input.v1.schema.json through v5",
        "contracts/knowledge-sink.output.v1.schema.json through v3",
    ]
    surface = next(
        item
        for item in manifest["surfaces"]  # type: ignore[index]
        if item["surface_id"] == "advanced.knowledge_sink"
    )
    assert "contracts/knowledge-sink.input.v6.schema.json" in surface["bindings"]


def test_pre_freeze_version_deadlock_is_explicit_and_fail_closed() -> None:
    from benchmarks.release.freeze_qualification_candidate import (
        QualificationFreezeError,
        freeze_candidate,
    )

    binding = _load(REPOSITORY / "benchmarks/v013/active-qualification-v1.json")
    assert binding["candidate_version"] == "0.12.0"
    assert binding["status"] == "construction_candidate"
    assert binding["blocker"] == "release_version_binding_deadlock"
    assert binding["release_ready"] is False
    assert binding["claim_eligible"] is False
    assert release_policy.required_manifest_schema_version("0.13.0") == (
        "deeplaw.commercial-release-manifest/v9"
    )
    assert release_policy.required_legacy_manifest_schema_version("0.13.0") == (
        "deeplaw.commercial-release-manifest/v6"
    )
    with pytest.raises(QualificationFreezeError, match="release_version_binding_deadlock"):
        freeze_candidate(
            template_path=REPOSITORY / "benchmarks/v013/active-qualification-v1.json",
            reproducible_report_path=REPOSITORY / "benchmarks/v013/active-qualification-v1.json",
            artifact_manifest_path=REPOSITORY / "benchmarks/v013/active-qualification-v1.json",
        )


def test_gate_v6_is_active_timeline_is_core_and_validators_are_ready() -> None:
    classification_path = (
        REPOSITORY / "benchmarks/release/v013-gate-classification-v6.json"
    )
    schema_path = REPOSITORY / "contracts/v013-release-gate-classification.v6.schema.json"
    schema = _load(schema_path)
    classification = _load(classification_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(classification)

    assert classification_path == release_policy.V013_V6_CLASSIFICATION_PATH
    assert classification["assembly_policy"] == {
        "assembly_enabled": False,
        "reason_code": "awaiting_all_core_gate_pass",
    }
    gates = {
        item["gate_id"]: item
        for item in classification["gates"]  # type: ignore[index]
    }
    assert gates["timeline"]["category"] == "Core"
    assert gates["timeline"]["required"] is True
    for gate_id in release_policy.V013_V6_CORE_GATE_IDS:
        gate = gates[gate_id]
        assert gate["implementation_status"] == "ready"
        assert gate["assembly_enabled"] is False
        assert "deeplaw.v013-gate-result/v1" in gate["output_schema_versions"]
    for gate_id in {
        "canonical_integrity",
        "migration_recovery",
        "secret_host_isolation",
        "scale_performance",
        "supported_platforms",
        "reproducible_supply_chain",
        "selective_forget",
        "timeline",
    }:
        assert gates[gate_id]["required_corpus_roles"] == ["development"]


def test_selective_forget_has_a_raw_contract_and_dedicated_validator() -> None:
    raw_schema = _load(
        REPOSITORY / "contracts/selective-forget-qualification.v1.schema.json"
    )
    Draft202012Validator.check_schema(raw_schema)
    assert raw_schema["properties"]["schema_version"]["const"] == (
        "deeplaw.selective-forget-qualification/v1"
    )
    validator = REPOSITORY / "benchmarks/release/v013_gate_validator.py"
    source = validator.read_text(encoding="utf-8")
    assert "validate_selective_forget" in source
    assert "forgotten_state_admission_count" in source


def test_platform_core_v2_is_complete_non_overlapping_and_active() -> None:
    from benchmarks.release.platform_inventory import verify_platform_inventory

    manifest = REPOSITORY / "benchmarks/release/platform-core-test-manifest-v2.json"
    result = verify_platform_inventory(
        repository=REPOSITORY,
        manifest_path=manifest,
        require_match=True,
    )
    assert result["status"] == "passed"
    assert result["unexpected_node_ids"] == []
    assert result["missing_node_ids"] == []
    assert result["overlap_node_ids"] == []


def test_retained_manifest_rejects_hash_drift_from_verified_build(tmp_path: Path) -> None:
    from benchmarks.release.retained_artifact_manifest import (
        SingleArtifactError,
        verify_single_artifact_source,
    )

    verified = tmp_path / "verified"
    retained = tmp_path / "retained"
    verified.mkdir()
    retained.mkdir()
    (verified / "deeplaw-0.12.0-py3-none-any.whl").write_bytes(b"verified wheel")
    (verified / "deeplaw-0.12.0.tar.gz").write_bytes(b"verified sdist")
    (retained / "deeplaw-0.12.0-py3-none-any.whl").write_bytes(b"different wheel")
    (retained / "deeplaw-0.12.0.tar.gz").write_bytes(b"verified sdist")

    with pytest.raises(SingleArtifactError, match="verified artifact hash"):
        verify_single_artifact_source(verified_dist=verified, consumer_dist=retained)


def test_candidate_artifact_retention_is_at_least_ninety_days() -> None:
    workflow = (REPOSITORY / ".github/workflows/candidate-full.yml").read_text(
        encoding="utf-8"
    )
    commercial = (
        REPOSITORY / ".github/workflows/commercial-qualification.yml"
    ).read_text(encoding="utf-8")
    release = (REPOSITORY / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    assert "retention-days: 90" in workflow
    assert workflow.count("verify_reproducible_build") == 1
    assert "uv build" not in workflow
    verifier_command = "python -m benchmarks.release.retained_artifact_manifest"
    assert verifier_command in commercial
    assert verifier_command in release
    assert "fresh-install-verified-artifact/v1" in release


def test_historical_gate_v5_and_protocol_v1_bytes_remain_unchanged() -> None:
    expected = {
        "benchmarks/release/v013-gate-classification-v5.json": (
            "d8f3e638f8f57c09adf55e274def67c3cabbc729b7e9cdd287cc9605eda6c7bb"
        ),
        "contracts/v013-release-gate-classification.v5.schema.json": (
            "1201ed131973fb42a345b1761b1e82eb3b7dc3579cf971e662e05b407adcc816"
        ),
        "contracts/v013-qualification-protocol.v1.schema.json": (
            "6410ad445c5d3d664bbf9ba51beb0cbf95d4064696c24af65961b321ae3015b8"
        ),
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((REPOSITORY / relative).read_bytes()).hexdigest() == digest

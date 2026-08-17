"""Version-conditional v0.13 release envelope and workflow regressions."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release import release_policy

REPOSITORY = Path(__file__).resolve().parents[1]
V5 = "deeplaw.commercial-release-manifest/v5"
V6 = "deeplaw.commercial-release-manifest/v6"
VERSION = "0.13.0"
COMMIT = "a" * 40
TREE = "b" * 40


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _record_digest(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _synthetic_envelope() -> dict[str, Any]:
    """Build a shape-only development fixture; it is not semantic release evidence."""

    artifacts: list[dict[str, Any]] = []

    def artifact(path: str) -> tuple[str, str]:
        digest = _digest(path)
        artifacts.append({"path": path, "sha256": digest, "byte_size": len(path)})
        return path, digest

    wheel_path, wheel_sha = artifact("dist/deeplaw-0.13.0-py3-none-any.whl")
    sdist_path, sdist_sha = artifact("dist/deeplaw-0.13.0.tar.gz")
    assert wheel_path and sdist_path
    refs = {
        name: artifact(f"evidence/{name}.json")
        for name in (
            "prd",
            "traceability",
            "protocol",
            "thresholds",
            "human-gold",
            "compiler-evaluator-isolation",
            "classification",
            "semantic-report",
        )
    }
    gate_statuses = [
        {"gate_id": gate, "category": "Core", "status": "passed"}
        for gate in sorted(release_policy.V013_CORE_GATE_IDS)
    ]
    gate_statuses.extend(
        {"gate_id": gate, "category": "Capability", "status": "not_claimed"}
        for gate in sorted(release_policy.V013_CAPABILITY_GATE_IDS)
    )
    gate_statuses.extend(
        {
            "gate_id": gate,
            "category": "Competitive Claim",
            "status": "not_claimed",
        }
        for gate in sorted(release_policy.V013_COMPETITIVE_GATE_IDS)
    )
    manifest: dict[str, Any] = {
        "schema_version": V6,
        "environment": {
            "platform_system": "Synthetic",
            "platform_release": "development",
            "platform_version": "development",
            "machine": "development",
            "python_implementation": "CPython",
            "python_version": "3.12.0",
            "python_executable_name": "python",
            "uv_version": "0.0.0",
            "ci": False,
            "github_actions": False,
            "github_runner_os": None,
            "github_runner_arch": None,
        },
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": VERSION,
            "tag": f"v{VERSION}",
            "commit": COMMIT,
            "tree": TREE,
        },
        "bindings": {
            "prd_path": refs["prd"][0],
            "prd_sha256": refs["prd"][1],
            "traceability_path": refs["traceability"][0],
            "traceability_sha256": refs["traceability"][1],
            "qualification_protocol_path": refs["protocol"][0],
            "qualification_protocol_sha256": refs["protocol"][1],
            "thresholds_path": refs["thresholds"][0],
            "thresholds_sha256": refs["thresholds"][1],
            "human_gold_manifest_path": refs["human-gold"][0],
            "human_gold_manifest_sha256": refs["human-gold"][1],
            "compiler_evaluator_isolation_path": refs["compiler-evaluator-isolation"][0],
            "compiler_evaluator_isolation_sha256": refs[
                "compiler-evaluator-isolation"
            ][1],
            "gate_classification_path": refs["classification"][0],
            "gate_classification_sha256": refs["classification"][1],
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "candidate_wheel_sha256": wheel_sha,
            "candidate_sdist_sha256": sdist_sha,
            "candidate_version": VERSION,
        },
        "artifacts": artifacts,
        "semantic_evidence": {
            "report_path": refs["semantic-report"][0],
            "report_artifact_sha256": refs["semantic-report"][1],
            "report_record_sha256": _digest("semantic-record"),
            "report_kind": "v013_commercial_gate_collection",
            "status": "passed",
            "hard_zero": True,
            "release_ready": True,
            "claim_eligible": True,
            "competitive_claim_eligible": False,
            "gate_statuses": gate_statuses,
        },
        "commercial_release_eligible": True,
        "quality_protocol_eligible": True,
        "competitive_claim_eligible": False,
    }
    manifest["record_sha256"] = _record_digest(manifest)
    return manifest


def _refresh(manifest: dict[str, Any]) -> None:
    manifest["record_sha256"] = _record_digest(manifest)


def test_v013_selects_v6_and_cannot_downgrade_to_v5() -> None:
    assert release_policy.required_manifest_schema_version("0.12.9") == V5
    assert release_policy.required_manifest_schema_version(VERSION) == V6
    with pytest.raises(release_policy.ReleasePolicyError, match="requires"):
        release_policy.validate_manifest_for_release(
            {"schema_version": V5},
            release_version=VERSION,
        )


def test_v6_schema_and_envelope_policy_accept_only_the_derived_shape() -> None:
    manifest = _synthetic_envelope()
    schema = json.loads(
        (REPOSITORY / "contracts/commercial-release-manifest.v6.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    release_policy.validate_manifest_for_release(manifest, release_version=VERSION)


def test_envelope_rejects_missing_or_failed_core_gate() -> None:
    missing = _synthetic_envelope()
    missing["semantic_evidence"]["gate_statuses"].pop(0)
    _refresh(missing)
    with pytest.raises(release_policy.ReleasePolicyError, match="incomplete"):
        release_policy.validate_manifest_for_release(missing, release_version=VERSION)

    failed = _synthetic_envelope()
    failed["semantic_evidence"]["gate_statuses"][0]["status"] = "failed"
    _refresh(failed)
    with pytest.raises(release_policy.ReleasePolicyError, match="Core gate"):
        release_policy.validate_manifest_for_release(failed, release_version=VERSION)


def test_capability_and_competitive_gate_semantics_are_fail_closed() -> None:
    capability = _synthetic_envelope()
    row = next(
        item
        for item in capability["semantic_evidence"]["gate_statuses"]
        if item["category"] == "Capability"
    )
    row["status"] = "not_executed"
    _refresh(capability)
    with pytest.raises(release_policy.ReleasePolicyError, match="Capability gates"):
        release_policy.validate_manifest_for_release(capability, release_version=VERSION)

    competitive = _synthetic_envelope()
    row = next(
        item
        for item in competitive["semantic_evidence"]["gate_statuses"]
        if item["category"] == "Competitive Claim"
    )
    row["status"] = "passed"
    _refresh(competitive)
    with pytest.raises(release_policy.ReleasePolicyError, match="competitive claims"):
        release_policy.validate_manifest_for_release(competitive, release_version=VERSION)


def test_manifest_record_and_candidate_assets_remain_exact() -> None:
    manifest = _synthetic_envelope()
    manifest["bindings"]["candidate_wheel_sha256"] = "0" * 64
    _refresh(manifest)
    with pytest.raises(release_policy.ReleasePolicyError, match="wheel sha256"):
        release_policy.validate_manifest_for_release(manifest, release_version=VERSION)

    tampered = _synthetic_envelope()
    tampered["record_sha256"] = "0" * 64
    with pytest.raises(release_policy.ReleasePolicyError, match="record_sha256"):
        release_policy.validate_manifest_for_release(tampered, release_version=VERSION)


def test_required_evidence_inventory_names_semantic_bindings() -> None:
    required = set(release_policy.V013_REQUIRED_EVIDENCE_PATHS)
    assert {
        "bindings.thresholds_sha256",
        "bindings.human_gold_manifest_sha256",
        "bindings.compiler_evaluator_isolation_sha256",
        "bindings.gate_classification_sha256",
        "semantic_evidence.report_artifact_sha256",
        "semantic_evidence.report_record_sha256",
        "semantic_evidence.gate_statuses[]",
    } <= required


def test_publish_and_post_release_validate_semantics_before_envelope() -> None:
    workflow = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")
    validation = workflow.split("\n  validate-assets:", maxsplit=1)[1].split(
        "\n  fresh-install:", maxsplit=1
    )[0]
    assert "benchmarks.release.release_provenance_v8" in validation
    assert "--assets-root" in validation
    assert "--candidate-run-id" in validation
    assert "--evidence-run-id" in validation
    assert "--qualification-run-id" in validation
    public = workflow.split("\n  public-redownload:", maxsplit=1)[1]
    assert "gh release download" not in public
    assert "https://github.com/${GITHUB_REPOSITORY}/releases/download" in public
    assert "curl --fail --location" in public
    assert "sha256sum --check" in public
    assert "sigstore verify identity" in public
    assert "gh attestation verify" in public


def test_historical_assembler_points_v013_to_semantic_assembler() -> None:
    source = (REPOSITORY / "benchmarks/release/commercial_release.py").read_text(
        encoding="utf-8"
    )
    assert "benchmarks.release.v013_commercial_release" in source


def test_v013_commercial_qualification_recomputes_v8_from_verified_artifacts() -> None:
    workflow = (
        REPOSITORY / ".github/workflows/commercial-qualification.yml"
    ).read_text(
        encoding="utf-8"
    )
    assert "verified-candidate-artifacts" in workflow
    assert "v013-qualification-evidence" in workflow
    assert "candidate-full-raw-evidence" in workflow
    assert "benchmarks.release.assemble_commercial_qualification_v8" in workflow
    assert "benchmarks.release.release_provenance_v8" in workflow
    assert "v013-gate-classification-v8.json" in workflow
    assert "qualification-protocol-v2.json" in workflow
    assert "machine-only" in workflow
    assert "post_build_machine_reference_binding" in workflow
    assert "--candidate-machine-reference-binding" in workflow
    assert "trusted-human-approver" not in workflow
    assert "uv build" not in workflow

    legacy = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(
        encoding="utf-8"
    )
    assert "Legacy v0.12" in legacy
    assert "v0.13 must consume Candidate Full verified artifacts" in legacy


def test_envelope_fixture_is_not_misrepresented_as_external_evidence() -> None:
    module = Path(__file__).read_text(encoding="utf-8")
    assert "shape-only development fixture" in module
    assert "not semantic release evidence" in module
    assert copy.deepcopy(_synthetic_envelope())["competitive_claim_eligible"] is False

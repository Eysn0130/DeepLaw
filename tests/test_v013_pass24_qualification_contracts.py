from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACTS = REPOSITORY / "contracts"
V6_RELEASE_SCHEMA = CONTRACTS / "commercial-release-manifest.v6.schema.json"
V6_RELEASE_SCHEMA_SHA256 = "4d1411554ac6cc286f000d205b2346d9117860221a2aeb524f167b2ce6f6420a"
SHA = "a" * 64
OTHER_SHA = "b" * 64
COMMIT = "1" * 40
TREE = "2" * 40


def _load_schema(filename: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / filename).read_text(encoding="utf-8"))


def _validator(filename: str) -> Draft202012Validator:
    schema = _load_schema(filename)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _assert_valid(filename: str, value: dict[str, Any]) -> None:
    _validator(filename).validate(value)


def _assert_invalid(filename: str, value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _validator(filename).validate(value)


def _semantic_gold() -> dict[str, Any]:
    labels = [
        {"label_id": "include_answer", "description": "Expected bounded answer"},
        {"label_id": "exclude_unbound", "description": "Must remain excluded"},
    ]
    case_template = {
        "labels": ["include_answer", "exclude_unbound"],
        "expected": {"include": ["include_answer"], "exclude": ["exclude_unbound"]},
        "duties": ["exact_citation"],
        "hard_failures": ["false_authority"],
        "thresholds": {
            "minimum_case_pass_rate": 1.0,
            "minimum_duty_coverage": 1.0,
            "maximum_hard_failures": 0,
            "maximum_false_authority": 0,
        },
    }
    cases = []
    for suffix in ("cold", "resume", "compact"):
        case = copy.deepcopy(case_template)
        case["case_id"] = f"goldcase_{suffix}"
        cases.append(case)
    return {
        "schema_version": "deeplaw.semantic-human-gold/v3",
        "status": "semantic_human_gold_frozen",
        "frozen_at": "2026-08-16T00:00:00Z",
        "gold_id": "semanticgold_0123456789abcdef01234567",
        "model_outputs_seen_before_freeze": False,
        "candidate_visible_when_frozen": False,
        "claim_eligible": False,
        "author": {"identity": "human-author:primary", "role": "human_author"},
        "human_approval": {
            "attestation_type": "external_human_attestation",
            "attestation_identity": "attestor:independent-reviewer",
            "attestation_digest": SHA,
            "approval_record": {
                "record_id": "approval:semantic-gold-v3",
                "record_sha256": SHA,
                "issuer": "issuer:human-review-board",
            },
            "approved_at": "2026-08-16T00:01:00Z",
            "decision": "approved",
        },
        "labels": labels,
        "cases": cases,
        "duties": [
            {
                "duty_id": "exact_citation",
                "description": "Every included result retains exact evidence identity.",
            }
        ],
        "hard_failures": [
            {
                "code": "false_authority",
                "description": "Derived text cannot be promoted to source authority.",
            }
        ],
        "thresholds": {
            "minimum_case_pass_rate": 1.0,
            "minimum_duty_coverage": 1.0,
            "maximum_hard_failures": 0,
            "maximum_false_authority": 0,
        },
    }


def _candidate_binding() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.candidate-gold-binding-receipt/v1",
        "status": "post_build_candidate_gold_bound",
        "bound_at": "2026-08-16T01:00:00Z",
        "semantic_gold": {
            "gold_id": "semanticgold_0123456789abcdef01234567",
            "schema_version": "deeplaw.semantic-human-gold/v3",
            "sha256": SHA,
        },
        "candidate": {"commit": COMMIT, "tree": TREE, "lock_sha256": SHA},
        "artifacts": {
            "wheel": {"name": "deeplaw-0.13.0-py3-none-any.whl", "sha256": SHA, "byte_size": 100},
            "sdist": {"name": "deeplaw-0.13.0.tar.gz", "sha256": OTHER_SHA, "byte_size": 120},
        },
        "holdout": {"role": "qualification_holdout", "sha256": SHA},
        "blind": {"role": "final_blind", "sha256": OTHER_SHA},
        "scorer": {"identity": "scorer:pass24-v1", "sha256": SHA},
        "runner": {"identity": "runner:pass24-isolated-v1", "sha256": OTHER_SHA},
        "record_sha256": SHA,
    }


def _pre_publish_gate() -> dict[str, Any]:
    build = {
        "wheel_sha256": SHA,
        "sdist_sha256": OTHER_SHA,
    }
    return {
        "schema_version": "deeplaw.pre-publish-artifact-gate/v1",
        "status": "pre_publish_passed",
        "created_at": "2026-08-16T02:00:00Z",
        "candidate": {"commit": COMMIT, "tree": TREE, "lock_sha256": SHA},
        "builds": {
            "count": 2,
            "byte_identical": True,
            "first": {"build_id": "first", **build, "receipt_sha256": SHA},
            "second": {"build_id": "second", **build, "receipt_sha256": OTHER_SHA},
        },
        "retained_artifacts": {
            "manifest_sha256": SHA,
            "manifest_path": "retained/manifest.json",
            "wheel": {
                "name": "deeplaw-0.13.0-py3-none-any.whl",
                "sha256": SHA,
                "byte_size": 100,
                "retained_path": "retained/deeplaw-0.13.0-py3-none-any.whl",
            },
            "sdist": {
                "name": "deeplaw-0.13.0.tar.gz",
                "sha256": OTHER_SHA,
                "byte_size": 120,
                "retained_path": "retained/deeplaw-0.13.0.tar.gz",
            },
        },
        "sbom": {
            "format": "cyclonedx-json",
            "sha256": SHA,
            "path": "supply/sbom.json",
            "verified": True,
        },
        "openvex": {
            "format": "openvex-json",
            "sha256": OTHER_SHA,
            "path": "supply/openvex.json",
            "verified": True,
        },
        "licenses": {
            "format": "license-report-json",
            "sha256": SHA,
            "path": "supply/licenses.json",
            "verified": True,
        },
        "provenance": {
            "format": "in-toto",
            "sha256": OTHER_SHA,
            "path": "supply/provenance.json",
            "verified": True,
        },
        "record_sha256": SHA,
    }


def _post_public_verification() -> dict[str, Any]:
    artifacts = {"wheel_sha256": SHA, "sdist_sha256": OTHER_SHA}
    return {
        "schema_version": "deeplaw.post-public-verification/v1",
        "status": "post_public_verified",
        "verified_at": "2026-08-16T03:00:00Z",
        "artifact_binding": artifacts,
        "sigstore": {
            "identity": "sigstore:release-workflow",
            "bundle_sha256": SHA,
            "verified": True,
        },
        "attestation": {
            "identity": "attestor:release-workflow",
            "attestation_sha256": OTHER_SHA,
            "verified": True,
        },
        "anonymous_redownload": {
            "attempt_id": "redownload:pass24-1",
            "source": "anonymous_public_redownload",
            **artifacts,
            "sha256_matches": True,
        },
        "public_sha256": artifacts,
        "public_release_verified": True,
        "record_sha256": SHA,
    }


def _post_public_verification_v2() -> dict[str, Any]:
    value = _post_public_verification()
    value["schema_version"] = "deeplaw.post-public-verification/v2"
    value["release_binding"] = {
        "tag": "v0.13.0",
        "commit": COMMIT,
        "tree": TREE,
        "commercial_manifest_sha256": SHA,
        "sha256s_sha256": OTHER_SHA,
    }
    return value


def _release_manifest() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.commercial-release-manifest/v7",
        "environment": {
            "platform_system": "Darwin",
            "platform_release": "25.0.0",
            "platform_version": "25.0.0",
            "machine": "arm64",
            "python_implementation": "CPython",
            "python_version": "3.13.0",
            "python_executable_name": "python3",
            "uv_version": "0.8.0",
            "ci": True,
            "github_actions": True,
            "github_runner_os": "macos",
            "github_runner_arch": "arm64",
        },
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": "0.13.0",
            "tag": "v0.13.0",
            "commit": COMMIT,
            "tree": TREE,
        },
        "run_ids": {
            "candidate_run_id": 130001,
            "evidence_run_id": 130002,
            "qualification_run_id": 130003,
        },
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": SHA,
            "sdist_sha256": OTHER_SHA,
            "version": "0.13.0",
        },
        "artifact_binding": {
            "wheel": {
                "path": "dist/deeplaw-0.13.0-py3-none-any.whl",
                "sha256": SHA,
                "byte_size": 100,
            },
            "sdist": {"path": "dist/deeplaw-0.13.0.tar.gz", "sha256": OTHER_SHA, "byte_size": 120},
            "retained_manifest_sha256": SHA,
        },
        "external_bindings": {
            "semantic_gold_sha256": SHA,
            "holdout_sha256": OTHER_SHA,
            "blind_sha256": SHA,
            "scorer_sha256": OTHER_SHA,
            "runner_sha256": SHA,
            "isolation_sha256": OTHER_SHA,
        },
        "pre_publish_artifact_gate": {
            "path": "receipts/pre-publish-artifact-gate.json",
            "receipt_sha256": SHA,
            "status": "pre_publish_passed",
        },
        "semantic_evidence": {
            "report_path": "evidence/commercial-gate-collection.json",
            "report_sha256": SHA,
            "record_sha256": OTHER_SHA,
            "status": "passed",
            "hard_zero": True,
            "core_gates_passed": True,
        },
        "release_ready": True,
        "public_release_verified": False,
        "post_public_verification": None,
        "claim_eligible": True,
        "commercial_release_eligible": True,
        "quality_protocol_eligible": True,
        "competitive_claim_eligible": False,
        "record_sha256": SHA,
    }


def _assert_transitive_bindings(value: dict[str, Any]) -> None:
    candidate = value["candidate_binding"]
    receipt = value["pre_publish_artifact_gate"]
    if candidate["commit"] != COMMIT or candidate["tree"] != TREE:
        raise AssertionError("candidate commit/tree do not match release provenance")
    if receipt["receipt_sha256"] != SHA:
        raise AssertionError("pre-publish receipt hash does not match the retained receipt")
    if value["run_ids"]["candidate_run_id"] == value["run_ids"]["evidence_run_id"]:
        raise AssertionError("candidate and evidence run IDs must be distinct")
    if value["run_ids"]["evidence_run_id"] == value["run_ids"]["qualification_run_id"]:
        raise AssertionError("evidence and qualification run IDs must be distinct")


def test_all_pass24_contracts_are_closed_and_current() -> None:
    filenames = [
        "semantic-human-gold.v3.schema.json",
        "candidate-gold-binding-receipt.v1.schema.json",
        "pre-publish-artifact-gate.v1.schema.json",
        "post-public-verification.v1.schema.json",
        "post-public-verification.v2.schema.json",
        "commercial-release-manifest.v7.schema.json",
    ]
    for filename in filenames:
        schema = _load_schema(filename)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False


def test_semantic_gold_is_candidate_independent_and_requires_external_approval() -> None:
    value = _semantic_gold()
    _assert_valid("semantic-human-gold.v3.schema.json", value)
    schema = _load_schema("semantic-human-gold.v3.schema.json")
    assert "candidate_commit" not in schema["properties"]
    assert "candidate_tree" not in schema["properties"]
    assert "candidate_wheel_sha256" not in schema["properties"]
    assert "author_is_human" not in schema["properties"]

    candidate_bound = copy.deepcopy(value)
    candidate_bound["candidate_commit"] = COMMIT
    _assert_invalid("semantic-human-gold.v3.schema.json", candidate_bound)
    self_asserted = copy.deepcopy(value)
    self_asserted["author_is_human"] = True
    _assert_invalid("semantic-human-gold.v3.schema.json", self_asserted)

    for field in ("attestation_identity", "attestation_digest", "approval_record"):
        missing = copy.deepcopy(value)
        del missing["human_approval"][field]
        _assert_invalid("semantic-human-gold.v3.schema.json", missing)


def test_candidate_binding_receipt_requires_every_two_phase_hash_and_identity() -> None:
    value = _candidate_binding()
    _assert_valid("candidate-gold-binding-receipt.v1.schema.json", value)
    required_locations = [
        ("semantic_gold", "sha256"),
        ("candidate", "commit"),
        ("candidate", "tree"),
        ("candidate", "lock_sha256"),
        ("artifacts", "wheel"),
        ("artifacts", "sdist"),
        ("holdout", "sha256"),
        ("blind", "sha256"),
        ("scorer", "identity"),
        ("scorer", "sha256"),
        ("runner", "identity"),
        ("runner", "sha256"),
    ]
    for location, field in required_locations:
        missing = copy.deepcopy(value)
        del missing[location][field]
        _assert_invalid("candidate-gold-binding-receipt.v1.schema.json", missing)


def test_pre_publish_gate_has_two_builds_and_no_public_verification_requirement() -> None:
    value = _pre_publish_gate()
    _assert_valid("pre-publish-artifact-gate.v1.schema.json", value)
    assert value["builds"]["byte_identical"] is True
    assert value["builds"]["first"]["wheel_sha256"] == value["builds"]["second"]["wheel_sha256"]
    assert value["builds"]["first"]["sdist_sha256"] == value["builds"]["second"]["sdist_sha256"]

    public_fact = copy.deepcopy(value)
    public_fact["public_release_verified"] = True
    _assert_invalid("pre-publish-artifact-gate.v1.schema.json", public_fact)
    for field in ("sbom", "openvex", "licenses", "provenance", "retained_artifacts"):
        missing = copy.deepcopy(value)
        del missing[field]
        _assert_invalid("pre-publish-artifact-gate.v1.schema.json", missing)


def test_post_public_receipt_is_separate_and_requires_public_sha_and_attestation() -> None:
    value = _post_public_verification()
    _assert_valid("post-public-verification.v1.schema.json", value)
    pre_publish = copy.deepcopy(value)
    pre_publish["status"] = "pre_publish_passed"
    _assert_invalid("post-public-verification.v1.schema.json", pre_publish)
    for field in ("sigstore", "attestation", "anonymous_redownload", "public_sha256"):
        missing = copy.deepcopy(value)
        del missing[field]
        _assert_invalid("post-public-verification.v1.schema.json", missing)


def test_post_public_v2_binds_immutable_release_and_checksum_set() -> None:
    value = _post_public_verification_v2()
    _assert_valid("post-public-verification.v2.schema.json", value)

    for field in (
        "tag",
        "commit",
        "tree",
        "commercial_manifest_sha256",
        "sha256s_sha256",
    ):
        missing = copy.deepcopy(value)
        del missing["release_binding"][field]
        _assert_invalid("post-public-verification.v2.schema.json", missing)

    wrong_commit = copy.deepcopy(value)
    wrong_commit["release_binding"]["commit"] = "not-a-commit"
    _assert_invalid("post-public-verification.v2.schema.json", wrong_commit)

    wrong_tag = copy.deepcopy(value)
    wrong_tag["release_binding"]["tag"] = "release-candidate"
    _assert_invalid("post-public-verification.v2.schema.json", wrong_tag)


def test_v7_release_manifest_requires_three_distinct_runs_and_exact_bindings() -> None:
    value = _release_manifest()
    _assert_valid("commercial-release-manifest.v7.schema.json", value)
    _assert_transitive_bindings(value)

    for field in ("candidate_run_id", "evidence_run_id", "qualification_run_id"):
        missing = copy.deepcopy(value)
        del missing["run_ids"][field]
        _assert_invalid("commercial-release-manifest.v7.schema.json", missing)
    for field in (
        "semantic_gold_sha256",
        "holdout_sha256",
        "blind_sha256",
        "scorer_sha256",
        "runner_sha256",
    ):
        missing = copy.deepcopy(value)
        del missing["external_bindings"][field]
        _assert_invalid("commercial-release-manifest.v7.schema.json", missing)

    mismatched = copy.deepcopy(value)
    mismatched["candidate_binding"]["commit"] = "3" * 40
    _assert_valid("commercial-release-manifest.v7.schema.json", mismatched)
    with pytest.raises(AssertionError, match="commit/tree"):
        _assert_transitive_bindings(mismatched)

    duplicate_run = copy.deepcopy(value)
    duplicate_run["run_ids"]["evidence_run_id"] = duplicate_run["run_ids"]["candidate_run_id"]
    _assert_valid("commercial-release-manifest.v7.schema.json", duplicate_run)
    with pytest.raises(AssertionError, match="distinct"):
        _assert_transitive_bindings(duplicate_run)


def test_v7_separates_release_ready_from_public_release_verified() -> None:
    pre_public = _release_manifest()
    _assert_valid("commercial-release-manifest.v7.schema.json", pre_public)
    assert pre_public["release_ready"] is True
    assert pre_public["claim_eligible"] is True
    assert pre_public["competitive_claim_eligible"] is False
    assert pre_public["public_release_verified"] is False
    assert pre_public["post_public_verification"] is None

    verified = _release_manifest()
    verified["public_release_verified"] = True
    verified["post_public_verification"] = _post_public_verification()
    _assert_valid("commercial-release-manifest.v7.schema.json", verified)

    missing_post = _release_manifest()
    missing_post["public_release_verified"] = True
    _assert_invalid("commercial-release-manifest.v7.schema.json", missing_post)
    unexpected_post = _release_manifest()
    unexpected_post["post_public_verification"] = _post_public_verification()
    _assert_invalid("commercial-release-manifest.v7.schema.json", unexpected_post)


def test_historical_v6_release_schema_bytes_are_unchanged() -> None:
    digest = hashlib.sha256(V6_RELEASE_SCHEMA.read_bytes()).hexdigest()
    assert digest == V6_RELEASE_SCHEMA_SHA256

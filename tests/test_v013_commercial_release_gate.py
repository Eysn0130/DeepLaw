"""Regression gates for the v0.13 commercial-release decision seam.

The positive v6 object below is explicitly a repository-visible *synthetic development* fixture:
it is an in-memory contract-shape probe, never a Human Gold, holdout, qualification result, or
release evidence artifact.  v0.12 compatibility remains a version-selection contract; v0.13 must
not be downgraded to the historical v5/no-model decision.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
V5_MANIFEST = "deeplaw.commercial-release-manifest/v5"
V6_MANIFEST = "deeplaw.commercial-release-manifest/v6"

# Keep each release-blocking input independently addressable.  An aggregate "v6 evidence
# passed" flag would allow a downgrade or a missing hard gate to hide behind one boolean.
REQUIRED_V013_EVIDENCE_PATHS = {
    "bindings.prd_sha256",
    "bindings.traceability_sha256",
    "bindings.qualification_protocol_sha256",
    "bindings.candidate_commit",
    "bindings.candidate_tree",
    "bindings.candidate_wheel_sha256",
    "bindings.candidate_sdist_sha256",
    "qualification.human_gold_manifest_sha256",
    "qualification.compiler_evaluator_isolation_receipt_sha256",
    "host_acceptance.model_task_acceptance",
    "real_codex.independent_runs",
    "real_claude.independent_runs",
    "real_opencode.independent_runs",
    "host_acceptance.declared_supported_hosts",
    "comparison.host_only",
    "comparison.host_native_memory",
    "comparison.host_native_memory_plus_deeplaw",
    "comparison.equal_budget",
    "legal.source_count",
    "legal.false_authority",
    "legal.wrong_version_primary",
    "legal.invalid_quote",
    "legal.invalid_locator",
    "legal.cross_boundary_disclosure",
    "legal.secret_leak",
    "scale.statement_100k",
    "scale.statement_5k",
    "scale.statement_10k",
    "scale.relation_100k",
    "scale.relation_10k",
    "scale.wiki_100k",
    "scale.wiki_10k",
    "scale.requests_10000_rss",
    "scale.readers_8",
    "scale.cache_invalidation",
    "operations.timeline",
    "operations.semantic_restore",
    "operations.selective_forget",
    "platform_gates.systems",
    "platform_gates.python_versions",
    "platform_gates.mandatory_skips",
    "supply_chain.reproducible_wheel_sdist",
    "supply_chain.sbom",
    "supply_chain.licenses",
    "supply_chain.openvex",
    "supply_chain.provenance",
    "supply_chain.public_redownload",
}

_SYNTHETIC_VERSION = "0.13.0"
_SYNTHETIC_COMMIT = "a" * 40
_SYNTHETIC_TREE = "b" * 40
_SYNTHETIC_MARKER = "repository-visible-synthetic-development-only"


def _release_policy() -> Any:
    """Load the shared seam lazily so static workflow failures remain visible as well."""

    return importlib.import_module("benchmarks.release.release_policy")


def _expect_rejected(
    validator: Callable[..., Any], manifest: dict[str, Any], *, release_version: str
) -> Exception:
    try:
        validator(manifest, release_version=release_version)
    except Exception as error:  # The policy owns its concrete error class.
        return error
    pytest.fail(
        "validate_manifest_for_release unexpectedly accepted a release decision: "
        f"version={release_version}, schema={manifest.get('schema_version')}"
    )


def _synthetic_v6_development_manifest() -> dict[str, Any]:
    """Build an in-memory contract fixture; this is never persisted or release evidence."""

    policy = _release_policy()
    artifacts: list[dict[str, Any]] = []

    def add_artifact(name: str) -> tuple[str, str]:
        payload = f"{_SYNTHETIC_MARKER}:{name}".encode()
        digest = hashlib.sha256(payload).hexdigest()
        artifacts.append({"path": name, "sha256": digest, "byte_size": len(payload)})
        return name, digest

    def evidence(name: str) -> tuple[str, str]:
        return add_artifact(f"synthetic/development/{name}.json")

    _wheel_path, wheel_sha256 = add_artifact(
        f"dist/deeplaw-{_SYNTHETIC_VERSION}-py3-none-any.whl"
    )
    _sdist_path, sdist_sha256 = add_artifact(f"dist/deeplaw-{_SYNTHETIC_VERSION}.tar.gz")
    prd_path, prd_sha256 = evidence("prd-contract")
    traceability_path, traceability_sha256 = evidence("traceability-contract")
    protocol_path, protocol_sha256 = evidence("protocol-contract")
    human_path, human_sha256 = evidence("external-reference-contract-shape")
    isolation_path, isolation_sha256 = evidence("compiler-evaluator-isolation")
    host_acceptance_path, host_acceptance_sha256 = evidence("host-acceptance")
    host_evidence = {
        name: evidence(f"{name}-host-run") for name in ("codex", "claude", "opencode")
    }
    comparison_paths = {
        name: evidence(f"comparison-{name}")
        for name in ("host-only", "host-native-memory", "host-native-memory-plus-deeplaw")
    }
    equal_budget_path, equal_budget_sha256 = evidence("comparison-equal-budget")
    legal_path, legal_sha256 = evidence("legal-scorer")
    pack_path, pack_sha256 = evidence("legal-pack-manifest")
    catalog_path, catalog_sha256 = evidence("legal-catalog")
    legal_release_path, legal_release_sha256 = evidence("legal-pack-release")
    scale_names = (
        "current_candidate",
        "statement_5k",
        "statement_10k",
        "statement_100k",
        "relation_10k",
        "relation_100k",
        "wiki_10k",
        "wiki_100k",
        "requests_10000_rss",
        "readers_8",
        "cache_invalidation",
    )
    scale_paths = {
        name: evidence(f"scale-{name.replace('_', '-')}") for name in scale_names
    }
    operation_paths = {
        name: evidence(f"operation-{name.replace('_', '-')}")
        for name in ("timeline", "semantic_restore", "selective_forget")
    }
    platform_report_path, platform_report_sha256 = evidence("platform-matrix")
    platform_rows = []
    for system in ("Darwin", "Linux", "Windows"):
        for python_version in ("3.11", "3.12", "3.13"):
            row_path, row_sha256 = evidence(f"platform-{system.lower()}-{python_version}")
            platform_rows.append(
                {
                    "system": system,
                    "python_version": python_version,
                    "report_path": row_path,
                    "report_sha256": row_sha256,
                }
            )
    supply_paths = {
        name: evidence(f"supply-{name}")
        for name in (
            "reproducible-wheel-sdist",
            "sbom",
            "licenses",
            "openvex",
            "provenance",
            "public-redownload",
        )
    }

    manifest: dict[str, Any] = {
        "schema_version": V6_MANIFEST,
        "environment": {
            "platform_system": "Synthetic",
            "platform_release": "development",
            "platform_version": "synthetic",
            "machine": "synthetic",
            "python_implementation": "CPython",
            "python_version": "3.12.0",
            "python_executable_name": "synthetic-python",
            "uv_version": "synthetic-uv",
            "ci": False,
            "github_actions": False,
            "github_runner_os": None,
            "github_runner_arch": None,
        },
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": _SYNTHETIC_VERSION,
            "tag": f"v{_SYNTHETIC_VERSION}",
            "commit": _SYNTHETIC_COMMIT,
            "tree": _SYNTHETIC_TREE,
        },
        "bindings": {
            "prd_path": prd_path,
            "prd_sha256": prd_sha256,
            "traceability_path": traceability_path,
            "traceability_sha256": traceability_sha256,
            "qualification_protocol_path": protocol_path,
            "qualification_protocol_sha256": protocol_sha256,
            "candidate_commit": _SYNTHETIC_COMMIT,
            "candidate_tree": _SYNTHETIC_TREE,
            "candidate_wheel_sha256": wheel_sha256,
            "candidate_sdist_sha256": sdist_sha256,
            "candidate_version": _SYNTHETIC_VERSION,
        },
        "artifacts": artifacts,
        "qualification": {
            "human_gold_manifest_path": human_path,
            "human_gold_manifest_sha256": human_sha256,
            "human_gold_origin": "repository_external",
            "human_gold_model_output": False,
            "compiler_evaluator_isolation_receipt_path": isolation_path,
            "compiler_evaluator_isolation_receipt_sha256": isolation_sha256,
            "compiler_evaluator_isolated": True,
        },
        "host_acceptance": {
            "report_path": host_acceptance_path,
            "report_sha256": host_acceptance_sha256,
            "model_task_acceptance": True,
            "model_task_results_claimed": True,
            "model_or_api_call_attempted": True,
            "declared_supported_hosts": ["claude_code", "codex", "opencode"],
        },
        "real_codex": {
            "independent_runs": 3,
            "model_task_acceptance": True,
            "model_or_api_call_attempted": True,
            "evidence_path": host_evidence["codex"][0],
            "evidence_sha256": host_evidence["codex"][1],
        },
        "real_claude": {
            "independent_runs": 3,
            "model_task_acceptance": True,
            "model_or_api_call_attempted": True,
            "evidence_path": host_evidence["claude"][0],
            "evidence_sha256": host_evidence["claude"][1],
        },
        "real_opencode": {
            "independent_runs": 3,
            "model_task_acceptance": True,
            "model_or_api_call_attempted": True,
            "evidence_path": host_evidence["opencode"][0],
            "evidence_sha256": host_evidence["opencode"][1],
        },
        "comparison": {
            "host_only": {
                "passed": True,
                "first_correct_action": 1,
                "report_path": comparison_paths["host-only"][0],
                "report_sha256": comparison_paths["host-only"][1],
                "incremental_benefit": False,
            },
            "host_native_memory": {
                "passed": True,
                "first_correct_action": 2,
                "report_path": comparison_paths["host-native-memory"][0],
                "report_sha256": comparison_paths["host-native-memory"][1],
                "incremental_benefit": False,
            },
            "host_native_memory_plus_deeplaw": {
                "passed": True,
                "first_correct_action": 3,
                "report_path": comparison_paths["host-native-memory-plus-deeplaw"][0],
                "report_sha256": comparison_paths["host-native-memory-plus-deeplaw"][1],
                "incremental_benefit": True,
            },
            "equal_budget": True,
            "equal_budget_report_path": equal_budget_path,
            "equal_budget_report_sha256": equal_budget_sha256,
        },
        "legal": {
            "report_path": legal_path,
            "report_sha256": legal_sha256,
            "pack_manifest_path": pack_path,
            "pack_manifest_sha256": pack_sha256,
            "catalog_path": catalog_path,
            "catalog_sha256": catalog_sha256,
            "release_path": legal_release_path,
            "release_sha256": legal_release_sha256,
            "source_count": 28,
            "signed_and_verified": True,
            "critical_failures": 0,
            "false_authority": 0,
            "wrong_version_primary": 0,
            "invalid_quote": 0,
            "invalid_locator": 0,
            "cross_boundary_disclosure": 0,
            "secret_leak": 0,
        },
        "scale": {
            name: True for name in scale_names
        },
        "operations": {
            name: True for name in ("timeline", "semantic_restore", "selective_forget")
        },
        "platform_gates": {
            "systems": ["Darwin", "Linux", "Windows"],
            "python_versions": ["3.11", "3.12", "3.13"],
            "matrix": platform_rows,
            "mandatory_skips": 0,
            "matrix_complete": True,
            "report_path": platform_report_path,
            "report_sha256": platform_report_sha256,
        },
        "supply_chain": {name: True for name in (
            "reproducible_wheel_sdist",
            "sbom",
            "licenses",
            "openvex",
            "provenance",
            "public_redownload",
        )},
        "commercial_release_eligible": True,
        "quality_protocol_eligible": True,
        "competitive_claim_eligible": False,
    }
    for name, pair in scale_paths.items():
        manifest["scale"][f"{name}_report_path"] = pair[0]
        manifest["scale"][f"{name}_report_sha256"] = pair[1]
    for name, pair in operation_paths.items():
        manifest["operations"][f"{name}_report_path"] = pair[0]
        manifest["operations"][f"{name}_report_sha256"] = pair[1]
    for name, pair in supply_paths.items():
        manifest["supply_chain"][f"{name.replace('-', '_')}_path"] = pair[0]
        manifest["supply_chain"][f"{name.replace('-', '_')}_sha256"] = pair[1]
    manifest["record_sha256"] = policy._record_digest(manifest)
    return manifest


def _mutated_synthetic_manifest(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Apply one explicit mutation and recompute the outer record digest in memory."""

    manifest = _synthetic_v6_development_manifest()
    mutator(manifest)
    policy = _release_policy()
    manifest["record_sha256"] = policy._record_digest(manifest)
    return manifest


def _workflow_section(text: str, start: str, end: str | None = None) -> str:
    section = text.split(start, maxsplit=1)[1]
    if end is not None:
        section = section.split(end, maxsplit=1)[0]
    return section


def test_version_policy_keeps_historical_v5_and_selects_v6_for_v013() -> None:
    policy = _release_policy()

    assert policy.required_manifest_schema_version("0.12.0") == V5_MANIFEST
    assert policy.required_manifest_schema_version("0.12.9") == V5_MANIFEST
    assert policy.required_manifest_schema_version("0.13.0") == V6_MANIFEST
    assert policy.required_manifest_schema_version("0.13.9") == V6_MANIFEST

    # Compatibility selects v5 for the historical line, but the validator still rejects an
    # incomplete manifest.  This avoids turning the compatibility assertion into a weak bypass.
    legacy_error = _expect_rejected(
        policy.validate_manifest_for_release,
        {"schema_version": V5_MANIFEST},
        release_version="0.12.0",
    )
    assert "v5" in str(legacy_error).casefold() or "incomplete" in str(legacy_error).casefold()

    error = _expect_rejected(
        policy.validate_manifest_for_release,
        {"schema_version": V5_MANIFEST},
        release_version="0.13.0",
    )
    assert "v5" in str(error).casefold() or "0.13" in str(error)

    with pytest.raises(policy.ReleasePolicyError, match="no commercial release policy"):
        policy.required_manifest_schema_version("0.14.0")
    with pytest.raises(policy.ReleasePolicyError, match="no commercial release policy"):
        policy.required_manifest_schema_version("1.0.0")


def test_v013_policy_exposes_independent_hard_evidence_and_fails_closed() -> None:
    policy = _release_policy()

    assert isinstance(policy.V013_REQUIRED_EVIDENCE_PATHS, tuple)
    exposed = set(policy.V013_REQUIRED_EVIDENCE_PATHS)
    assert exposed >= REQUIRED_V013_EVIDENCE_PATHS

    # No fake v6 evidence is supplied.  A schema-version-only object must fail closed before a
    # commercial decision can be accepted.
    error = _expect_rejected(
        policy.validate_manifest_for_release,
        {"schema_version": V6_MANIFEST},
        release_version="0.13.0",
    )
    assert "0.13" in str(error) or "v6" in str(error).casefold()


def test_v013_evidence_refs_are_closed_and_record_digest_is_recomputed() -> None:
    policy = _release_policy()
    required = set(policy.V013_REQUIRED_EVIDENCE_PATHS)
    assert {
        "qualification.human_gold_manifest_path",
        "qualification.human_gold_manifest_sha256",
        "real_codex.evidence_path",
        "real_codex.evidence_sha256",
        "comparison.host_only.report_path",
        "comparison.host_only.report_sha256",
        "legal.pack_manifest_path",
        "legal.pack_manifest_sha256",
        "scale.statement_5k_report_path",
        "scale.statement_5k_report_sha256",
        "scale.statement_10k_report_path",
        "scale.statement_10k_report_sha256",
        "scale.statement_100k_report_path",
        "scale.statement_100k_report_sha256",
        "scale.relation_10k_report_path",
        "scale.relation_10k_report_sha256",
        "scale.wiki_10k_report_path",
        "scale.wiki_10k_report_sha256",
        "operations.semantic_restore_report_path",
        "operations.semantic_restore_report_sha256",
        "platform_gates.matrix[].report_path",
        "platform_gates.matrix[].report_sha256",
        "supply_chain.sbom_path",
        "supply_chain.sbom_sha256",
    } <= required

    payload = {"schema_version": V6_MANIFEST, "evidence": ["external"]}
    valid = {**payload, "record_sha256": policy._record_digest(payload)}
    policy._verify_record_digest(valid)
    tampered = {**valid, "evidence": ["changed"]}
    with pytest.raises(policy.ReleasePolicyError, match="record_sha256"):
        policy._verify_record_digest(tampered)

    schema = json.loads(
        (REPOSITORY / "contracts/commercial-release-manifest.v6.schema.json").read_text(
            encoding="utf-8"
        )
    )
    for section in (
        "qualification",
        "host_acceptance",
        "comparison",
        "legal",
        "scale",
        "operations",
        "platform_gates",
        "supply_chain",
    ):
        assert schema["properties"][section].get("additionalProperties") is False
    assert schema["$defs"]["real-host"]["additionalProperties"] is False
    assert schema["$defs"]["comparison-lane"]["additionalProperties"] is False
    assert schema["properties"]["artifacts"]["items"]["additionalProperties"] is False


def test_repository_visible_synthetic_v6_shape_is_contract_self_consistent_only() -> None:
    """A complete in-memory shape may pass policy without becoming release evidence."""

    policy = _release_policy()
    manifest = _synthetic_v6_development_manifest()
    policy.validate_manifest_for_release(manifest, release_version=_SYNTHETIC_VERSION)
    assert all(
        item["path"].startswith("synthetic/development/")
        for item in manifest["artifacts"]
        if item["path"].startswith("synthetic/")
    )


@pytest.mark.parametrize(
    ("case", "mutator", "error_fragment"),
    [
        pytest.param(
            "v5 downgrade",
            lambda manifest: manifest.__setitem__("schema_version", V5_MANIFEST),
            "requires",
            id="v5-at-v013",
        ),
        pytest.param(
            "no-model host acceptance",
            lambda manifest: manifest["host_acceptance"].__setitem__(
                "model_task_acceptance", False
            ),
            "host_acceptance.model_task_acceptance",
            id="no-model-is-not-model-acceptance",
        ),
        pytest.param(
            "real Codex fewer than three runs",
            lambda manifest: manifest["real_codex"].__setitem__("independent_runs", 2),
            "real_codex.independent_runs",
            id="real-host-run-count",
        ),
        pytest.param(
            "critical legal counter",
            lambda manifest: manifest["legal"].__setitem__("critical_failures", 1),
            "legal.critical_failures",
            id="legal-critical-counter",
        ),
        pytest.param(
            "synthetic origin claim",
            lambda manifest: manifest["qualification"].__setitem__(
                "human_gold_origin", "synthetic_development"
            ),
            "qualification.human_gold_origin",
            id="origin-must-be-external",
        ),
        pytest.param(
            "comparison has no incremental benefit",
            lambda manifest: manifest["comparison"][
                "host_native_memory_plus_deeplaw"
            ].__setitem__("incremental_benefit", False),
            "incremental_benefit",
            id="comparison-incremental-benefit",
        ),
        pytest.param(
            "DeepLaw First Correct Action regresses",
            lambda manifest: manifest["comparison"][
                "host_native_memory_plus_deeplaw"
            ].__setitem__("first_correct_action", 0),
            "lowers First Correct Action",
            id="comparison-first-correct-action",
        ),
        pytest.param(
            "platform matrix is short",
            lambda manifest: manifest["platform_gates"]["matrix"].pop(),
            "exactly nine rows",
            id="platform-matrix-missing",
        ),
        pytest.param(
            "platform matrix repeats a row",
            lambda manifest: manifest["platform_gates"]["matrix"].__setitem__(
                1, copy.deepcopy(manifest["platform_gates"]["matrix"][0])
            ),
            "reused by multiple release gates",
            id="platform-matrix-duplicate",
        ),
        pytest.param(
            "mandatory platform skip",
            lambda manifest: manifest["platform_gates"].__setitem__("mandatory_skips", 1),
            "platform_gates.mandatory_skips",
            id="platform-mandatory-skip",
        ),
        pytest.param(
            "candidate commit binding",
            lambda manifest: manifest["bindings"].__setitem__("candidate_commit", "c" * 40),
            "bindings.candidate_commit",
            id="candidate-commit-binding",
        ),
        pytest.param(
            "candidate tree binding",
            lambda manifest: manifest["bindings"].__setitem__("candidate_tree", "d" * 40),
            "bindings.candidate_tree",
            id="candidate-tree-binding",
        ),
        pytest.param(
            "candidate wheel binding",
            lambda manifest: manifest["bindings"].__setitem__(
                "candidate_wheel_sha256", "e" * 64
            ),
            "artifacts.wheel.sha256",
            id="candidate-wheel-binding",
        ),
        pytest.param(
            "candidate sdist binding",
            lambda manifest: manifest["bindings"].__setitem__(
                "candidate_sdist_sha256", "f" * 64
            ),
            "artifacts.sdist.sha256",
            id="candidate-sdist-binding",
        ),
        pytest.param(
            "evidence path absent from artifacts",
            lambda manifest: manifest["real_codex"].__setitem__(
                "evidence_path", "synthetic/development/missing.json"
            ),
            "not present in manifest artifacts",
            id="evidence-path-binding",
        ),
        pytest.param(
            "evidence hash differs from artifact",
            lambda manifest: manifest["real_codex"].__setitem__(
                "evidence_sha256", "1" * 64
            ),
            "does not match the artifact bytes",
            id="evidence-hash-binding",
        ),
        pytest.param(
            "duplicate evidence artifact",
            lambda manifest: manifest["artifacts"].append(
                copy.deepcopy(manifest["artifacts"][2])
            ),
            "artifacts contains duplicate path",
            id="duplicate-evidence-artifact",
        ),
    ],
)
def test_synthetic_v6_mutations_cannot_bypass_hard_gates(
    case: str,
    mutator: Callable[[dict[str, Any]], None],
    error_fragment: str,
) -> None:
    policy = _release_policy()
    manifest = _mutated_synthetic_manifest(mutator)
    error = _expect_rejected(
        policy.validate_manifest_for_release,
        manifest,
        release_version=_SYNTHETIC_VERSION,
    )
    assert error_fragment.casefold() in str(error).casefold(), case


@pytest.mark.parametrize(
    ("mutator", "error_fragment"),
    [
        pytest.param(
            lambda manifest: manifest["scale"].__setitem__("statement_5k", False),
            "scale.statement_5k",
            id="statement-5k-false",
        ),
        pytest.param(
            lambda manifest: manifest["scale"].pop("statement_10k_report_path"),
            "statement_10k_report_path",
            id="statement-10k-path-missing",
        ),
        pytest.param(
            lambda manifest: manifest["scale"].__setitem__(
                "wiki_10k_report_sha256", "0" * 64
            ),
            "does not match the artifact bytes",
            id="wiki-10k-hash-mismatch",
        ),
    ],
)
def test_new_statement_relation_wiki_scale_gates_are_independently_bound(
    mutator: Callable[[dict[str, Any]], None], error_fragment: str
) -> None:
    policy = _release_policy()
    manifest = _mutated_synthetic_manifest(mutator)
    error = _expect_rejected(
        policy.validate_manifest_for_release,
        manifest,
        release_version=_SYNTHETIC_VERSION,
    )
    assert error_fragment.casefold() in str(error).casefold()


def test_synthetic_v6_record_digest_tamper_and_future_version_fail_closed() -> None:
    policy = _release_policy()
    tampered = _synthetic_v6_development_manifest()
    tampered["record_sha256"] = "0" * 64
    error = _expect_rejected(
        policy.validate_manifest_for_release,
        tampered,
        release_version=_SYNTHETIC_VERSION,
    )
    assert "record_sha256" in str(error)

    future_error = _expect_rejected(
        policy.validate_manifest_for_release,
        _synthetic_v6_development_manifest(),
        release_version="0.14.0",
    )
    assert "no commercial release policy" in str(future_error)


def test_publish_and_post_release_call_the_same_version_conditional_policy() -> None:
    workflow = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")
    publish = _workflow_section(workflow, "\n  publish:", "\n  post-release:")
    post_release = _workflow_section(workflow, "\n  post-release:")

    for path in (publish, post_release):
        assert "benchmarks.release.release_policy" in path
        assert "commercial-release-manifest.json" in path
        assert (
            "RELEASE_VERSION" in path
            or "needs.release-context.outputs.version" in path
        )
        assert V5_MANIFEST not in path
    assert "commercial manifest bytes differ" in publish
    assert "manifest_asset_names" in publish
    assert 'path.stat().st_size != record.get(' in publish


def test_no_model_host_smoke_is_explicitly_not_model_task_acceptance_for_v013() -> None:
    workflow = (REPOSITORY / ".github/workflows/commercial-gates.yml").read_text(
        encoding="utf-8"
    )
    no_model = _workflow_section(workflow, "\n  no-model-hosts:", "\n  oci:")
    lowered = no_model.casefold()

    assert "run_no_model_host_acceptance" in no_model
    assert "model_task_acceptance" in lowered
    for line in lowered.splitlines():
        if "model_task_acceptance" in line:
            assert "true" not in line
            assert "false" in line or "== 0" in line or "== false" in line

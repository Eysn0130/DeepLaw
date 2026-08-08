"""Regression gates for the v0.13 commercial-release decision seam.

These tests intentionally exercise the release-policy boundary rather than assembling a fake
successful v6 evidence bundle.  v0.12 compatibility remains a version-selection contract;
v0.13 must not be downgraded to the historical v5/no-model decision.
"""

from __future__ import annotations

import importlib
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
    "scale.relation_100k",
    "scale.wiki_100k",
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

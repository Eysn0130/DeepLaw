"""Version-conditional commercial release policy.

This module deliberately uses only the Python standard library.  It is invoked by release
workflow steps before project dependencies are installed, so it must remain importable from a
clean checkout.  The v0.12 manifest is a compatibility contract; v0.13 introduces an independent
v6 decision contract and cannot be downgraded to the historical v5/no-model decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

V5_MANIFEST_SCHEMA = "deeplaw.commercial-release-manifest/v5"
V6_MANIFEST_SCHEMA = "deeplaw.commercial-release-manifest/v6"

_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_RE = re.compile(r"^[0-9a-f]{40}$")

# Every item is intentionally a separate path.  A single aggregate ``passed`` field cannot hide
# a missing model run, a critical legal failure, or a supply-chain verification omission.
V013_REQUIRED_EVIDENCE_PATHS: tuple[str, ...] = (
    "bindings.prd_path",
    "bindings.traceability_path",
    "bindings.qualification_protocol_path",
    "bindings.prd_sha256",
    "bindings.traceability_sha256",
    "bindings.qualification_protocol_sha256",
    "bindings.candidate_commit",
    "bindings.candidate_tree",
    "bindings.candidate_wheel_sha256",
    "bindings.candidate_sdist_sha256",
    "qualification.human_gold_manifest_sha256",
    "qualification.human_gold_manifest_path",
    "qualification.compiler_evaluator_isolation_receipt_sha256",
    "qualification.compiler_evaluator_isolation_receipt_path",
    "host_acceptance.report_path",
    "host_acceptance.report_sha256",
    "host_acceptance.model_task_acceptance",
    "host_acceptance.model_task_results_claimed",
    "host_acceptance.model_or_api_call_attempted",
    "real_codex.evidence_path",
    "real_codex.evidence_sha256",
    "real_claude.evidence_path",
    "real_claude.evidence_sha256",
    "real_opencode.evidence_path",
    "real_opencode.evidence_sha256",
    "real_codex.independent_runs",
    "real_claude.independent_runs",
    "real_opencode.independent_runs",
    "real_codex.model_task_acceptance",
    "real_claude.model_task_acceptance",
    "real_opencode.model_task_acceptance",
    "real_codex.model_or_api_call_attempted",
    "real_claude.model_or_api_call_attempted",
    "real_opencode.model_or_api_call_attempted",
    "host_acceptance.declared_supported_hosts",
    "comparison.host_only",
    "comparison.host_native_memory",
    "comparison.host_native_memory_plus_deeplaw",
    "comparison.equal_budget",
    "comparison.equal_budget_report_path",
    "comparison.equal_budget_report_sha256",
    "comparison.host_only.report_path",
    "comparison.host_only.report_sha256",
    "comparison.host_native_memory.report_path",
    "comparison.host_native_memory.report_sha256",
    "comparison.host_native_memory_plus_deeplaw.report_path",
    "comparison.host_native_memory_plus_deeplaw.report_sha256",
    "legal.source_count",
    "legal.signed_and_verified",
    "legal.critical_failures",
    "legal.report_path",
    "legal.report_sha256",
    "legal.pack_manifest_path",
    "legal.pack_manifest_sha256",
    "legal.catalog_path",
    "legal.catalog_sha256",
    "legal.release_path",
    "legal.release_sha256",
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
    "scale.current_candidate_report_path",
    "scale.current_candidate_report_sha256",
    "scale.statement_5k_report_path",
    "scale.statement_5k_report_sha256",
    "scale.statement_10k_report_path",
    "scale.statement_10k_report_sha256",
    "scale.statement_100k_report_path",
    "scale.statement_100k_report_sha256",
    "scale.relation_10k_report_path",
    "scale.relation_10k_report_sha256",
    "scale.relation_100k_report_path",
    "scale.relation_100k_report_sha256",
    "scale.wiki_10k_report_path",
    "scale.wiki_10k_report_sha256",
    "scale.wiki_100k_report_path",
    "scale.wiki_100k_report_sha256",
    "scale.requests_10000_rss_report_path",
    "scale.requests_10000_rss_report_sha256",
    "scale.readers_8_report_path",
    "scale.readers_8_report_sha256",
    "scale.cache_invalidation_report_path",
    "scale.cache_invalidation_report_sha256",
    "operations.timeline",
    "operations.semantic_restore",
    "operations.selective_forget",
    "operations.timeline_report_path",
    "operations.timeline_report_sha256",
    "operations.semantic_restore_report_path",
    "operations.semantic_restore_report_sha256",
    "operations.selective_forget_report_path",
    "operations.selective_forget_report_sha256",
    "platform_gates.systems",
    "platform_gates.python_versions",
    "platform_gates.mandatory_skips",
    "platform_gates.report_path",
    "platform_gates.report_sha256",
    "platform_gates.matrix[].report_path",
    "platform_gates.matrix[].report_sha256",
    "supply_chain.reproducible_wheel_sdist",
    "supply_chain.sbom",
    "supply_chain.licenses",
    "supply_chain.openvex",
    "supply_chain.provenance",
    "supply_chain.public_redownload",
    "supply_chain.reproducible_wheel_sdist_path",
    "supply_chain.reproducible_wheel_sdist_sha256",
    "supply_chain.sbom_path",
    "supply_chain.sbom_sha256",
    "supply_chain.licenses_path",
    "supply_chain.licenses_sha256",
    "supply_chain.openvex_path",
    "supply_chain.openvex_sha256",
    "supply_chain.provenance_path",
    "supply_chain.provenance_sha256",
    "supply_chain.public_redownload_path",
    "supply_chain.public_redownload_sha256",
)

_V5_REQUIRED_TOP_LEVEL = frozenset(
    {
        "schema_version",
        "environment",
        "release",
        "bindings",
        "artifacts",
        "platform_gates",
        "host_acceptance",
        "supply_chain",
        "commercial_gates",
        "evaluation_protocol",
        "living_wiki_quality",
        "semantic_living_wiki_quality",
        "authoritative_source_quality",
        "authoritative_evidence_quality",
        "editor_integrations",
        "release_documentation",
        "commercial_release_eligible",
        "quality_protocol_eligible",
        "competitive_claim_eligible",
        "competitive_evidence_missing",
        "claim_policy",
        "record_sha256",
    }
)

_V6_REQUIRED_TOP_LEVEL = frozenset(
    {
        "schema_version",
        "environment",
        "release",
        "bindings",
        "artifacts",
        "qualification",
        "host_acceptance",
        "real_codex",
        "real_claude",
        "real_opencode",
        "comparison",
        "legal",
        "scale",
        "operations",
        "platform_gates",
        "supply_chain",
        "commercial_release_eligible",
        "quality_protocol_eligible",
        "competitive_claim_eligible",
        "record_sha256",
    }
)

_V6_ENVIRONMENT_FIELDS = {
    "platform_system",
    "platform_release",
    "platform_version",
    "machine",
    "python_implementation",
    "python_version",
    "python_executable_name",
    "uv_version",
    "ci",
    "github_actions",
    "github_runner_os",
    "github_runner_arch",
}


class ReleasePolicyError(RuntimeError):
    """Raised when a manifest cannot be admitted for the requested release version."""


def _semver(version: str) -> tuple[int, int, int]:
    if not isinstance(version, str):
        raise ReleasePolicyError("release version must be a pure semver string")
    match = _SEMVER_RE.fullmatch(version)
    if match is None:
        raise ReleasePolicyError(f"release version is not pure semver: {version!r}")
    return tuple(int(part) for part in match.groups())


def required_manifest_schema_version(version: str) -> str:
    """Return the manifest schema required by a release package version."""

    major, minor, _patch = _semver(version)
    if (major, minor) < (0, 13):
        return V5_MANIFEST_SCHEMA
    if (major, minor) == (0, 13):
        return V6_MANIFEST_SCHEMA
    raise ReleasePolicyError(f"no commercial release policy is defined for {version}")


def _fail(message: str) -> None:
    raise ReleasePolicyError(message)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{path} must be an object")
    return value


def _required(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for component in path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            _fail(f"required release evidence is missing: {path}")
        current = current[component]
    return current


def _sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(f"{path} must be a lowercase SHA-256 digest")
    return value


def _canonical_json(value: Any) -> str:
    """Match ``benchmarks.release.evidence.canonical_json`` without importing dependencies."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _verify_record_digest(manifest: Mapping[str, Any]) -> None:
    expected = _record_digest(manifest)
    if manifest.get("record_sha256") != expected:
        _fail("record_sha256 does not match the canonical release manifest bytes")


def _safe_relative_path(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{path} must be a non-empty safe relative artifact path")
    if "\\" in value or value.startswith("/") or value.startswith("\\"):
        _fail(f"{path} must be a safe relative artifact path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(f"{path} must be a safe relative artifact path")
    if ":" in parts[0]:
        _fail(f"{path} must not contain a drive-qualified path")
    return value


def _evidence_ref(
    manifest: Mapping[str, Any],
    artifact_index: Mapping[str, Mapping[str, Any]],
    *,
    path: str,
    hash_path: str,
    seen: set[str],
) -> tuple[str, str]:
    return _evidence_ref_values(
        artifact_index,
        path_value=_required(manifest, path),
        hash_value=_required(manifest, hash_path),
        path=path,
        hash_path=hash_path,
        seen=seen,
    )


def _evidence_ref_values(
    artifact_index: Mapping[str, Mapping[str, Any]],
    *,
    path_value: Any,
    hash_value: Any,
    path: str,
    hash_path: str,
    seen: set[str],
) -> tuple[str, str]:
    logical_path = _safe_relative_path(path_value, path)
    digest = _sha256(hash_value, hash_path)
    artifact = artifact_index.get(logical_path)
    if artifact is None:
        _fail(f"{path} is not present in manifest artifacts")
    if artifact.get("sha256") != digest:
        _fail(f"{hash_path} does not match the artifact bytes for {logical_path}")
    if logical_path in seen:
        _fail(f"evidence artifact is reused by multiple release gates: {logical_path}")
    seen.add(logical_path)
    return logical_path, digest


def _git(value: Any, path: str) -> str:
    if not isinstance(value, str) or _GIT_RE.fullmatch(value) is None:
        _fail(f"{path} must be a lowercase 40-character commit/tree id")
    return value


def _exact(value: Any, expected: Any, path: str) -> None:
    if value != expected:
        _fail(f"{path} does not match the release policy")


def _nonnegative_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{path} must be a non-negative integer")
    return value


def _true(value: Any, path: str) -> None:
    if value is not True:
        _fail(f"{path} must be true")


def _v5_manifest(manifest: Mapping[str, Any], release_version: str) -> None:
    missing = sorted(_V5_REQUIRED_TOP_LEVEL - set(manifest))
    if missing:
        _fail(f"v5 manifest is incomplete; missing fields: {', '.join(missing)}")
    unexpected = sorted(set(manifest) - _V5_REQUIRED_TOP_LEVEL)
    if unexpected:
        _fail(f"v5 manifest contains unsupported fields: {', '.join(unexpected)}")
    release = _mapping(manifest["release"], "release")
    _exact(release.get("version"), release_version, "release.version")
    _exact(release.get("tag"), f"v{release_version}", "release.tag")
    _git(release.get("commit"), "release.commit")
    _git(release.get("tree"), "release.tree")
    _exact(manifest["commercial_release_eligible"], True, "commercial_release_eligible")
    _exact(manifest["quality_protocol_eligible"], True, "quality_protocol_eligible")
    _exact(manifest["competitive_claim_eligible"], False, "competitive_claim_eligible")
    platform = _mapping(manifest["platform_gates"], "platform_gates")
    _exact(platform.get("mandatory_skips"), 0, "platform_gates.mandatory_skips")
    host = _mapping(manifest["host_acceptance"], "host_acceptance")
    _exact(host.get("model_task_acceptance"), False, "host_acceptance.model_task_acceptance")
    _exact(
        host.get("model_task_results_claimed"),
        False,
        "host_acceptance.model_task_results_claimed",
    )
    _verify_record_digest(manifest)


def _v6_manifest(manifest: Mapping[str, Any], release_version: str) -> None:
    missing = sorted(_V6_REQUIRED_TOP_LEVEL - set(manifest))
    if missing:
        _fail(f"v6 manifest is incomplete; missing fields: {', '.join(missing)}")
    unexpected = sorted(set(manifest) - _V6_REQUIRED_TOP_LEVEL)
    if unexpected:
        _fail(f"v6 manifest contains unsupported fields: {', '.join(unexpected)}")

    def closed(value: Any, path: str, fields: set[str]) -> Mapping[str, Any]:
        item = _mapping(value, path)
        missing_fields = sorted(fields - set(item))
        if missing_fields:
            _fail(f"{path} is incomplete; missing fields: {', '.join(missing_fields)}")
        unexpected_fields = sorted(set(item) - fields)
        if unexpected_fields:
            _fail(f"{path} contains unsupported fields: {', '.join(unexpected_fields)}")
        return item

    closed(manifest["environment"], "environment", _V6_ENVIRONMENT_FIELDS)
    release = closed(
        manifest["release"],
        "release",
        {"repository", "version", "tag", "commit", "tree"},
    )
    _exact(release["repository"], "Eysn0130/DeepLaw", "release.repository")
    _exact(release["version"], release_version, "release.version")
    _exact(release["tag"], f"v{release_version}", "release.tag")
    release_commit = _git(release["commit"], "release.commit")
    release_tree = _git(release["tree"], "release.tree")

    bindings = closed(
        manifest["bindings"],
        "bindings",
        {
            "prd_path",
            "prd_sha256",
            "traceability_path",
            "traceability_sha256",
            "qualification_protocol_path",
            "qualification_protocol_sha256",
            "candidate_commit",
            "candidate_tree",
            "candidate_wheel_sha256",
            "candidate_sdist_sha256",
            "candidate_version",
        },
    )
    _exact(bindings["candidate_commit"], release_commit, "bindings.candidate_commit")
    _exact(bindings["candidate_tree"], release_tree, "bindings.candidate_tree")
    _exact(bindings["candidate_version"], release_version, "bindings.candidate_version")
    for path in (
        "bindings.prd_sha256",
        "bindings.traceability_sha256",
        "bindings.qualification_protocol_sha256",
        "bindings.candidate_wheel_sha256",
        "bindings.candidate_sdist_sha256",
    ):
        _sha256(_required(manifest, path), path)

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        _fail("artifacts must be a non-empty release asset list")
    artifact_index: dict[str, Mapping[str, Any]] = {}
    wheel_assets: list[Mapping[str, Any]] = []
    sdist_assets: list[Mapping[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        item = closed(
            artifact,
            f"artifacts[{index}]",
            {"path", "sha256", "byte_size"},
        )
        path = _safe_relative_path(item["path"], f"artifacts[{index}].path")
        if path in artifact_index:
            _fail(f"artifacts contains duplicate path: {path}")
        artifact_index[path] = item
        _sha256(item["sha256"], f"artifacts[{index}].sha256")
        if not isinstance(item["byte_size"], int) or isinstance(item["byte_size"], bool):
            _fail(f"artifacts[{index}].byte_size must be a positive integer")
        if item["byte_size"] < 1:
            _fail(f"artifacts[{index}].byte_size must be positive")
        if path.endswith(".whl"):
            if not Path(path).name.startswith(f"deeplaw-{release_version}-"):
                _fail(f"artifacts[{index}].path is not bound to the release version")
            wheel_assets.append(item)
        if path.endswith(".tar.gz"):
            if Path(path).name != f"deeplaw-{release_version}.tar.gz":
                _fail(f"artifacts[{index}].path is not bound to the release version")
            sdist_assets.append(item)
    if len(wheel_assets) != 1 or len(sdist_assets) != 1:
        _fail("artifacts must bind exactly one wheel and one sdist")
    _exact(wheel_assets[0]["sha256"], bindings["candidate_wheel_sha256"], "artifacts.wheel.sha256")
    _exact(sdist_assets[0]["sha256"], bindings["candidate_sdist_sha256"], "artifacts.sdist.sha256")

    seen_evidence: set[str] = set()
    for path, hash_path in (
        ("bindings.prd_path", "bindings.prd_sha256"),
        ("bindings.traceability_path", "bindings.traceability_sha256"),
        ("bindings.qualification_protocol_path", "bindings.qualification_protocol_sha256"),
    ):
        _evidence_ref(
            manifest,
            artifact_index,
            path=path,
            hash_path=hash_path,
            seen=seen_evidence,
        )

    qualification = closed(
        manifest["qualification"],
        "qualification",
        {
            "human_gold_manifest_path",
            "human_gold_manifest_sha256",
            "human_gold_origin",
            "human_gold_model_output",
            "compiler_evaluator_isolation_receipt_path",
            "compiler_evaluator_isolation_receipt_sha256",
            "compiler_evaluator_isolated",
        },
    )
    _evidence_ref(
        manifest,
        artifact_index,
        path="qualification.human_gold_manifest_path",
        hash_path="qualification.human_gold_manifest_sha256",
        seen=seen_evidence,
    )
    _evidence_ref(
        manifest,
        artifact_index,
        path="qualification.compiler_evaluator_isolation_receipt_path",
        hash_path="qualification.compiler_evaluator_isolation_receipt_sha256",
        seen=seen_evidence,
    )
    _exact(
        qualification["human_gold_origin"],
        "repository_external",
        "qualification.human_gold_origin",
    )
    _exact(
        qualification["human_gold_model_output"],
        False,
        "qualification.human_gold_model_output",
    )
    _exact(
        qualification["compiler_evaluator_isolated"],
        True,
        "qualification.compiler_evaluator_isolated",
    )

    host_acceptance = closed(
        manifest["host_acceptance"],
        "host_acceptance",
        {
            "report_path",
            "report_sha256",
            "model_task_acceptance",
            "model_task_results_claimed",
            "model_or_api_call_attempted",
            "declared_supported_hosts",
        },
    )
    _evidence_ref(
        manifest,
        artifact_index,
        path="host_acceptance.report_path",
        hash_path="host_acceptance.report_sha256",
        seen=seen_evidence,
    )
    _true(host_acceptance["model_task_acceptance"], "host_acceptance.model_task_acceptance")
    _true(
        host_acceptance["model_task_results_claimed"],
        "host_acceptance.model_task_results_claimed",
    )
    _true(
        host_acceptance["model_or_api_call_attempted"],
        "host_acceptance.model_or_api_call_attempted",
    )
    _exact(
        host_acceptance["declared_supported_hosts"],
        ["claude_code", "codex", "opencode"],
        "host_acceptance.declared_supported_hosts",
    )

    for host_name in ("real_codex", "real_claude", "real_opencode"):
        host = closed(
            manifest[host_name],
            host_name,
            {
                "independent_runs",
                "model_task_acceptance",
                "model_or_api_call_attempted",
                "evidence_path",
                "evidence_sha256",
            },
        )
        runs = _nonnegative_integer(host["independent_runs"], f"{host_name}.independent_runs")
        if runs < 3:
            _fail(f"{host_name}.independent_runs must be at least three")
        _true(host["model_task_acceptance"], f"{host_name}.model_task_acceptance")
        _true(host["model_or_api_call_attempted"], f"{host_name}.model_or_api_call_attempted")
        _evidence_ref(
            manifest,
            artifact_index,
            path=f"{host_name}.evidence_path",
            hash_path=f"{host_name}.evidence_sha256",
            seen=seen_evidence,
        )

    comparison = closed(
        manifest["comparison"],
        "comparison",
        {
            "host_only",
            "host_native_memory",
            "host_native_memory_plus_deeplaw",
            "equal_budget",
            "equal_budget_report_path",
            "equal_budget_report_sha256",
        },
    )
    lanes: dict[str, Mapping[str, Any]] = {}
    for lane in ("host_only", "host_native_memory", "host_native_memory_plus_deeplaw"):
        lanes[lane] = closed(
            comparison[lane],
            f"comparison.{lane}",
            {
                "passed",
                "first_correct_action",
                "report_path",
                "report_sha256",
                "incremental_benefit",
            },
        )
        _true(lanes[lane]["passed"], f"comparison.{lane}.passed")
        if isinstance(lanes[lane]["first_correct_action"], bool) or not isinstance(
            lanes[lane]["first_correct_action"], (int, float)
        ):
            _fail(f"comparison.{lane}.first_correct_action must be numeric")
        if not math.isfinite(float(lanes[lane]["first_correct_action"])):
            _fail(f"comparison.{lane}.first_correct_action must be finite")
        if lanes[lane]["first_correct_action"] < 0:
            _fail(f"comparison.{lane}.first_correct_action must be non-negative")
        _evidence_ref(
            manifest,
            artifact_index,
            path=f"comparison.{lane}.report_path",
            hash_path=f"comparison.{lane}.report_sha256",
            seen=seen_evidence,
        )
    _true(comparison["equal_budget"], "comparison.equal_budget")
    _evidence_ref(
        manifest,
        artifact_index,
        path="comparison.equal_budget_report_path",
        hash_path="comparison.equal_budget_report_sha256",
        seen=seen_evidence,
    )
    candidate_action = lanes["host_native_memory_plus_deeplaw"]["first_correct_action"]
    for lane in ("host_only", "host_native_memory"):
        if candidate_action < lanes[lane]["first_correct_action"]:
            _fail("DeepLaw comparison lowers First Correct Action")
    _true(
        lanes["host_native_memory_plus_deeplaw"]["incremental_benefit"],
        "comparison.host_native_memory_plus_deeplaw.incremental_benefit",
    )

    legal = closed(
        manifest["legal"],
        "legal",
        {
            "report_path",
            "report_sha256",
            "pack_manifest_path",
            "pack_manifest_sha256",
            "catalog_path",
            "catalog_sha256",
            "release_path",
            "release_sha256",
            "source_count",
            "signed_and_verified",
            "critical_failures",
            "false_authority",
            "wrong_version_primary",
            "invalid_quote",
            "invalid_locator",
            "cross_boundary_disclosure",
            "secret_leak",
        },
    )
    for path, hash_path in (
        ("legal.report_path", "legal.report_sha256"),
        ("legal.pack_manifest_path", "legal.pack_manifest_sha256"),
        ("legal.catalog_path", "legal.catalog_sha256"),
        ("legal.release_path", "legal.release_sha256"),
    ):
        _evidence_ref(manifest, artifact_index, path=path, hash_path=hash_path, seen=seen_evidence)
    _exact(legal["source_count"], 28, "legal.source_count")
    _true(legal["signed_and_verified"], "legal.signed_and_verified")
    _exact(legal["critical_failures"], 0, "legal.critical_failures")
    for path in (
        "legal.false_authority",
        "legal.wrong_version_primary",
        "legal.invalid_quote",
        "legal.invalid_locator",
        "legal.cross_boundary_disclosure",
        "legal.secret_leak",
    ):
        _exact(_required(manifest, path), 0, path)

    scale = closed(
        manifest["scale"],
        "scale",
        {
            "current_candidate",
            "current_candidate_report_path",
            "current_candidate_report_sha256",
            "statement_5k",
            "statement_5k_report_path",
            "statement_5k_report_sha256",
            "statement_10k",
            "statement_10k_report_path",
            "statement_10k_report_sha256",
            "statement_100k",
            "statement_100k_report_path",
            "statement_100k_report_sha256",
            "relation_10k",
            "relation_10k_report_path",
            "relation_10k_report_sha256",
            "relation_100k",
            "relation_100k_report_path",
            "relation_100k_report_sha256",
            "wiki_10k",
            "wiki_10k_report_path",
            "wiki_10k_report_sha256",
            "wiki_100k",
            "wiki_100k_report_path",
            "wiki_100k_report_sha256",
            "requests_10000_rss",
            "requests_10000_rss_report_path",
            "requests_10000_rss_report_sha256",
            "readers_8",
            "readers_8_report_path",
            "readers_8_report_sha256",
            "cache_invalidation",
            "cache_invalidation_report_path",
            "cache_invalidation_report_sha256",
        },
    )
    for name in (
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
    ):
        _true(scale[name], f"scale.{name}")
        _evidence_ref(
            manifest,
            artifact_index,
            path=f"scale.{name}_report_path",
            hash_path=f"scale.{name}_report_sha256",
            seen=seen_evidence,
        )

    operations = closed(
        manifest["operations"],
        "operations",
        {
            "timeline",
            "timeline_report_path",
            "timeline_report_sha256",
            "semantic_restore",
            "semantic_restore_report_path",
            "semantic_restore_report_sha256",
            "selective_forget",
            "selective_forget_report_path",
            "selective_forget_report_sha256",
        },
    )
    for name in ("timeline", "semantic_restore", "selective_forget"):
        _true(operations[name], f"operations.{name}")
        _evidence_ref(
            manifest,
            artifact_index,
            path=f"operations.{name}_report_path",
            hash_path=f"operations.{name}_report_sha256",
            seen=seen_evidence,
        )

    platform = closed(
        manifest["platform_gates"],
        "platform_gates",
        {
            "systems",
            "python_versions",
            "matrix",
            "mandatory_skips",
            "matrix_complete",
            "report_path",
            "report_sha256",
        },
    )
    _exact(platform["systems"], ["Darwin", "Linux", "Windows"], "platform_gates.systems")
    _exact(platform["python_versions"], ["3.11", "3.12", "3.13"], "platform_gates.python_versions")
    _evidence_ref(
        manifest,
        artifact_index,
        path="platform_gates.report_path",
        hash_path="platform_gates.report_sha256",
        seen=seen_evidence,
    )
    matrix = platform["matrix"]
    expected_matrix = [
        {"system": system, "python_version": python_version}
        for system in ("Darwin", "Linux", "Windows")
        for python_version in ("3.11", "3.12", "3.13")
    ]
    if not isinstance(matrix, list) or len(matrix) != 9:
        _fail("platform_gates.matrix must contain exactly nine rows")
    observed_matrix: list[dict[str, Any]] = []
    for index, row in enumerate(matrix):
        item = closed(
            row,
            f"platform_gates.matrix[{index}]",
            {"system", "python_version", "report_path", "report_sha256"},
        )
        observed_matrix.append({"system": item["system"], "python_version": item["python_version"]})
        _evidence_ref_values(
            artifact_index,
            path_value=item["report_path"],
            hash_value=item["report_sha256"],
            path=f"platform_gates.matrix[{index}].report_path",
            hash_path=f"platform_gates.matrix[{index}].report_sha256",
            seen=seen_evidence,
        )
    if observed_matrix != expected_matrix:
        _fail("platform_gates.matrix must cover exactly three systems and three Python versions")
    _exact(platform["mandatory_skips"], 0, "platform_gates.mandatory_skips")
    _true(platform["matrix_complete"], "platform_gates.matrix_complete")

    supply_chain = closed(
        manifest["supply_chain"],
        "supply_chain",
        {
            "reproducible_wheel_sdist",
            "reproducible_wheel_sdist_path",
            "reproducible_wheel_sdist_sha256",
            "sbom",
            "sbom_path",
            "sbom_sha256",
            "licenses",
            "licenses_path",
            "licenses_sha256",
            "openvex",
            "openvex_path",
            "openvex_sha256",
            "provenance",
            "provenance_path",
            "provenance_sha256",
            "public_redownload",
            "public_redownload_path",
            "public_redownload_sha256",
        },
    )
    for name in (
        "reproducible_wheel_sdist",
        "sbom",
        "licenses",
        "openvex",
        "provenance",
        "public_redownload",
    ):
        _true(supply_chain[name], f"supply_chain.{name}")
        _evidence_ref(
            manifest,
            artifact_index,
            path=f"supply_chain.{name}_path",
            hash_path=f"supply_chain.{name}_sha256",
            seen=seen_evidence,
        )

    _exact(manifest["commercial_release_eligible"], True, "commercial_release_eligible")
    _exact(manifest["quality_protocol_eligible"], True, "quality_protocol_eligible")
    _exact(manifest["competitive_claim_eligible"], False, "competitive_claim_eligible")
    _sha256(manifest["record_sha256"], "record_sha256")
    _verify_record_digest(manifest)


def validate_manifest_for_release(
    manifest: Mapping[str, Any], *, release_version: str
) -> None:
    """Validate a manifest against the policy selected by ``release_version``."""

    _semver(release_version)
    if not isinstance(manifest, Mapping):
        _fail("release manifest must be an object")
    expected = required_manifest_schema_version(release_version)
    observed = manifest.get("schema_version")
    if observed != expected:
        _fail(
            f"release {release_version} requires {expected}; observed manifest schema {observed!r}"
        )
    if expected == V5_MANIFEST_SCHEMA:
        _v5_manifest(manifest, release_version)
    else:
        _v6_manifest(manifest, release_version)


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a version-conditional DeepLaw release manifest"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-version", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        validate_manifest_for_release(payload, release_version=args.release_version)
    except (OSError, json.JSONDecodeError, ReleasePolicyError) as error:
        print(str(error))
        return 1
    print(f"validated commercial release manifest for {args.release_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

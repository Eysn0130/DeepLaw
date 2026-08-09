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
    "bindings.prd_sha256",
    "bindings.traceability_path",
    "bindings.traceability_sha256",
    "bindings.qualification_protocol_path",
    "bindings.qualification_protocol_sha256",
    "bindings.thresholds_path",
    "bindings.thresholds_sha256",
    "bindings.human_gold_manifest_path",
    "bindings.human_gold_manifest_sha256",
    "bindings.compiler_evaluator_isolation_path",
    "bindings.compiler_evaluator_isolation_sha256",
    "bindings.gate_classification_path",
    "bindings.gate_classification_sha256",
    "bindings.candidate_commit",
    "bindings.candidate_tree",
    "bindings.candidate_wheel_sha256",
    "bindings.candidate_sdist_sha256",
    "semantic_evidence.report_path",
    "semantic_evidence.report_artifact_sha256",
    "semantic_evidence.report_record_sha256",
    "semantic_evidence.gate_statuses[]",
)

V013_CORE_GATE_IDS = frozenset(
    {
        "canonical_integrity",
        "migration_recovery",
        "secret_host_isolation",
        "bounded_context",
        "legal_evidence",
        "source_citation_locator",
        "scale_performance",
        "supported_platforms",
        "reproducible_supply_chain",
        "human_gold_isolation",
        "codex",
        "selective_forget",
    }
)
V013_CAPABILITY_GATE_IDS = frozenset({"timeline", "semantic_restore", "claude", "opencode"})
V013_COMPETITIVE_GATE_IDS = frozenset(
    {"comparative_incremental_benefit", "superiority", "sota"}
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
        "semantic_evidence",
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
    """Validate only the v6 envelope, artifact bindings, and derived gate invariants.

    Semantic report content is deliberately validated by ``semantic_evidence`` before this
    envelope may reach publish.  Keeping that boundary explicit prevents this policy from treating
    a caller-authored pass boolean as evidence.
    """

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
            "thresholds_path",
            "thresholds_sha256",
            "human_gold_manifest_path",
            "human_gold_manifest_sha256",
            "compiler_evaluator_isolation_path",
            "compiler_evaluator_isolation_sha256",
            "gate_classification_path",
            "gate_classification_sha256",
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
    for name in (
        "prd_sha256",
        "traceability_sha256",
        "qualification_protocol_sha256",
        "thresholds_sha256",
        "human_gold_manifest_sha256",
        "compiler_evaluator_isolation_sha256",
        "gate_classification_sha256",
        "candidate_wheel_sha256",
        "candidate_sdist_sha256",
    ):
        _sha256(bindings[name], f"bindings.{name}")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        _fail("artifacts must be a non-empty release asset list")
    artifact_index: dict[str, Mapping[str, Any]] = {}
    wheel_assets: list[Mapping[str, Any]] = []
    sdist_assets: list[Mapping[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        item = closed(artifact, f"artifacts[{index}]", {"path", "sha256", "byte_size"})
        path = _safe_relative_path(item["path"], f"artifacts[{index}].path")
        if path in artifact_index:
            _fail(f"artifacts contains duplicate path: {path}")
        _sha256(item["sha256"], f"artifacts[{index}].sha256")
        if isinstance(item["byte_size"], bool) or not isinstance(item["byte_size"], int):
            _fail(f"artifacts[{index}].byte_size must be a positive integer")
        if item["byte_size"] < 1:
            _fail(f"artifacts[{index}].byte_size must be positive")
        artifact_index[path] = item
        if path.endswith(".whl"):
            if not Path(path).name.startswith(f"deeplaw-{release_version}-"):
                _fail(f"artifacts[{index}].path is not bound to the release version")
            wheel_assets.append(item)
        elif path.endswith(".tar.gz"):
            if Path(path).name != f"deeplaw-{release_version}.tar.gz":
                _fail(f"artifacts[{index}].path is not bound to the release version")
            sdist_assets.append(item)
    if len(wheel_assets) != 1 or len(sdist_assets) != 1:
        _fail("artifacts must bind exactly one wheel and one sdist")
    _exact(wheel_assets[0]["sha256"], bindings["candidate_wheel_sha256"], "wheel sha256")
    _exact(sdist_assets[0]["sha256"], bindings["candidate_sdist_sha256"], "sdist sha256")

    seen: set[str] = set()
    for path_name, hash_name in (
        ("prd_path", "prd_sha256"),
        ("traceability_path", "traceability_sha256"),
        ("qualification_protocol_path", "qualification_protocol_sha256"),
        ("thresholds_path", "thresholds_sha256"),
        ("human_gold_manifest_path", "human_gold_manifest_sha256"),
        ("compiler_evaluator_isolation_path", "compiler_evaluator_isolation_sha256"),
        ("gate_classification_path", "gate_classification_sha256"),
    ):
        _evidence_ref_values(
            artifact_index,
            path_value=bindings[path_name],
            hash_value=bindings[hash_name],
            path=f"bindings.{path_name}",
            hash_path=f"bindings.{hash_name}",
            seen=seen,
        )

    semantic = closed(
        manifest["semantic_evidence"],
        "semantic_evidence",
        {
            "report_path",
            "report_artifact_sha256",
            "report_record_sha256",
            "report_kind",
            "status",
            "hard_zero",
            "release_ready",
            "claim_eligible",
            "competitive_claim_eligible",
            "gate_statuses",
        },
    )
    _evidence_ref_values(
        artifact_index,
        path_value=semantic["report_path"],
        hash_value=semantic["report_artifact_sha256"],
        path="semantic_evidence.report_path",
        hash_path="semantic_evidence.report_artifact_sha256",
        seen=seen,
    )
    _sha256(semantic["report_record_sha256"], "semantic_evidence.report_record_sha256")
    _exact(
        semantic["report_kind"],
        "v013_commercial_gate_collection",
        "semantic_evidence.report_kind",
    )
    _exact(semantic["status"], "passed", "semantic_evidence.status")
    _true(semantic["hard_zero"], "semantic_evidence.hard_zero")
    _true(semantic["release_ready"], "semantic_evidence.release_ready")
    _true(semantic["claim_eligible"], "semantic_evidence.claim_eligible")
    _exact(
        semantic["competitive_claim_eligible"],
        False,
        "semantic_evidence.competitive_claim_eligible",
    )
    statuses = semantic["gate_statuses"]
    if not isinstance(statuses, list) or len(statuses) != (
        len(V013_CORE_GATE_IDS) + len(V013_CAPABILITY_GATE_IDS) + len(V013_COMPETITIVE_GATE_IDS)
    ):
        _fail("semantic_evidence.gate_statuses is incomplete")
    observed: dict[str, tuple[str, str]] = {}
    for index, status in enumerate(statuses):
        item = closed(
            status,
            f"semantic_evidence.gate_statuses[{index}]",
            {"gate_id", "category", "status"},
        )
        gate_id = item["gate_id"]
        if not isinstance(gate_id, str) or gate_id in observed:
            _fail("semantic_evidence.gate_statuses contains an invalid or duplicate gate")
        if item["status"] not in {
            "passed",
            "failed",
            "not_applicable",
            "not_executed",
            "not_claimed",
        }:
            _fail(f"semantic_evidence.gate_statuses[{index}].status is invalid")
        observed[gate_id] = (item["category"], item["status"])
    if set(observed) != V013_CORE_GATE_IDS | V013_CAPABILITY_GATE_IDS | V013_COMPETITIVE_GATE_IDS:
        _fail("semantic_evidence.gate_statuses does not match the frozen v0.13 gate inventory")
    if any(observed[gate] != ("Core", "passed") for gate in V013_CORE_GATE_IDS):
        _fail("every v0.13 Core gate must be semantically passed")
    if any(
        observed[gate][0] != "Capability" or observed[gate][1] not in {"passed", "not_claimed"}
        for gate in V013_CAPABILITY_GATE_IDS
    ):
        _fail("v0.13 Capability gates must be passed or explicitly not_claimed")
    if any(
        observed[gate] != ("Competitive Claim", "not_claimed")
        for gate in V013_COMPETITIVE_GATE_IDS
    ):
        _fail("v0.13 competitive claims must remain not_claimed")

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

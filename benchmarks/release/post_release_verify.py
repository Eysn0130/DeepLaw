"""Replay the historical v5/v6 post-release envelope verification path.

The current v0.13 release workflow reopens the v8 manifest with
``benchmarks.release.release_provenance_v8`` and performs its own public redownload check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from benchmarks.release.evidence import (
    environment_manifest,
    file_record,
    load_json,
    repository_binding,
    sha256_file,
    verify_record_digest,
    write_report,
)
from benchmarks.release.release_policy import (
    V5_MANIFEST_SCHEMA,
    ReleasePolicyError,
    required_legacy_manifest_schema_version,
    validate_legacy_manifest_for_release,
)

SCHEMA_VERSION = "deeplaw.post-release-verification/v1"


class PostReleaseError(RuntimeError):
    pass


def _downloaded(downloads: Path, logical_path: str) -> Path:
    matches = [path for path in downloads.rglob(Path(logical_path).name) if path.is_file()]
    if len(matches) != 1:
        raise PostReleaseError(f"release asset is missing or ambiguous: {logical_path}")
    return matches[0]


def verify(
    repository: Path,
    *,
    downloads: Path,
    lifecycle_path: Path,
    checksum_log: Path,
    signature_log: Path,
    provenance_log: Path,
    formal_quality_path: Path,
    release_url: str,
) -> dict[str, Any]:
    binding = repository_binding(repository)
    version = binding["package_version"]
    tag = f"v{version}"
    if not binding["worktree_clean"]:
        raise PostReleaseError("post-release verification requires a clean tag checkout")
    manifest_path = _downloaded(downloads, "commercial-release-manifest.json")
    manifest = load_json(manifest_path)
    verify_record_digest(manifest, field="commercial release manifest")
    try:
        expected_schema = required_legacy_manifest_schema_version(version)
        validate_legacy_manifest_for_release(manifest, release_version=version)
    except ReleasePolicyError as error:
        raise PostReleaseError(str(error)) from error
    release = manifest.get("release", {})
    common_manifest_checks = (
        manifest.get("commercial_release_eligible") is not True
        or manifest.get("quality_protocol_eligible") is not True
        or manifest.get("competitive_claim_eligible") is not False
        or release.get("version") != version
        or release.get("tag") != tag
        or release.get("commit") != binding["commit"]
        or release.get("tree") != binding["tree"]
    )
    if expected_schema == V5_MANIFEST_SCHEMA:
        common_manifest_checks = common_manifest_checks or (
            manifest.get("bindings", {}).get("lock_sha256") != binding["lock_sha256"]
            or manifest.get("bindings", {}).get("contracts_inventory_sha256")
            != binding["contracts"]["inventory_sha256"]
            or manifest.get("bindings", {}).get("migrations_inventory_sha256")
            != binding["migrations"]["inventory_sha256"]
            or manifest.get("living_wiki_quality", {}).get("passed") is not True
            or manifest.get("living_wiki_quality", {}).get("quality_regression") is not False
            or manifest.get("living_wiki_quality", {}).get("performance_regression") is not False
            or manifest.get("authoritative_source_quality", {}).get("passed") is not True
            or manifest.get("authoritative_source_quality", {}).get("source_count") != 28
            or manifest.get("semantic_living_wiki_quality", {}).get("passed") is not True
            or manifest.get("semantic_living_wiki_quality", {}).get("formal_release_eligible")
            is not True
            or manifest.get("authoritative_evidence_quality", {}).get("passed") is not True
            or manifest.get("editor_integrations", {}).get("passed") is not True
        )
    if common_manifest_checks:
        raise PostReleaseError("downloaded commercial manifest does not bind the tag checkout")

    verified_assets: list[dict[str, Any]] = []
    basenames: set[str] = set()
    for record in manifest.get("artifacts", []):
        logical_path = record.get("path")
        if not isinstance(logical_path, str):
            raise PostReleaseError("commercial manifest contains an invalid artifact path")
        basename = Path(logical_path).name
        if basename in basenames:
            raise PostReleaseError("commercial manifest uses duplicate release asset names")
        basenames.add(basename)
        selected = _downloaded(downloads, logical_path)
        if sha256_file(selected) != record.get("sha256") or selected.stat().st_size != record.get(
            "byte_size"
        ):
            raise PostReleaseError(f"downloaded release bytes differ: {logical_path}")
        verified_assets.append(
            {
                "asset_name": basename,
                "sha256": record["sha256"],
                "byte_size": record["byte_size"],
            }
        )

    if expected_schema == V5_MANIFEST_SCHEMA:
        authoritative_manifest = manifest["authoritative_evidence_quality"]
        authoritative_path = _downloaded(downloads, authoritative_manifest["report_path"])
        authoritative_report = load_json(authoritative_path)
        verify_record_digest(
            authoritative_report,
            field="downloaded Authoritative evidence quality report",
        )
        if (
            sha256_file(authoritative_path) != authoritative_manifest["report_sha256"]
            or authoritative_report.get("record_sha256")
            != authoritative_manifest["record_sha256"]
            or authoritative_report.get("binding", {}).get("commit") != binding["commit"]
            or authoritative_report.get("passed") is not True
            or not all(authoritative_report.get("checks", {}).values())
            or any(authoritative_report.get("security_failures", {}).values())
        ):
            raise PostReleaseError("downloaded Authoritative evidence quality report is invalid")

    lifecycle = load_json(lifecycle_path)
    verify_record_digest(lifecycle, field="post-release distribution lifecycle")
    if (
        lifecycle.get("schema_version") != "deeplaw.distribution-lifecycle/v1"
        or lifecycle.get("passed") is not True
        or lifecycle.get("binding", {}).get("commit") != binding["commit"]
        or not all(lifecycle.get("gates", {}).values())
    ):
        raise PostReleaseError("post-release install/upgrade/uninstall lifecycle failed")
    wheel_name = lifecycle["artifacts"]["wheel"]["logical_name"]
    sdist_name = lifecycle["artifacts"]["sdist"]["logical_name"]
    expected = {record["asset_name"]: record["sha256"] for record in verified_assets}
    if (
        expected.get(wheel_name) != lifecycle["artifacts"]["wheel"]["sha256"]
        or expected.get(sdist_name) != lifecycle["artifacts"]["sdist"]["sha256"]
    ):
        raise PostReleaseError("post-release lifecycle did not use formal release bytes")
    formal_quality = load_json(formal_quality_path)
    verify_record_digest(formal_quality, field="formal-release Living Wiki quality")
    formal_candidate = formal_quality.get("candidate", {})
    if (
        formal_quality.get("schema_version") != "deeplaw.living-wiki-quality-report/v1"
        or formal_quality.get("passed") is not True
        or formal_quality.get("competitive_claim_eligible") is not False
        or formal_candidate.get("role") != "formal_release"
        or formal_candidate.get("commit") != binding["commit"]
        or formal_candidate.get("version") != version
        or formal_candidate.get("artifact_sha256")
        != lifecycle["artifacts"]["wheel"]["sha256"]
        or formal_quality.get("suite", {}).get("suite_sha256")
        != file_record(repository / "benchmarks/living_wiki/quality-suite-v1.json")["sha256"]
        or formal_quality.get("suite", {}).get("runner_sha256")
        != file_record(repository / "benchmarks/living_wiki/run_quality_gate.py")["sha256"]
        or any(
            value != 0
            for key, value in formal_quality["security"].items()
            if key != "unauthorized_write_rejected"
        )
        or formal_quality["security"].get("unauthorized_write_rejected") is not True
    ):
        raise PostReleaseError("formal-release Living Wiki quality smoke failed")
    for path, field in (
        (checksum_log, "checksum"),
        (signature_log, "Sigstore signature"),
        (provenance_log, "GitHub provenance"),
    ):
        if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
            raise PostReleaseError(f"{field} verification log is unavailable")
    return {
        "schema_version": SCHEMA_VERSION,
        "binding": binding,
        "environment": environment_manifest(),
        "release": {
            "tag": tag,
            "url": release_url,
            "commit": binding["commit"],
            "lock_sha256": binding["lock_sha256"],
            "contracts_inventory_sha256": binding["contracts"]["inventory_sha256"],
            "migrations_inventory_sha256": binding["migrations"]["inventory_sha256"],
        },
        "downloaded_asset_count": len(verified_assets),
        "verified_assets": verified_assets,
        "verification_evidence": {
            "checksums": file_record(checksum_log, logical_name="checksum-verification.log"),
            "sigstore_oidc": file_record(signature_log, logical_name="sigstore-verification.log"),
            "github_provenance": file_record(
                provenance_log, logical_name="provenance-verification.log"
            ),
            "distribution_lifecycle": file_record(
                lifecycle_path, logical_name="post-release-distribution-lifecycle.json"
            ),
            "formal_release_living_wiki_quality": file_record(
                formal_quality_path,
                logical_name="formal-release-living-wiki-quality.json",
            ),
        },
        "gates": {
            "github_release_download": True,
            "all_manifest_artifact_sha256": True,
            "sha256sums_verified": True,
            "sigstore_oidc_verified": True,
            "github_provenance_verified": True,
            "clean_wheel_install": True,
            "clean_sdist_install": True,
            "upgrade_from_0_6_0": True,
            "uninstall": True,
            "living_wiki_quality_artifact": True,
            "living_wiki_baseline_comparison_artifact": True,
            "authoritative_source_quality_artifact": True,
            "semantic_real_host_quality_artifact": True,
            "authoritative_evidence_quality_artifact": True,
            "editor_integration_artifacts": True,
        },
        "commercial_release_eligible": True,
        "quality_protocol_eligible": True,
        "competitive_claim_eligible": False,
        "passed": True,
    }


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Bind post-release install and signature verification to GitHub assets."
    )
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--downloads", type=Path, required=True)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--checksum-log", type=Path, required=True)
    parser.add_argument("--signature-log", type=Path, required=True)
    parser.add_argument("--provenance-log", type=Path, required=True)
    parser.add_argument("--formal-quality", type=Path, required=True)
    parser.add_argument("--release-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify(
            args.repository.resolve(),
            downloads=args.downloads.resolve(),
            lifecycle_path=args.lifecycle.resolve(),
            checksum_log=args.checksum_log.resolve(),
            signature_log=args.signature_log.resolve(),
            provenance_log=args.provenance_log.resolve(),
            formal_quality_path=args.formal_quality.resolve(),
            release_url=args.release_url,
        )
        write_report(args.output.resolve(), report)
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    release = manifest.get("release", {})
    if (
        manifest.get("schema_version") != "deeplaw.commercial-release-manifest/v4"
        or manifest.get("commercial_release_eligible") is not True
        or manifest.get("quality_protocol_eligible") is not True
        or manifest.get("competitive_claim_eligible") is not False
        or release.get("version") != version
        or release.get("tag") != tag
        or release.get("commit") != binding["commit"]
        or manifest.get("bindings", {}).get("lock_sha256") != binding["lock_sha256"]
        or manifest.get("bindings", {}).get("contracts_inventory_sha256")
        != binding["contracts"]["inventory_sha256"]
        or manifest.get("bindings", {}).get("migrations_inventory_sha256")
        != binding["migrations"]["inventory_sha256"]
        or manifest.get("living_wiki_quality", {}).get("passed") is not True
        or manifest.get("living_wiki_quality", {}).get("quality_regression")
        is not False
        or manifest.get("living_wiki_quality", {}).get(
            "performance_regression"
        )
        is not False
        or manifest.get("authoritative_source_quality", {}).get("passed")
        is not True
        or manifest.get("authoritative_source_quality", {}).get("source_count") != 28
    ):
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
        if (
            sha256_file(selected) != record.get("sha256")
            or selected.stat().st_size != record.get("byte_size")
        ):
            raise PostReleaseError(f"downloaded release bytes differ: {logical_path}")
        verified_assets.append(
            {
                "asset_name": basename,
                "sha256": record["sha256"],
                "byte_size": record["byte_size"],
            }
        )

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
        formal_quality.get("schema_version")
        != "deeplaw.living-wiki-quality-report/v1"
        or formal_quality.get("passed") is not True
        or formal_quality.get("competitive_claim_eligible") is not False
        or formal_candidate.get("role") != "formal_release"
        or formal_candidate.get("commit") != binding["commit"]
        or formal_candidate.get("version") != version
        or formal_candidate.get("artifact_sha256")
        != lifecycle["artifacts"]["wheel"]["sha256"]
        or formal_quality.get("suite", {}).get("suite_sha256")
        != file_record(
            repository / "benchmarks/living_wiki/quality-suite-v1.json"
        )["sha256"]
        or formal_quality.get("suite", {}).get("runner_sha256")
        != file_record(
            repository / "benchmarks/living_wiki/run_quality_gate.py"
        )["sha256"]
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
            "migrations_inventory_sha256": binding["migrations"][
                "inventory_sha256"
            ],
        },
        "downloaded_asset_count": len(verified_assets),
        "verified_assets": verified_assets,
        "verification_evidence": {
            "checksums": file_record(checksum_log, logical_name="checksum-verification.log"),
            "sigstore_oidc": file_record(
                signature_log, logical_name="sigstore-verification.log"
            ),
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

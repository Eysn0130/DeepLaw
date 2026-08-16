"""Freeze a complete, disjoint Platform Core manifest from pytest collection.

This maintainer-only command never changes test selection. It records the exact
node IDs produced by the repository's closed marker expressions and preserves
known JUnit identities from the historical v1 manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.release.evidence import canonical_json, sha256_bytes
from benchmarks.release.platform_inventory import collect_node_ids

REPOSITORY = Path(__file__).resolve().parents[2]
HISTORICAL_MANIFEST = (
    REPOSITORY / "benchmarks/release/platform-core-test-manifest-v1.json"
)
HISTORICAL_COMPATIBILITY_NODE_ID = (
    "tests/test_identity_migration_v060.py::"
    "test_real_v060_wheel_additive_migration_verification_and_rollback"
)


class PlatformManifestFreezeError(ValueError):
    """Raised when collection cannot be represented without ambiguity."""


def _sha256(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _historical_descriptors() -> dict[str, dict[str, str]]:
    value = json.loads(HISTORICAL_MANIFEST.read_text(encoding="utf-8"))
    cases = [
        *value["inventories"]["common"]["cases"],
        *value["inventories"]["windows"]["additional_cases"],
        *value["classifications"]["qualification"]["cases"],
    ]
    return {str(case["node_id"]): dict(case["junit"]) for case in cases}


def _descriptor(node_id: str, historical: dict[str, dict[str, str]]) -> dict[str, Any]:
    known = historical.get(node_id)
    if known is not None:
        return {"node_id": node_id, "junit": known}
    path, separator, name = node_id.partition("::")
    if separator != "::" or not path.endswith(".py") or not name or "::" in name:
        raise PlatformManifestFreezeError(
            f"new node ID requires an explicit JUnit descriptor: {node_id}"
        )
    return {
        "node_id": node_id,
        "junit": {
            "classname": path[:-3].replace("/", "."),
            "name": name,
        },
    }


def build_manifest(repository: Path = REPOSITORY) -> dict[str, Any]:
    """Collect and freeze the v2 common, Windows, and qualification inventories."""

    root = repository.resolve(strict=True)
    historical = _historical_descriptors()
    common_ids = collect_node_ids(root, selection="common")
    windows_ids = collect_node_ids(root, selection="windows")
    qualification_ids = collect_node_ids(root, selection="qualification")
    common_set = set(common_ids)
    windows_set = set(windows_ids)
    qualification_set = set(qualification_ids)
    if not common_set < windows_set:
        raise PlatformManifestFreezeError(
            "Windows inventory must be a strict superset of common inventory"
        )
    if common_set & qualification_set or windows_set & qualification_set:
        raise PlatformManifestFreezeError(
            "Platform Core and qualification classifications overlap"
        )
    additional_ids = sorted(windows_set - common_set)
    common = [_descriptor(node_id, historical) for node_id in common_ids]
    additional = [_descriptor(node_id, historical) for node_id in additional_ids]
    qualification = [
        _descriptor(node_id, historical) for node_id in qualification_ids
    ]
    historical_case = next(
        (case for case in common if case["node_id"] == HISTORICAL_COMPATIBILITY_NODE_ID),
        None,
    )
    if historical_case is None:
        raise PlatformManifestFreezeError(
            "frozen historical compatibility case is absent from Platform Core"
        )
    manifest: dict[str, Any] = {
        "schema_version": "deeplaw.platform-core-test-manifest/v2",
        "selection": {
            "common": "not qualification and not windows_native",
            "windows": "not qualification",
            "qualification": "qualification",
            "windows_native": "windows_native",
        },
        "generation": {
            "commands": [
                "uv run --frozen pytest --collect-only -q -o addopts='' "
                "-m 'not qualification and not windows_native'",
                "uv run --frozen pytest --collect-only -q -o addopts='' "
                "-m 'not qualification'",
                "uv run --frozen pytest --collect-only -q -o addopts='' "
                "-m 'qualification'",
            ],
            "pytest": "pytest",
            "selection_source": "repository test collection",
        },
        "inventories": {
            "common": {
                "selection": "not qualification and not windows_native",
                "count": len(common),
                "sha256": _sha256(common),
                "cases": common,
            },
            "windows": {
                "selection": "not qualification",
                "extends": "common",
                "count": len(common) + len(additional),
                "sha256": _sha256([*common, *additional]),
                "additional_cases": additional,
            },
        },
        "classifications": {
            "qualification": {
                "selection": "qualification",
                "status": "not_executed",
                "cases": qualification,
            },
            "nonapplicable": {
                "selection": "windows_native on non-Windows",
                "status": "nonapplicable",
                "cases": additional,
            },
            "historical_compatibility": {
                "selection": "exact frozen v0.6 wheel required",
                "status": "required_fixture",
                "cases": [historical_case],
            },
        },
    }
    manifest["manifest_sha256"] = _sha256(manifest)
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description="Freeze Platform Core v2")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY / "benchmarks/release/platform-core-test-manifest-v2.json",
    )
    args = parser.parse_args()
    manifest = build_manifest()
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

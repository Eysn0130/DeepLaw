"""Preflight the repository test collection without mutating the frozen manifest.

The release manifest is intentionally immutable.  This module inventories the
current source collection in a child pytest process, compares that inventory
with the selected frozen Platform Core inventory, and writes a digest-bound
receipt.  Candidate CI records drift as evidence; a manual Platform Core run
may opt into fail-closed matching with ``--require-match``.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.release.evidence import (
    canonical_json,
    load_json,
    sha256_bytes,
    write_report,
)
from benchmarks.release.platform_gate import load_platform_manifest
from deeplaw.subprocess_environment import _build_subprocess_environment

SCHEMA_VERSION = "deeplaw.platform-core-inventory-preflight/v1"
INVENTORY_SCHEMA_VERSION = SCHEMA_VERSION
PLATFORM_INVENTORY_SCHEMA_VERSION = SCHEMA_VERSION
MANIFEST_FILENAME = "platform-core-test-manifest-v2.json"
SCHEMA_FILENAME = "platform-core-inventory-preflight.v1.schema.json"
MODE_CANDIDATE = "candidate"
MODE_PLATFORM_CORE = "platform_core"
MODE_ALIASES = {
    "candidate": MODE_CANDIDATE,
    "candidate_current_source": MODE_CANDIDATE,
    "platform-core": MODE_PLATFORM_CORE,
    "manual_platform_core": MODE_PLATFORM_CORE,
    "manual-platform-core": MODE_PLATFORM_CORE,
    "platform_core": MODE_PLATFORM_CORE,
}
CANDIDATE_STATUS = "candidate_current_source_inventory"
PLATFORM_CORE_STATUS = "platform_core_inventory_preflight"
SELECTIONS = {
    "common": "not qualification and not windows_native",
    "windows": "not qualification",
    "qualification": "qualification",
}


class PlatformInventoryError(RuntimeError):
    """Raised when collection or a frozen inventory comparison is invalid."""


def _schema_path(repository: Path) -> Path:
    return repository / "contracts" / SCHEMA_FILENAME


def _manifest_path(repository: Path) -> Path:
    return repository / "benchmarks" / "release" / MANIFEST_FILENAME


def _git(repository: Path, *arguments: str, allow_empty: bool = False) -> str:
    environment = _build_subprocess_environment(
        overrides={"PYTHONPATH": str(repository)}
    )
    process = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode != 0 or (not allow_empty and not process.stdout.strip()):
        detail = process.stderr.strip() or "no output"
        raise PlatformInventoryError(
            f"git {' '.join(arguments)} failed: {detail[:400]}"
        )
    return process.stdout.strip()


def candidate_binding(repository: Path) -> dict[str, Any]:
    """Return the current HEAD/tree and whether those bytes describe the worktree."""

    return {
        "head": _git(repository, "rev-parse", "HEAD"),
        "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
        "worktree_clean": not bool(
            _git(
                repository,
                "status",
                "--porcelain",
                "--untracked-files=all",
                allow_empty=True,
            )
        ),
    }


def inventory_digest(node_ids: list[str]) -> str:
    """Digest a sorted node-id inventory using the repository canonical JSON form."""

    return sha256_bytes(canonical_json(node_ids).encode("utf-8"))


def _validate_selection(selection: str) -> str:
    try:
        return SELECTIONS[selection]
    except KeyError as error:
        supported = ", ".join(sorted(SELECTIONS))
        raise PlatformInventoryError(
            f"unsupported platform inventory selection {selection!r}; expected {supported}"
        ) from error


def _parse_node_ids(stdout: str) -> list[str]:
    """Extract pytest ``--collect-only -q`` node IDs from stdout.

    Quiet collection emits one node ID per line followed by a summary.  Keep
    only path-like testcase lines so warnings and the summary cannot become
    part of the inventory.  A duplicate is retained here and rejected by the
    comparison layer, making collection anomalies visible in the receipt.
    """

    node_ids: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("=") or "::" not in line:
            continue
        if not (".py::" in line or line.startswith("tests/")):
            continue
        node_ids.append(line)
    return sorted(node_ids)


def collect_node_ids(
    repository: Path,
    *,
    selection: str,
    python_executable: str | None = None,
) -> list[str]:
    """Collect current-source node IDs in a closed child environment."""

    marker = _validate_selection(selection)
    executable = python_executable or sys.executable
    environment = _build_subprocess_environment(
        overrides={"PYTHONPATH": str(repository)}
    )
    command = [
        executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-o",
        "addopts=",
        "-m",
        marker,
    ]
    try:
        process = subprocess.run(
            command,
            cwd=repository,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PlatformInventoryError(f"pytest collection could not start: {error}") from error
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise PlatformInventoryError(
            f"pytest collection failed with exit code {process.returncode}: {detail[:600]}"
        )
    node_ids = _parse_node_ids(process.stdout)
    if not node_ids:
        raise PlatformInventoryError("pytest collection produced no node IDs")
    return node_ids


def _manifest_inventory(
    manifest: dict[str, Any], *, selection: str
) -> tuple[list[str], str, str]:
    if selection == "qualification":
        cases = list(manifest["classifications"]["qualification"]["cases"])
        node_ids = sorted(str(case["node_id"]) for case in cases)
        return node_ids, inventory_digest(node_ids), str(manifest["manifest_sha256"])
    inventory_name = "windows" if selection == "windows" else "common"
    inventory = manifest["inventories"][inventory_name]
    if inventory_name == "windows":
        cases = [
            *manifest["inventories"]["common"]["cases"],
            *inventory["additional_cases"],
        ]
    else:
        cases = list(inventory["cases"])
    node_ids = sorted(str(case["node_id"]) for case in cases)
    return node_ids, str(inventory["sha256"]), str(manifest["manifest_sha256"])


def _duplicate_values(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def frozen_comparison(
    observed_node_ids: list[str],
    *,
    manifest: dict[str, Any],
    selection: str,
) -> dict[str, Any]:
    """Compare observed IDs to one immutable manifest inventory."""

    expected_node_ids, manifest_inventory_sha256, manifest_sha256 = _manifest_inventory(
        manifest,
        selection=selection,
    )
    observed_set = set(observed_node_ids)
    expected_set = set(expected_node_ids)
    expected_digest = inventory_digest(expected_node_ids)
    observed_digest = inventory_digest(observed_node_ids)
    missing = sorted(expected_set - observed_set)
    unexpected = sorted(observed_set - expected_set)
    duplicate = _duplicate_values(observed_node_ids)
    matches = not missing and not unexpected and not duplicate and len(
        observed_node_ids
    ) == len(expected_node_ids)
    return {
        "manifest_sha256": manifest_sha256,
        "manifest_inventory_sha256": manifest_inventory_sha256,
        "expected_count": len(expected_node_ids),
        "expected_digest": expected_digest,
        "missing": missing,
        "unexpected": unexpected,
        "duplicate": duplicate,
        "matches": matches,
        "digest_matches": observed_digest == expected_digest,
    }


def verify_platform_inventory(
    *,
    repository: Path,
    manifest_path: Path,
    require_match: bool,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Verify both active Platform Core inventories and classification disjointness."""

    manifest = load_platform_manifest(manifest_path.resolve(strict=True))
    common = collect_node_ids(
        repository.resolve(strict=True),
        selection="common",
        python_executable=python_executable,
    )
    windows = collect_node_ids(
        repository.resolve(strict=True),
        selection="windows",
        python_executable=python_executable,
    )
    qualification = collect_node_ids(
        repository.resolve(strict=True),
        selection="qualification",
        python_executable=python_executable,
    )
    common_result = frozen_comparison(common, manifest=manifest, selection="common")
    windows_result = frozen_comparison(windows, manifest=manifest, selection="windows")
    qualification_result = frozen_comparison(
        qualification,
        manifest=manifest,
        selection="qualification",
    )
    common_set = set(common)
    windows_set = set(windows)
    qualification_set = set(qualification)
    additional = windows_set - common_set
    classified = {
        label: {
            str(case["node_id"])
            for case in manifest["classifications"][label]["cases"]
        }
        for label in ("qualification", "nonapplicable", "historical_compatibility")
    }
    overlap = sorted(
        (classified["qualification"] & classified["nonapplicable"])
        | (classified["qualification"] & classified["historical_compatibility"])
        | (classified["nonapplicable"] & classified["historical_compatibility"])
    )
    missing = sorted(
        set(common_result["missing"])
        | set(windows_result["missing"])
        | set(qualification_result["missing"])
    )
    unexpected = sorted(
        set(common_result["unexpected"])
        | set(windows_result["unexpected"])
        | set(qualification_result["unexpected"])
    )
    overlap.extend(sorted(windows_set & qualification_set))
    if not (
        additional <= classified["nonapplicable"] <= windows_set
    ):
        overlap.append("nonapplicable_windows_inventory_mismatch")
    if classified["qualification"] != qualification_set:
        overlap.append("qualification_inventory_mismatch")
    exact = all(
        result["matches"] and result["digest_matches"]
        for result in (common_result, windows_result, qualification_result)
    )
    passed = exact and not missing and not unexpected and not overlap
    if require_match and not passed:
        raise PlatformInventoryError(
            "current Platform Core v2 collection does not match its frozen manifest"
        )
    return {
        "schema_version": "deeplaw.platform-core-v2-verification/v1",
        "status": "passed" if passed else "failed",
        "missing_node_ids": missing,
        "unexpected_node_ids": unexpected,
        "overlap_node_ids": sorted(set(overlap)),
        "common_count": len(common),
        "windows_count": len(windows),
        "qualification_count": len(qualification),
    }


def build_receipt(
    repository: Path,
    *,
    mode: str = MODE_CANDIDATE,
    selection: str | None = None,
    manifest_path: Path | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Collect, compare, and return one schema-valid preflight receipt.

    The receipt deliberately reports ``release_ready: false`` for both modes;
    this preflight is evidence about collection identity, not a release gate.
    """

    mode = MODE_ALIASES.get(mode, mode)
    if mode not in {MODE_CANDIDATE, MODE_PLATFORM_CORE}:
        raise PlatformInventoryError(f"unsupported platform inventory mode: {mode}")
    if selection is None:
        selection = (
            "windows"
            if mode == MODE_PLATFORM_CORE and platform.system() == "Windows"
            else "common"
    )
    marker = _validate_selection(selection)
    root = repository.resolve(strict=True)
    selected_manifest = manifest_path or _manifest_path(root)
    if not selected_manifest.is_absolute():
        selected_manifest = root / selected_manifest
    selected_manifest = selected_manifest.resolve(strict=True)
    try:
        manifest = load_platform_manifest(selected_manifest)
    except (OSError, RuntimeError) as error:
        raise PlatformInventoryError(f"frozen platform manifest is invalid: {error}") from error
    node_ids = collect_node_ids(
        root,
        selection=selection,
        python_executable=python_executable,
    )
    comparison = frozen_comparison(
        node_ids,
        manifest=manifest,
        selection=selection,
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "status": (
            CANDIDATE_STATUS if mode == MODE_CANDIDATE else PLATFORM_CORE_STATUS
        ),
        "release_ready": False,
        "candidate": candidate_binding(root),
        "selection": marker,
        "node_ids": node_ids,
        "count": len(node_ids),
        "digest": inventory_digest(node_ids),
        "frozen_comparison": comparison,
    }
    schema = load_json(_schema_path(root))
    try:
        Draft202012Validator(schema).validate(
            {**receipt, "record_sha256": sha256_bytes(canonical_json(receipt).encode("utf-8"))}
        )
    except Exception as error:  # jsonschema uses several concrete exception classes
        raise PlatformInventoryError(f"platform inventory receipt is invalid: {error}") from error
    return receipt


def write_receipt(path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Write a digest-bound receipt and return the sealed record."""

    sealed = {
        **receipt,
        "record_sha256": sha256_bytes(canonical_json(receipt).encode("utf-8")),
    }
    write_report(path, sealed)
    return sealed


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Preflight current Platform test collection against the frozen manifest."
    )
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--mode",
        choices=tuple(sorted(MODE_ALIASES)),
        default=MODE_CANDIDATE,
    )
    parser.add_argument("--selection", choices=tuple(sorted(SELECTIONS)))
    parser.add_argument("--require-match", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        mode = MODE_ALIASES[args.mode]
        if args.require_match and mode != MODE_PLATFORM_CORE:
            raise PlatformInventoryError(
                "--require-match is only valid for manual Platform Core mode"
            )
        receipt = build_receipt(
            args.repository.resolve(),
            mode=mode,
            selection=args.selection,
            manifest_path=args.manifest,
        )
        sealed = (
            write_receipt(args.output.resolve(), receipt)
            if args.output is not None
            else {
                **receipt,
                "record_sha256": sha256_bytes(canonical_json(receipt).encode("utf-8")),
            }
        )
        sys.stdout.write(json.dumps(sealed, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        if args.require_match:
            if not receipt["candidate"]["worktree_clean"]:
                raise PlatformInventoryError(
                    "manual Platform Core requires an exact clean candidate worktree"
                )
            if not receipt["frozen_comparison"]["matches"]:
                raise PlatformInventoryError(
                    "current Platform Core collection does not match frozen manifest: "
                    f"missing={len(receipt['frozen_comparison']['missing'])} "
                    f"unexpected={len(receipt['frozen_comparison']['unexpected'])}"
                )
    except (OSError, PlatformInventoryError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

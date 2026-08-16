from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from benchmarks.release.evidence import (
    canonical_json,
    environment_manifest,
    file_record,
    load_json,
    repository_binding,
    sha256_bytes,
    sha256_file,
    verify_record_digest,
    write_report,
)

SCHEMA_VERSION = "deeplaw.platform-release-gate/v1"
MANIFEST_SCHEMA_VERSION = "deeplaw.platform-core-test-manifest/v2"
HISTORICAL_MANIFEST_SCHEMA_VERSION = "deeplaw.platform-core-test-manifest/v1"
BINDING_RECEIPT_SCHEMA_VERSION = "deeplaw.platform-gate-binding-receipt/v2"
HISTORICAL_BINDING_RECEIPT_SCHEMA_VERSION = "deeplaw.platform-gate-binding-receipt/v1"
MANIFEST_FILENAME = "platform-core-test-manifest-v2.json"
SUPPORTED_SYSTEMS = frozenset({"Linux", "Darwin", "Windows"})
SUPPORTED_PYTHON_MINORS = frozenset({"3.11", "3.12", "3.13"})
REQUIRED_TEST_MODULES = frozenset(
    {
        "tests.test_golden_cli",
        "tests.test_identity_migration_v060",
        "tests.test_knowledge_cli_acceptance",
        "tests.test_knowledge_control",
        "tests.test_knowledge_maintenance",
        "tests.test_knowledge_mcp",
        "tests.test_mcp_cli",
        "tests.test_release_engineering",
        "tests.test_windows_acl",
    }
)
WINDOWS_NATIVE_TESTS = frozenset(
    {
        "test_native_windows_vault_acl_is_owner_only_after_real_ingest",
        "test_native_windows_acl_rejects_directory_junction",
        "test_host_connect_and_launcher_reject_junction_ancestor",
        "test_closed_launcher_hardens_root_home_tmp_and_work_with_native_acl",
    }
)


class PlatformGateError(RuntimeError):
    pass


_STRICT_MANDATORY_FIELDS = frozenset(
    {
        "inventory_selection",
        "inventory_count",
        "inventory_sha256",
        "missing",
        "unexpected",
        "duplicate",
        "qualification_status",
        "nonapplicable_status",
        "historical_compatibility_status",
    }
)


def _manifest_schema_path(schema_version: str) -> Path:
    suffix = "v2" if schema_version == MANIFEST_SCHEMA_VERSION else "v1"
    return Path(__file__).resolve().parents[2] / "contracts" / (
        f"platform-core-test-manifest.{suffix}.schema.json"
    )


def _manifest_digest(manifest: dict[str, Any]) -> str:
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return sha256_bytes(canonical_json(body).encode("utf-8"))


def _case_descriptor_identity(case: dict[str, Any]) -> tuple[str, str]:
    junit = case["junit"]
    return str(junit["classname"]), str(junit["name"])


def _validate_manifest_invariants(manifest: dict[str, Any]) -> None:
    common = manifest["inventories"]["common"]
    windows = manifest["inventories"]["windows"]
    common_cases = list(common["cases"])
    windows_cases = [*common_cases, *windows["additional_cases"]]
    if common["selection"] != manifest["selection"]["common"]:
        raise PlatformGateError("platform common inventory selection is inconsistent")
    if windows["selection"] != manifest["selection"]["windows"]:
        raise PlatformGateError("platform Windows inventory selection is inconsistent")
    if common["count"] != len(common_cases) or common["sha256"] != _inventory_digest(
        common_cases
    ):
        raise PlatformGateError("platform common inventory count or digest is invalid")
    if windows["count"] != len(windows_cases) or windows["sha256"] != _inventory_digest(
        windows_cases
    ):
        raise PlatformGateError("platform Windows inventory count or digest is invalid")
    for label, cases in (("common", common_cases), ("windows", windows_cases)):
        identities = [_case_descriptor_identity(case) for case in cases]
        node_ids = [str(case["node_id"]) for case in cases]
        if len(identities) != len(set(identities)) or len(node_ids) != len(set(node_ids)):
            raise PlatformGateError(f"platform {label} inventory contains duplicate cases")

    classifications = manifest["classifications"]
    expected_statuses = {
        "qualification": "not_executed",
        "nonapplicable": "nonapplicable",
        "historical_compatibility": "required_fixture",
    }
    if any(
        classifications[label]["status"] != status
        for label, status in expected_statuses.items()
    ):
        raise PlatformGateError("platform test classification status is invalid")
    classified = {
        label: {
            _case_descriptor_identity(case)
            for case in classifications[label]["cases"]
        }
        for label in (
            "qualification",
            "nonapplicable",
            "historical_compatibility",
        )
    }
    if classified["qualification"] & set(map(_case_descriptor_identity, windows_cases)):
        raise PlatformGateError("qualification cases leaked into Platform Core inventory")
    additional = {
        _case_descriptor_identity(case) for case in windows["additional_cases"]
    }
    if classified["nonapplicable"] != additional:
        raise PlatformGateError("nonapplicable cases do not match Windows-only inventory")
    if not classified["historical_compatibility"].issubset(
        set(map(_case_descriptor_identity, common_cases))
    ):
        raise PlatformGateError("historical compatibility case is outside common inventory")
    if any(
        classified[left] & classified[right]
        for left, right in (
            ("qualification", "nonapplicable"),
            ("qualification", "historical_compatibility"),
            ("nonapplicable", "historical_compatibility"),
        )
    ):
        raise PlatformGateError("platform test classifications overlap")


def load_platform_manifest(path: Path) -> dict[str, Any]:
    selected = path.resolve(strict=False)
    if path.is_symlink() or not selected.is_file():
        raise PlatformGateError("platform test manifest must be a regular file")
    try:
        manifest = load_json(selected)
        schema_version = manifest.get("schema_version")
        if schema_version not in {
            MANIFEST_SCHEMA_VERSION,
            HISTORICAL_MANIFEST_SCHEMA_VERSION,
        }:
            raise PlatformGateError("platform test manifest schema is unsupported")
        schema = load_json(_manifest_schema_path(str(schema_version)))
        Draft202012Validator(schema).validate(manifest)
    except (OSError, RuntimeError, ValidationError) as error:
        raise PlatformGateError(f"platform test manifest is invalid: {error}") from error
    if manifest.get("manifest_sha256") != _manifest_digest(manifest):
        raise PlatformGateError("platform test manifest digest is invalid")
    _validate_manifest_invariants(manifest)
    return manifest


def _case_identity(case: ET.Element) -> tuple[str, str]:
    classname = case.attrib.get("classname", "")
    name = case.attrib.get("name", "")
    if not classname or not name:
        raise PlatformGateError("JUnit testcase is missing classname or name")
    return classname, name


def _manifest_cases(manifest: dict[str, Any], *, expected_system: str) -> list[dict[str, Any]]:
    common = list(manifest["inventories"]["common"]["cases"])
    if expected_system == "Windows":
        common.extend(manifest["inventories"]["windows"]["additional_cases"])
    return common


def _inventory_digest(cases: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json(cases).encode("utf-8"))


def _strict_manifest_junit_report(
    cases: list[ET.Element],
    *,
    manifest: dict[str, Any],
    expected_system: str,
) -> dict[str, Any]:
    expected_cases = _manifest_cases(manifest, expected_system=expected_system)
    expected_identities = [
        (case["junit"]["classname"], case["junit"]["name"])
        for case in expected_cases
    ]
    observed_identities = [_case_identity(case) for case in cases]
    duplicate_identities = sorted(
        {
            identity
            for identity in observed_identities
            if observed_identities.count(identity) > 1
        }
    )
    expected_set = set(expected_identities)
    observed_set = set(observed_identities)
    missing = sorted(expected_set - observed_set)
    unexpected = sorted(observed_set - expected_set)
    if duplicate_identities:
        raise PlatformGateError(
            "JUnit inventory contains duplicate identities: "
            + ", ".join(f"{classname}::{name}" for classname, name in duplicate_identities[:8])
        )
    if missing or unexpected:
        raise PlatformGateError(
            "JUnit inventory does not match frozen manifest: "
            f"missing={len(missing)} unexpected={len(unexpected)}"
        )
    if len(observed_identities) != len(expected_identities):
        raise PlatformGateError("JUnit inventory count does not match frozen manifest")
    if any(
        case.find("failure") is not None
        or case.find("error") is not None
        or case.find("skipped") is not None
        for case in cases
    ):
        raise PlatformGateError(
            "Platform Core JUnit contains a failure, error, or unclassified skip"
        )
    if expected_system != "Windows":
        native = {
            (case["junit"]["classname"], case["junit"]["name"])
            for case in manifest["inventories"]["windows"]["additional_cases"]
        }
        if observed_set.intersection(native):
            raise PlatformGateError(
                "non-Windows Platform Core unexpectedly ran Windows native tests"
            )
    return {
        "inventory_selection": (
            manifest["selection"]["windows"]
            if expected_system == "Windows"
            else manifest["selection"]["common"]
        ),
        "inventory_count": len(expected_cases),
        "inventory_sha256": _inventory_digest(expected_cases),
        "missing": [],
        "unexpected": [],
        "duplicate": [],
        "qualification_status": manifest["classifications"]["qualification"]["status"],
        "nonapplicable_status": (
            "passed"
            if expected_system == "Windows"
            else manifest["classifications"]["nonapplicable"]["status"]
        ),
        "historical_compatibility_status": "passed",
    }


def _junit_report(
    path: Path,
    *,
    expected_system: str,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise PlatformGateError(f"JUnit report is unavailable or invalid: {error}") from error
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    if not suites:
        raise PlatformGateError("JUnit report contains no test suite")
    counters = {
        field: sum(int(suite.attrib.get(field, "0")) for suite in suites)
        for field in ("tests", "failures", "errors", "skipped")
    }
    cases = list(root.findall(".//testcase"))
    if counters["tests"] != len(cases):
        raise PlatformGateError(
            "JUnit test counter does not match testcase inventory: "
            f"{counters['tests']} vs {len(cases)}"
        )
    strict_inventory: dict[str, Any] = {}
    manifest: dict[str, Any] | None = None
    if manifest_path is not None:
        manifest = load_platform_manifest(manifest_path)
        strict_inventory = _strict_manifest_junit_report(
            cases,
            manifest=manifest,
            expected_system=expected_system,
        )
    modules = {
        case.attrib.get("classname", "")
        for case in cases
        if case.attrib.get("classname")
    }
    missing_modules = sorted(REQUIRED_TEST_MODULES - modules)
    if missing_modules:
        raise PlatformGateError(
            "mandatory suite is missing test modules: " + ", ".join(missing_modules)
        )
    names = {case.attrib.get("name", "") for case in cases}
    missing_windows = sorted(WINDOWS_NATIVE_TESTS - names) if expected_system == "Windows" else []
    if missing_windows:
        raise PlatformGateError(
            "Windows mandatory suite did not execute native tests: " + ", ".join(missing_windows)
        )
    if manifest is None and counters["tests"] < 580:
        raise PlatformGateError("mandatory suite executed fewer than 580 tests")
    if any(counters[field] for field in ("failures", "errors", "skipped")):
        raise PlatformGateError(
            "mandatory suite must have zero failures, errors, and skips: " + str(counters)
        )
    return {
        **counters,
        "minimum_test_count": 580,
        "required_modules": sorted(REQUIRED_TEST_MODULES),
        "required_modules_observed": True,
        "windows_native_required": expected_system == "Windows",
        "windows_native_tests": sorted(WINDOWS_NATIVE_TESTS),
        "windows_native_observed": not missing_windows,
        **strict_inventory,
        "junit": file_record(path, logical_name=f"pytest-{expected_system.lower()}.xml"),
    }


def _uv_toolchain_receipt() -> dict[str, str]:
    executable = shutil.which("uv")
    if executable is None:
        raise PlatformGateError("resolved uv executable is unavailable")
    resolved = Path(executable).resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise PlatformGateError("resolved uv executable is not a regular file")
    process = subprocess.run(
        [str(resolved), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise PlatformGateError("uv --version failed")
    return {
        "uv_version": process.stdout.strip(),
        "resolved_executable_name": resolved.name,
        "resolved_executable_sha256": sha256_file(resolved),
    }


def _same_binding(left: dict[str, Any], right: dict[str, Any]) -> bool:
    scalar_binding_matches = all(
        left.get(field) == right.get(field)
        for field in ("commit", "tree", "package_version", "lock_sha256", "pyproject_sha256")
    )
    return (
        scalar_binding_matches
        and left.get("contracts", {}).get("inventory_sha256")
        == right.get("contracts", {}).get("inventory_sha256")
        and left.get("migrations", {}).get("inventory_sha256")
        == right.get("migrations", {}).get("inventory_sha256")
    )


def _legacy_mandatory_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if key not in _STRICT_MANDATORY_FIELDS
    }


def _binding_receipt(
    *,
    binding: dict[str, Any],
    environment: dict[str, Any],
    toolchain: dict[str, str],
    manifest: dict[str, Any],
    manifest_path: Path,
    mandatory: dict[str, Any],
) -> dict[str, Any]:
    receipt_schema_version = (
        BINDING_RECEIPT_SCHEMA_VERSION
        if manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
        else HISTORICAL_BINDING_RECEIPT_SCHEMA_VERSION
    )
    return {
        "schema_version": receipt_schema_version,
        "binding": binding,
        "environment": environment,
        "toolchain": toolchain,
        "test_manifest": {
            "schema_version": manifest["schema_version"],
            "manifest": file_record(
                manifest_path,
                logical_name=manifest_path.name,
            ),
            "manifest_sha256": manifest["manifest_sha256"],
            "inventory_selection": mandatory["inventory_selection"],
            "inventory_count": mandatory["inventory_count"],
            "inventory_sha256": mandatory["inventory_sha256"],
            "missing": mandatory["missing"],
            "unexpected": mandatory["unexpected"],
            "duplicate": mandatory["duplicate"],
            "qualification_status": mandatory["qualification_status"],
            "nonapplicable_status": mandatory["nonapplicable_status"],
            "historical_compatibility_status": mandatory[
                "historical_compatibility_status"
            ],
        },
        "passed": True,
    }


def build_report(
    repository: Path,
    *,
    junit: Path,
    manifest_path: Path,
    binding_receipt_path: Path | None = None,
    lifecycle_path: Path,
    expected_system: str,
    expected_python: str,
) -> dict[str, Any]:
    if expected_system not in SUPPORTED_SYSTEMS:
        raise PlatformGateError(f"unsupported platform gate: {expected_system}")
    if expected_python not in SUPPORTED_PYTHON_MINORS:
        raise PlatformGateError(
            f"unsupported Python platform gate: {expected_python}"
        )
    manifest = load_platform_manifest(manifest_path)
    binding = repository_binding(repository)
    toolchain = _uv_toolchain_receipt()
    environment = environment_manifest(uv_executable=shutil.which("uv") or "uv")
    if environment["platform_system"] != expected_system:
        raise PlatformGateError(
            f"runner platform is {environment['platform_system']}, expected {expected_system}"
        )
    observed_python = ".".join(environment["python_version"].split(".")[:2])
    if observed_python != expected_python:
        raise PlatformGateError(
            f"runner Python is {observed_python}, expected {expected_python}"
        )
    if not binding["worktree_clean"]:
        raise PlatformGateError("platform gate requires a clean release commit")
    lifecycle = load_json(lifecycle_path)
    verify_record_digest(lifecycle, field="distribution lifecycle")
    if lifecycle.get("schema_version") != "deeplaw.distribution-lifecycle/v1":
        raise PlatformGateError("distribution lifecycle schema is unsupported")
    if lifecycle.get("passed") is not True or not all(lifecycle.get("gates", {}).values()):
        raise PlatformGateError("distribution lifecycle did not pass every gate")
    if not _same_binding(binding, lifecycle.get("binding", {})):
        raise PlatformGateError("distribution lifecycle targets a different candidate")
    lifecycle_system = lifecycle.get("environment", {}).get("platform_system")
    if lifecycle_system != expected_system:
        raise PlatformGateError("distribution lifecycle targets a different operating system")
    lifecycle_python = ".".join(
        lifecycle.get("environment", {}).get("python_version", "").split(".")[:2]
    )
    if lifecycle_python != expected_python:
        raise PlatformGateError("distribution lifecycle targets a different Python version")
    strict_mandatory = _junit_report(
        junit,
        expected_system=expected_system,
        manifest_path=manifest_path,
    )
    mandatory = _legacy_mandatory_report(strict_mandatory)
    report = {
        "schema_version": SCHEMA_VERSION,
        "binding": binding,
        "environment": environment,
        "mandatory_suite": mandatory,
        "distribution_lifecycle": {
            "report": file_record(
                lifecycle_path,
                logical_name=f"distribution-lifecycle-{expected_system.lower()}.json",
            ),
            "wheel_sha256": lifecycle["artifacts"]["wheel"]["sha256"],
            "sdist_sha256": lifecycle["artifacts"]["sdist"]["sha256"],
            "legacy_wheel_sha256": lifecycle["artifacts"]["legacy_wheel"]["sha256"],
            "gates": lifecycle["gates"],
        },
        "coverage": {
            "cli_configuration": True,
            "migration_rollback": True,
            "snapshot_restore": True,
            "mcp_stdio_protocol": True,
            "mcp_read_only_tools": True,
            "corruption_regressions": True,
            "file_lock_regressions": True,
            "permission_regressions": True,
            "windows_acl_junction_reparse": expected_system == "Windows",
            "python_minor": expected_python,
        },
        "mandatory_skips_accepted": False,
        "passed": True,
    }
    if binding_receipt_path is not None:
        binding_receipt = _binding_receipt(
            binding=binding,
            environment=environment,
            toolchain=toolchain,
            manifest=manifest,
            manifest_path=manifest_path,
            mandatory=strict_mandatory,
        )
        binding_receipt["record_sha256"] = sha256_bytes(
            canonical_json(binding_receipt).encode("utf-8")
        )
        receipt_suffix = "v2" if manifest["schema_version"] == MANIFEST_SCHEMA_VERSION else "v1"
        binding_schema = load_json(
            Path(__file__).resolve().parents[2]
            / f"contracts/platform-gate-binding-receipt.{receipt_suffix}.schema.json"
        )
        Draft202012Validator(binding_schema).validate(binding_receipt)
        write_report(
            binding_receipt_path,
            binding_receipt,
        )
    return report


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Freeze one no-skip operating-system release gate."
    )
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--binding-receipt", type=Path)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--expected-system", choices=sorted(SUPPORTED_SYSTEMS), required=True)
    parser.add_argument(
        "--expected-python",
        choices=sorted(SUPPORTED_PYTHON_MINORS),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build_report(
            args.repository.resolve(),
            junit=args.junit.resolve(),
            manifest_path=args.manifest,
            binding_receipt_path=(
                args.binding_receipt.resolve() if args.binding_receipt is not None else None
            ),
            lifecycle_path=args.lifecycle.resolve(),
            expected_system=args.expected_system,
            expected_python=args.expected_python,
        )
        write_report(args.output.resolve(), report)
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from benchmarks.release.evidence import (
    environment_manifest,
    file_record,
    load_json,
    repository_binding,
    verify_record_digest,
    write_report,
)

SCHEMA_VERSION = "deeplaw.platform-release-gate/v1"
SUPPORTED_SYSTEMS = frozenset({"Linux", "Darwin", "Windows"})
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
    }
)


class PlatformGateError(RuntimeError):
    pass


def _junit_report(path: Path, *, expected_system: str) -> dict[str, Any]:
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
    if counters["tests"] < 580:
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
        "junit": file_record(path, logical_name=f"pytest-{expected_system.lower()}.xml"),
    }


def _same_binding(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        left.get(field) == right.get(field)
        for field in ("commit", "tree", "package_version", "lock_sha256", "pyproject_sha256")
    ) and left.get("contracts", {}).get("inventory_sha256") == right.get("contracts", {}).get(
        "inventory_sha256"
    )


def build_report(
    repository: Path,
    *,
    junit: Path,
    lifecycle_path: Path,
    expected_system: str,
) -> dict[str, Any]:
    if expected_system not in SUPPORTED_SYSTEMS:
        raise PlatformGateError(f"unsupported platform gate: {expected_system}")
    binding = repository_binding(repository)
    environment = environment_manifest()
    if environment["platform_system"] != expected_system:
        raise PlatformGateError(
            f"runner platform is {environment['platform_system']}, expected {expected_system}"
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
    mandatory = _junit_report(junit, expected_system=expected_system)
    return {
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
        },
        "mandatory_skips_accepted": False,
        "passed": True,
    }


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Freeze one no-skip operating-system release gate."
    )
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--expected-system", choices=sorted(SUPPORTED_SYSTEMS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build_report(
            args.repository.resolve(),
            junit=args.junit.resolve(),
            lifecycle_path=args.lifecycle.resolve(),
            expected_system=args.expected_system,
        )
        write_report(args.output.resolve(), report)
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release.evidence import canonical_json, sha256_bytes
from benchmarks.release.platform_gate import (
    MANIFEST_SCHEMA_VERSION,
    PlatformGateError,
    _binding_receipt,
    _junit_report,
    _manifest_digest,
    load_platform_manifest,
)
from benchmarks.release.platform_inventory import (
    CANDIDATE_STATUS,
    build_receipt,
    inventory_digest,
    write_receipt,
)
from benchmarks.release.platform_inventory import (
    SCHEMA_VERSION as INVENTORY_SCHEMA_VERSION,
)

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY / "benchmarks/release/platform-core-test-manifest-v1.json"
SCHEMA_PATH = REPOSITORY / "contracts/platform-core-test-manifest.v1.schema.json"
INVENTORY_SCHEMA_PATH = (
    REPOSITORY / "contracts/platform-core-inventory-preflight.v1.schema.json"
)


def test_platform_core_manifest_is_closed_frozen_and_digest_bound() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)

    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["selection"]["common"] == "not qualification and not windows_native"
    assert manifest["selection"]["windows"] == "not qualification"
    assert manifest["inventories"]["common"]["count"] > 1_000
    assert manifest["inventories"]["windows"]["count"] > manifest["inventories"]["common"]["count"]
    assert len(manifest["classifications"]["qualification"]["cases"]) == 6
    assert manifest["classifications"]["nonapplicable"]["status"] == "nonapplicable"
    assert (
        manifest["classifications"]["historical_compatibility"]["status"]
        == "required_fixture"
    )
    assert len(manifest["classifications"]["historical_compatibility"]["cases"]) == 1
    assert manifest["manifest_sha256"] == _manifest_digest(manifest)
    assert load_platform_manifest(MANIFEST_PATH) == manifest


def test_platform_core_manifest_generation_commands_are_explicit() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    commands = manifest["generation"]["commands"]
    assert commands == [
        "uv run --frozen pytest --collect-only -q -o addopts='' -m "
        "'not qualification and not windows_native'",
        "uv run --frozen pytest --collect-only -q -o addopts='' -m 'not qualification'",
    ]
    assert manifest["inventories"]["common"]["count"] == len(
        manifest["inventories"]["common"]["cases"]
    )
    assert manifest["inventories"]["windows"]["count"] == (
        len(manifest["inventories"]["common"]["cases"])
        + len(manifest["inventories"]["windows"]["additional_cases"])
    )


def test_platform_inventory_candidate_receipt_is_closed_and_preserves_drift(
    tmp_path: Path,
) -> None:
    manifest_before = MANIFEST_PATH.read_bytes()
    receipt = build_receipt(REPOSITORY, mode="candidate", selection="common")
    output = tmp_path / "candidate-current-source-inventory.json"
    sealed = write_receipt(output, receipt)

    schema = json.loads(INVENTORY_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(sealed)
    assert sealed["schema_version"] == INVENTORY_SCHEMA_VERSION
    assert sealed["mode"] == "candidate"
    assert sealed["status"] == CANDIDATE_STATUS
    assert sealed["release_ready"] is False
    assert isinstance(sealed["candidate"]["worktree_clean"], bool)
    assert sealed["node_ids"] == sorted(sealed["node_ids"])
    assert sealed["count"] == len(sealed["node_ids"])
    assert sealed["digest"] == inventory_digest(sealed["node_ids"])
    assert sealed["frozen_comparison"]["expected_count"] == 1339
    assert {
        "tests/test_subprocess_environment.py::test_closed_environment_maps_isolated_home_to_windows_userprofile",
        "tests/test_v013_runtime_stability.py::test_rss_child_uses_closed_portable_environment",
    } <= set(sealed["frozen_comparison"]["unexpected"])
    assert MANIFEST_PATH.read_bytes() == manifest_before


def test_platform_core_manifest_rejects_tampered_digest(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["inventories"]["common"]["cases"][0]["node_id"] += "-tampered"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest digest is invalid"):
        load_platform_manifest(path)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["inventories"]["common"]["sha256"] = "0" * 64
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="common inventory count or digest"):
        load_platform_manifest(path)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["classifications"]["historical_compatibility"]["status"] = "not_executed"
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="classification status"):
        load_platform_manifest(path)


def test_platform_core_manifest_requires_exact_junit_inventory(tmp_path: Path) -> None:
    manifest = load_platform_manifest(MANIFEST_PATH)
    cases = manifest["inventories"]["common"]["cases"]
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests=str(len(cases)))
    for case in cases:
        ET.SubElement(suite, "testcase", **case["junit"])
    junit = tmp_path / "common.xml"
    ET.ElementTree(root).write(junit, encoding="utf-8", xml_declaration=True)

    report = _junit_report(
        junit,
        expected_system="Darwin",
        manifest_path=MANIFEST_PATH,
    )
    assert report["inventory_count"] == manifest["inventories"]["common"]["count"]
    assert report["inventory_sha256"] == manifest["inventories"]["common"]["sha256"]

    binding_receipt = _binding_receipt(
        binding={
            "commit": "a" * 40,
            "tree": "b" * 40,
            "worktree_clean": True,
            "package_version": "0.12.0",
            "lock_sha256": "c" * 64,
            "pyproject_sha256": "d" * 64,
            "contracts": {},
            "migrations": {},
        },
        environment={
            "platform_system": "Darwin",
            "platform_release": "test",
            "platform_version": "test",
            "machine": "arm64",
            "python_implementation": "CPython",
            "python_version": "3.11.0",
            "python_executable_name": "python",
            "uv_version": "uv 0.11.5",
            "ci": False,
            "github_actions": False,
            "github_runner_os": None,
            "github_runner_arch": None,
        },
        toolchain={
            "uv_version": "uv 0.11.5",
            "resolved_executable_name": "uv",
            "resolved_executable_sha256": "e" * 64,
        },
        manifest=manifest,
        manifest_path=MANIFEST_PATH,
        mandatory=report,
    )
    binding_receipt["record_sha256"] = sha256_bytes(
        canonical_json(binding_receipt).encode("utf-8")
    )
    binding_schema = json.loads(
        (
            REPOSITORY
            / "contracts/platform-gate-binding-receipt.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(binding_schema).validate(binding_receipt)

    duplicate = ET.SubElement(suite, "testcase", **cases[0]["junit"])
    del duplicate
    duplicate_junit = tmp_path / "duplicate.xml"
    root.find("testsuite").set("tests", str(len(cases) + 1))  # type: ignore[union-attr]
    ET.ElementTree(root).write(duplicate_junit, encoding="utf-8", xml_declaration=True)
    with pytest.raises(PlatformGateError, match="duplicate"):
        _junit_report(
            duplicate_junit,
            expected_system="Darwin",
            manifest_path=MANIFEST_PATH,
        )

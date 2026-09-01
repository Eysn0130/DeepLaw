from __future__ import annotations

import hashlib
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
    frozen_comparison,
    inventory_digest,
    write_receipt,
)
from benchmarks.release.platform_inventory import (
    SCHEMA_VERSION as INVENTORY_SCHEMA_VERSION,
)

REPOSITORY = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY / "benchmarks/release/platform-core-test-manifest-v2.json"
SCHEMA_PATH = REPOSITORY / "contracts/platform-core-test-manifest.v2.schema.json"
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
    assert len(manifest["classifications"]["qualification"]["cases"]) == 12
    qualification_ids = {
        case["node_id"]
        for case in manifest["classifications"]["qualification"]["cases"]
    }
    assert (
        "tests/test_v013_pass26_opencode_real_loader.py::"
        "test_exact_opencode_loads_project_plugin_and_dispatches_native_session_event"
        in qualification_ids
    )
    windows_ids = {
        case["node_id"] for case in manifest["inventories"]["common"]["cases"]
    } | {
        case["node_id"]
        for case in manifest["inventories"]["windows"]["additional_cases"]
    }
    assert windows_ids >= {
        "tests/test_v013_pass22_host_lifecycle.py::"
        "test_lifecycle_config_requires_owner_only_non_symlink_file"
    }
    assert manifest["classifications"]["nonapplicable"]["status"] == "nonapplicable"
    assert (
        manifest["classifications"]["nonapplicable"]["selection"]
        == "platform-specific tests outside their applicable OS"
    )
    assert (
        manifest["classifications"]["historical_compatibility"]["status"]
        == "required_fixture"
    )
    assert len(manifest["classifications"]["historical_compatibility"]["cases"]) == 1
    assert manifest["manifest_sha256"] == _manifest_digest(manifest)
    assert load_platform_manifest(MANIFEST_PATH) == manifest


def test_platform_core_v1_bytes_remain_historical() -> None:
    expected = {
        "benchmarks/release/platform-core-test-manifest-v1.json": (
            "90ef22db1725f07aca8e6ab68cb65f305f87a9145c23d351b9ae69de3d3df70e"
        ),
        "contracts/platform-core-test-manifest.v1.schema.json": (
            "e65dbe0ea8fa98d383b19be42400c7be733de1d0c748cc00a44f03584896a94e"
        ),
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((REPOSITORY / relative).read_bytes()).hexdigest() == digest


def test_platform_core_manifest_generation_commands_are_explicit() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    commands = manifest["generation"]["commands"]
    assert commands == [
        "uv run --frozen pytest --collect-only -q -o addopts='' -m "
        "'not qualification and not windows_native'",
        "uv run --frozen pytest --collect-only -q -o addopts='' -m 'not qualification'",
        "uv run --frozen pytest --collect-only -q -o addopts='' -m 'qualification'",
    ]
    assert manifest["inventories"]["common"]["count"] == len(
        manifest["inventories"]["common"]["cases"]
    )
    assert manifest["inventories"]["windows"]["count"] == (
        len(manifest["inventories"]["common"]["cases"])
        + len(manifest["inventories"]["windows"]["additional_cases"])
    )


def test_platform_core_qualification_inventory_drift_is_visible() -> None:
    manifest = load_platform_manifest(MANIFEST_PATH)
    expected = sorted(
        case["node_id"]
        for case in manifest["classifications"]["qualification"]["cases"]
    )
    result = frozen_comparison(
        expected[1:],
        manifest=manifest,
        selection="qualification",
    )
    assert result["matches"] is False
    assert result["missing"] == [expected[0]]


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
    assert sealed["frozen_comparison"]["expected_count"] == sealed["count"]
    assert sealed["frozen_comparison"]["unexpected"] == []
    assert sealed["frozen_comparison"]["missing"] == []
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

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    additional = manifest["inventories"]["windows"]["additional_cases"]
    manifest["classifications"]["nonapplicable"]["cases"] = [
        case
        for case in manifest["classifications"]["nonapplicable"]["cases"]
        if case["node_id"] != additional[0]["node_id"]
    ]
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(
        PlatformGateError,
        match="Windows additional cases must be classified nonapplicable",
    ):
        load_platform_manifest(path)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["classifications"]["nonapplicable"]["cases"].append(
        {
            "junit": {
                "classname": "tests.fixture",
                "name": "test_outside_platform_inventory",
            },
            "node_id": "tests/fixture.py::test_outside_platform_inventory",
        }
    )
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(
        PlatformGateError,
        match="nonapplicable cases must belong to the Platform inventory",
    ):
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
            / "contracts/platform-gate-binding-receipt.v2.schema.json"
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

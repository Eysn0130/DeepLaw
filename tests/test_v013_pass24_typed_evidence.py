from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from benchmarks.release.typed_qualification_evidence import (
    TypedQualificationEvidenceError,
    parse_typed_evidence,
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


COMMIT = "a" * 40
TREE = "b" * 40
LOCK = "c" * 64
WHEEL = "d" * 64
SDIST = "e" * 64
CORPUS = "f" * 64
RUNNER = {"identity": "synthetic-runner", "sha256": "1" * 64}
SCORER = {"identity": "synthetic-scorer", "sha256": "2" * 64}


def _json_file(root: Path, name: str, value: Any) -> dict[str, Any]:
    raw = _canonical(value)
    path = root / name
    path.write_bytes(raw)
    return {
        "relative_path": name,
        "byte_size": len(raw),
        "sha256": _sha(raw),
        "media_type": "application/json",
    }


def _bytes_file(
    root: Path,
    name: str,
    raw: bytes,
    *,
    media_type: str,
) -> dict[str, Any]:
    path = root / name
    path.write_bytes(raw)
    return {
        "relative_path": name,
        "byte_size": len(raw),
        "sha256": _sha(raw),
        "media_type": media_type,
    }


def _legal_raw_evidence(source: Mapping[str, Any], index: int) -> dict[str, Any]:
    quote = f"Exact quote {index}"
    fragment_text = f"Canonical fragment {index}: {quote}"
    return {
        "version_id": source["version_id"],
        "authority": source["authority"],
        "fragment": {
            "document_id": source["source_id"],
            "fragment_id": f"fragment-{index:02d}",
            "text": fragment_text,
            "text_sha256": _sha(fragment_text.encode()),
        },
        "locator": {"kind": "page", "value": str(index + 1)},
        "quote_text": quote,
        "quote_sha256": _sha(quote.encode()),
        "effective_date": source["effective_date"],
        "exception": [],
        "proviso": [],
        "cross_reference": [],
        "ocr_critical_token": [],
        "wrong_version": False,
        "gap": None,
        "wiki_drill_down": None,
    }


def _envelope(
    root: Path,
    *,
    kind: str,
    payload: dict[str, Any],
    name: str = "typed-evidence.json",
    candidate_binding: dict[str, str] | None = None,
    corpus_sha256: str = CORPUS,
    corpus_role: str = "candidate_full",
) -> Path:
    selected_candidate = candidate_binding or {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": LOCK,
        "wheel_sha256": WHEEL,
        "sdist_sha256": SDIST,
    }
    value: dict[str, Any] = {
        "schema_version": "deeplaw.typed-qualification-evidence/v1",
        "kind": kind,
        "candidate_binding": selected_candidate,
        "run_binding": {"run_id": "run-v013-synthetic", "workflow_run_id": 1},
        "corpus": {"sha256": corpus_sha256, "role": corpus_role},
        "runner": RUNNER,
        "scorer": SCORER,
        "payload": payload,
        "record_sha256": "",
    }
    value["record_sha256"] = _sha(
        _canonical({key: item for key, item in value.items() if key != "record_sha256"})
    )
    path = root / name
    path.write_bytes(_canonical(value))
    return path


def _receipt(
    *,
    corpus_sha256: str = CORPUS,
    corpus_role: str = "candidate_full",
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "candidate": dict(
            candidate
            or {
                "commit": COMMIT,
                "tree": TREE,
                "lock_sha256": LOCK,
                "wheel_sha256": WHEEL,
                "sdist_sha256": SDIST,
            }
        ),
        "run": {"run_id": "run-v013-synthetic", "workflow_run_id": 1},
        "corpus": {"sha256": corpus_sha256, "role": corpus_role},
        "runner": dict(RUNNER),
        "scorer": dict(SCORER),
    }


def _platform_manifest(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks/release/platform-core-test-manifest-v2.json"
    )
    raw = manifest_path.read_bytes()
    return json.loads(raw), _bytes_file(
        root,
        "platform-core-test-manifest-v2.json",
        raw,
        media_type="application/json",
    )


def _platform_junit_bytes(
    manifest: Mapping[str, Any],
    *,
    failure: bool = False,
    cell: str = "cell",
    windows: bool = False,
) -> bytes:
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", {"name": f"pytest-{cell}"})
    cases = list(manifest["inventories"]["common"]["cases"])
    if windows:
        cases.extend(manifest["inventories"]["windows"]["additional_cases"])
    for index, item in enumerate(cases):
        junit = item["junit"]
        testcase = ET.SubElement(
            suite,
            "testcase",
            {"classname": junit["classname"], "name": junit["name"]},
        )
        if failure and index == 0:
            ET.SubElement(testcase, "failure")
        elif failure and index == 1:
            ET.SubElement(testcase, "skipped")
    return ET.tostring(root, encoding="utf-8")


def _assert_reject(path: Path, root: Path | None = None) -> None:
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(path, root=root)


def test_candidate_full_junit_recomputes_testcases_and_ignores_suite_counts(
    tmp_path: Path,
) -> None:
    junit = _bytes_file(
        tmp_path,
        "candidate.xml",
        (
            b'<testsuites tests="999" failures="0"><testsuite tests="999">'
            b'<testcase classname="tests.test_knowledge_control" '
            b'name="test_interrupted_migration_rolls_back_and_retains_a_verified_backup"/>'
            b'<testcase classname="tests.test_v013_pass22_continuity_closure" '
            b'name="test_partial_checkpoint_recovers_after_process_exit_and_restart"/>'
            b'<testcase classname="fixture" name="skip"><skipped/></testcase>'
            b"</testsuite></testsuites>"
        ),
        media_type="application/xml",
    )
    path = _envelope(tmp_path, kind="candidate_full_junit", payload={"source": junit})

    result = parse_typed_evidence(path)

    assert result["status"] == "failed"
    assert result["metrics"]["testcase_count"] == 3
    assert result["metrics"]["successful_testcase_count"] == 2
    assert result["hard_failure_counts"]["junit_skip"] == 1


def test_forbidden_caller_pass_fact_and_duplicate_or_nonfinite_json_are_rejected(
    tmp_path: Path,
) -> None:
    raw = (
        b'{"rows":[{"case_id":"x","platform":"linux",'
        b'"python_version":"3.12","outcome":"success","artifact_sha256":"'
        + WHEEL.encode()
        + b'"}],"passed":true}'
    )
    receipt = _bytes_file(tmp_path, "platform.json", raw, media_type="application/json")
    path = _envelope(tmp_path, kind="candidate_platform_receipt", payload={"source": receipt})
    _assert_reject(path)

    duplicate = b'{"rows":[],"rows":[]}'
    duplicate_ref = _bytes_file(
        tmp_path,
        "duplicate.json",
        duplicate,
        media_type="application/json",
    )
    duplicate_path = _envelope(
        tmp_path,
        kind="candidate_platform_receipt",
        payload={"source": duplicate_ref},
        name="duplicate-envelope.json",
    )
    _assert_reject(duplicate_path)

    nonfinite = (
        b'{"rows":[{"case_id":"x","platform":"linux",'
        b'"python_version":"3.12","outcome":"success","artifact_sha256":"'
        + WHEEL.encode()
        + b'","latency":1e999}]}'
    )
    nonfinite_ref = _bytes_file(
        tmp_path,
        "nonfinite.json",
        nonfinite,
        media_type="application/json",
    )
    nonfinite_path = _envelope(
        tmp_path,
        kind="candidate_platform_receipt",
        payload={"source": nonfinite_ref},
        name="nonfinite-envelope.json",
    )
    _assert_reject(nonfinite_path)

    binary_xml = _bytes_file(
        tmp_path,
        "invalid-utf8.xml",
        b"\xff\xfe",
        media_type="application/xml",
    )
    binary_path = _envelope(
        tmp_path,
        kind="candidate_full_junit",
        payload={"source": binary_xml},
        name="invalid-utf8-envelope.json",
    )
    _assert_reject(binary_path)


def test_manifest_closure_and_self_consistent_expected_observed_are_rejected(
    tmp_path: Path,
) -> None:
    closure_root = tmp_path / "closure"
    closure_root.mkdir()
    platform_source = _json_file(
        closure_root,
        "platform.json",
        {
            "rows": [
                {
                    "case_id": "case-1",
                    "platform": "ubuntu",
                    "python_version": "3.12",
                    "outcome": "success",
                    "artifact_sha256": WHEEL,
                }
            ]
        },
    )
    platform_path = _envelope(
        closure_root,
        kind="candidate_platform_receipt",
        payload={"source": platform_source},
    )
    (closure_root / "orphan.txt").write_text("unreferenced", encoding="utf-8")
    _assert_reject(platform_path)

    self_root = tmp_path / "self-consistent"
    self_root.mkdir()
    expected = _json_file(
        self_root,
        "expected.json",
        {
            "rows": [
                {
                    "journey_id": "self-1",
                    "operation": "rename",
                    "before": {
                        "identity": "wiki:one",
                        "revision_sha256": "a" * 64,
                        "exists": True,
                    },
                    "after": {
                        "identity": "wiki:one",
                        "revision_sha256": "a" * 64,
                        "exists": True,
                    },
                    "expected": True,
                }
            ]
        },
    )
    self_path = _envelope(
        self_root,
        kind="wiki_journey_rows",
        payload={"expected_source": expected, "observed_source": expected},
    )
    envelope = json.loads(self_path.read_text(encoding="utf-8"))
    envelope["corpus"]["sha256"] = expected["sha256"]
    envelope["record_sha256"] = _sha(
        _canonical({key: item for key, item in envelope.items() if key != "record_sha256"})
    )
    self_path.write_bytes(_canonical(envelope))
    _assert_reject(self_path)


def test_candidate_platform_receipt_requires_real_rows_and_exact_wheel_binding(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, Any]] = []
    junit_sources: list[dict[str, Any]] = []
    platform_manifest, platform_manifest_ref = _platform_manifest(tmp_path)
    for platform in ("ubuntu", "macos", "windows"):
        for python_version in ("3.11", "3.12", "3.13"):
            raw = _platform_junit_bytes(
                platform_manifest,
                cell=f"{platform}-{python_version}",
                windows=platform == "windows",
            )
            source_ref = _bytes_file(
                tmp_path,
                f"platform-{platform}-{python_version}.xml",
                raw,
                media_type="application/xml",
            )
            junit_sources.append(
                {
                    "platform": platform,
                    "python_version": python_version,
                    "source": source_ref,
                }
            )
            rows.append(
                {
                    "platform": platform,
                    "python_version": python_version,
                    "artifact_sha256": WHEEL,
                    "junit_source_sha256": source_ref["sha256"],
                }
            )
    value = {
        "receipt": _receipt(),
        "rows": rows,
    }
    source = _json_file(tmp_path, "platform.json", value)
    path = _envelope(
        tmp_path,
        kind="candidate_platform_receipt",
        payload={
            "source": source,
            "platform_manifest_source": platform_manifest_ref,
            "junit_sources": junit_sources,
        },
    )
    result = parse_typed_evidence(path)
    assert result["status"] == "passed"
    assert result["metrics"]["row_count"] == 9
    assert result["metrics"]["testcase_count"] == (
        6 * platform_manifest["inventories"]["common"]["count"]
        + 3 * platform_manifest["inventories"]["windows"]["count"]
    )
    duplicate_path = _envelope(
        tmp_path,
        kind="candidate_platform_receipt",
        payload={
            "source": source,
            "platform_manifest_source": platform_manifest_ref,
            "junit_sources": [*junit_sources, junit_sources[0]],
        },
        name="platform-duplicate-source.json",
    )
    _assert_reject(duplicate_path)

    failure_root = tmp_path / "platform-failure"
    failure_root.mkdir()
    failure_rows: list[dict[str, Any]] = []
    failure_sources: list[dict[str, Any]] = []
    failure_manifest, failure_manifest_ref = _platform_manifest(failure_root)
    cells = [
        (platform, python_version)
        for platform in ("ubuntu", "macos", "windows")
        for python_version in ("3.11", "3.12", "3.13")
    ]
    for index, (platform, python_version) in enumerate(cells):
        raw = _platform_junit_bytes(
            failure_manifest,
            failure=index == 0,
            cell=f"{platform}-{python_version}",
            windows=platform == "windows",
        )
        source_ref = _bytes_file(
            failure_root,
            f"platform-{platform}-{python_version}.xml",
            raw,
            media_type="application/xml",
        )
        failure_sources.append(
            {"platform": platform, "python_version": python_version, "source": source_ref}
        )
        failure_rows.append(
            {
                "platform": platform,
                "python_version": python_version,
                "artifact_sha256": WHEEL,
                "junit_source_sha256": source_ref["sha256"],
            }
        )
    failure_value = {
        "receipt": _receipt(),
        "rows": failure_rows,
    }
    failure_source = _json_file(failure_root, "platform.json", failure_value)
    failure_path = _envelope(
        failure_root,
        kind="candidate_platform_receipt",
        payload={
            "source": failure_source,
            "platform_manifest_source": failure_manifest_ref,
            "junit_sources": failure_sources,
        },
    )
    failure_result = parse_typed_evidence(failure_path)
    assert failure_result["status"] == "failed"
    assert failure_result["hard_failure_counts"]["platform_failure"] == 1
    assert failure_result["hard_failure_counts"]["platform_skip"] == 1

    value["rows"][0]["artifact_sha256"] = "9" * 64
    bad_source = _json_file(tmp_path, "platform-bad.json", value)
    bad_path = _envelope(
        tmp_path,
        kind="candidate_platform_receipt",
        payload={
            "source": bad_source,
            "platform_manifest_source": platform_manifest_ref,
            "junit_sources": junit_sources,
        },
        name="platform-bad-envelope.json",
    )
    _assert_reject(bad_path)


def _native_event() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.native-host-event/v2",
        "provenance_level": "native_plugin_hook",
        "host": "codex",
        "host_identity": {
            "binary_version": "codex-cli 0.148.0-alpha.9",
            "binary_sha256": "6170ff5578170ee9b74ad92bfcff96e6186f41d02b60815a7c2b01ad424c754f",
            "request_model": "gpt-5.6-luna",
            "reasoning": "max",
        },
        "event_type": "SessionStart",
        "event_sequence": {"index": 0},
        "session_sha256": "3" * 64,
        "parent_session_sha256": None,
        "observation": {"methods_observed": ["SessionStart"], "status": "received"},
        "route": {
            "status": "exact",
            "binding_sha256": "4" * 64,
            "task_handle_sha256": "5" * 64,
            "project_sha256": "6" * 64,
            "repository_sha256": "7" * 64,
            "worktree_sha256": "8" * 64,
        },
    }


def test_host_event_sequence_requires_event_derived_lifecycle_receipt(
    tmp_path: Path,
) -> None:
    from deeplaw.native_host import derive_native_host_receipt

    event = _native_event()
    lifecycle = derive_native_host_receipt(event)
    host_identity_sha256 = _sha(_canonical(event["host_identity"]))
    host_metadata = {
        "run_id": "run-v013-synthetic",
        "workflow_run_id": 1,
        "task_case": "cold/new",
        "host": "codex",
        "actual_response_model_id": "gpt-5.6-luna",
    }
    event_source = _json_file(
        tmp_path,
        "events.json",
        {**host_metadata, "events": [event]},
    )
    lifecycle_source = _json_file(
        tmp_path,
        "lifecycle.json",
        {**host_metadata, "receipts": [lifecycle]},
    )
    continuity_result = {
        "schema_version": "deeplaw.task-continuity-result/v2",
        "operation": "resume",
        "status": "exact",
        "write_performed": False,
        "transcript_copied": False,
        "native_host_lifecycle_observed": False,
        "task_handle_sha256": "5" * 64,
        "project_sha256": "6" * 64,
        "binding_sha256": "4" * 64,
        "workspace_bound": True,
        "checkpoint_route_status": "exact",
        "gap_codes": [],
        "run_id": "run-v013-synthetic",
    }
    continuity_source = _json_file(
        tmp_path,
        "continuity.json",
        {"task_case": "cold/new", "result": continuity_result},
    )
    expected_source = _json_file(
        tmp_path,
        "host-expected.json",
        {
            "rows": [
                {
                    "task_case": "cold/new",
                    "event_indices": [0],
                    "operations": [lifecycle["operation"]],
                    "routes": [lifecycle["route_binding_provenance"]],
                    "gap_codes": ["route_unverified"],
                    "continuity": {
                        "status": "exact",
                        "gap_codes": [],
                        "binding_sha256": "4" * 64,
                        "task_handle_sha256": "5" * 64,
                        "project_sha256": "6" * 64,
                    },
                }
            ]
        },
    )
    isolation = {
        "schema_version": "deeplaw.host-isolation-receipt/v1",
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": LOCK,
            "wheel_sha256": WHEEL,
            "sdist_sha256": SDIST,
        },
        "run_binding": {"run_id": "run-v013-synthetic", "workflow_run_id": 1},
        "corpus": {"sha256": expected_source["sha256"], "role": "candidate_full"},
        "runner": RUNNER,
        "scorer": SCORER,
        "host": "codex",
        "task_case": "cold/new",
        "owner_env": {
            "path_class": "owner_only_external_env",
            "outside_repository": True,
            "regular": True,
            "symlink": False,
            "owner_only": True,
        },
        "host_parent_expected_secret_present": True,
        "mcp_child_expected_secret_present": False,
        "auth_read_or_recorded": False,
        "transcript_read_or_recorded": False,
        "raw_prompt_read_or_recorded": False,
        "reasoning_read_or_recorded": False,
        "secret_value_read_or_recorded": False,
        "record_sha256": "",
    }
    isolation["record_sha256"] = _sha(
        _canonical({key: item for key, item in isolation.items() if key != "record_sha256"})
    )
    isolation_source = _json_file(tmp_path, "host-isolation.json", isolation)
    usage_source = _json_file(
        tmp_path,
        "usage.json",
        {
            "receipt": _receipt(corpus_sha256=expected_source["sha256"]),
            "rows": [
                {
                    "run_id": "run-v013-synthetic",
                    "workflow_run_id": 1,
                    "task_case": "cold/new",
                    "host": "codex",
                    "actual_response_model_id": "gpt-5.6-luna",
                    "host_identity_sha256": host_identity_sha256,
                    "candidate_commit": COMMIT,
                    "candidate_tree": TREE,
                    "corpus_sha256": expected_source["sha256"],
                    "runner_identity": RUNNER["identity"],
                    "runner_sha256": RUNNER["sha256"],
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "cache_tokens": 3,
                    "reasoning_tokens": 4,
                    "provider_bytes": 5,
                    "latency_ms": 6,
                    "rss_peak_bytes": 7,
                }
            ]
        },
    )
    path = _envelope(
        tmp_path,
        kind="host_event_sequence",
        payload={
            "event_source": event_source,
            "lifecycle_source": lifecycle_source,
            "usage_source": usage_source,
            "expected_source": expected_source,
            "continuity_source": continuity_source,
            "isolation_source": isolation_source,
        },
        corpus_sha256=expected_source["sha256"],
    )
    result = parse_typed_evidence(
        path,
        expected_corpus_sha256=expected_source["sha256"],
    )
    assert result["metrics"]["event_count"] == 1
    assert result["metrics"]["observed_gap_codes"] == ["route_unverified"]
    assert result["hard_failure_counts"].get("host_gap_expectation_mismatch", 0) == 0

    lifecycle["event_sequence"]["index"] = 9
    lifecycle["receipt_sha256"] = _sha(
        _canonical({key: item for key, item in lifecycle.items() if key != "receipt_sha256"})
    )
    bad_source = _json_file(tmp_path, "lifecycle-bad.json", {"receipts": [lifecycle]})
    bad_path = _envelope(
        tmp_path,
        kind="host_event_sequence",
        payload={
            "event_source": event_source,
            "lifecycle_source": bad_source,
            "usage_source": usage_source,
            "expected_source": expected_source,
            "continuity_source": continuity_source,
        },
        corpus_sha256=expected_source["sha256"],
        name="host-bad-envelope.json",
    )
    _assert_reject(bad_path)


def test_exact_wheel_receipt_rechecks_record_and_candidate_identity(tmp_path: Path) -> None:
    receipt: dict[str, Any] = {
        "schema_version": "deeplaw.exact-wheel-execution-receipt/v1",
        "status": "exact_wheel_executed",
        "runner_source_sha256": "1" * 64,
        "candidate": {
            "wheel_filename": "deeplaw-0.13.0-py3-none-any.whl",
            "wheel_sha256": WHEEL,
            "wheel_size": 1,
            "path_class": "candidate_full_wheel",
        },
        "requirements": {
            "filename": "requirements.txt",
            "sha256": "7" * 64,
            "bytes": 1,
            "path_class": "candidate_requirements",
            "hash_pinned": True,
        },
        "venv": {
            "path_class": "new_isolated_venv",
            "path_sha256": "2" * 64,
            "created_new": True,
            "system_site_packages": False,
            "site_packages_path_class": "venv_site_packages",
        },
        "runtime": {
            "python_implementation": "CPython",
            "python_version": "3.12.1",
            "python_executable_sha256": "3" * 64,
            "python_executable_path_class": "new_isolated_venv",
            "distribution_name": "deeplaw",
            "distribution_version": "0.13.0",
            "import_module": "deeplaw",
            "import_file_path_class": "venv_site_packages",
            "import_file_relative_path": "deeplaw/__init__.py",
            "import_file_sha256": "4" * 64,
        },
        "entrypoint": {
            "name": "deeplaw",
            "group": "console_scripts",
            "value": "deeplaw.cli:main",
            "executable_path_class": "venv_bin",
            "executable_relative_path": "bin/deeplaw",
            "executable_sha256": "5" * 64,
            "module_path_class": "venv_site_packages",
            "module_relative_path": "deeplaw/cli.py",
            "module_sha256": "6" * 64,
        },
        "version_check": {
            "argv": ["deeplaw", "--version"],
            "exit_code": 0,
            "stdout_sha256": "8" * 64,
            "stdout_bytes": 1,
            "stdout_path_class": "sanitized_stdout",
        },
        "public_journey": {
            "journey_status": "passed",
            "journey_root_path_class": "ephemeral_journey_root",
            "step_count": 5,
            "steps": [
                {
                    "name": name,
                    "status": "passed",
                    "exit_code": 0,
                    "argv": ["deeplaw", name],
                    "stdout_sha256": "9" * 64,
                    "stdout_bytes": 1,
                    "stdout_path_class": "sanitized_stdout",
                    "output_schema_version": "deeplaw.synthetic/v1",
                    "budget": None,
                }
                for name in (
                    "knowledge_init",
                    "source_add",
                    "source_verify",
                    "evidence_first_query",
                    "bounded_context",
                )
            ],
            "network_policy": {
                "network_access": "not_requested",
                "model_sidecar": False,
                "environment_allowlist": "minimal",
            },
        },
        "network_acquisition": {
            "explicit": True,
            "mode": "candidate_wheelhouse",
            "hash_pinned": True,
        },
        "environment_policy": {
            "python_isolated_mode": True,
            "pythonpath_cleared": True,
            "pythonhome_cleared": True,
            "user_site_disabled": True,
            "network_disabled_for_install": True,
            "requirements_hashes_required": True,
            "candidate_source_only": True,
        },
        "record_sha256": "",
    }
    receipt["record_sha256"] = _sha(
        _canonical({key: item for key, item in receipt.items() if key != "record_sha256"})
    )
    source = _json_file(tmp_path, "exact-wheel.json", receipt)
    path = _envelope(
        tmp_path,
        kind="exact_wheel_execution",
        payload={"source": source},
    )
    result = parse_typed_evidence(path)
    assert result["status"] == "passed"
    assert result["metrics"]["import_origin_verified"] is True

    receipt["network_acquisition"]["mode"] = "fixed_pypi_index"
    receipt["environment_policy"]["network_disabled_for_install"] = False
    receipt["record_sha256"] = _sha(
        _canonical({key: item for key, item in receipt.items() if key != "record_sha256"})
    )
    fixed_source = _json_file(tmp_path, "exact-wheel.json", receipt)
    fixed_envelope = json.loads(path.read_text(encoding="utf-8"))
    fixed_envelope["payload"]["source"] = fixed_source
    fixed_envelope["record_sha256"] = _sha(
        _canonical(
            {key: item for key, item in fixed_envelope.items() if key != "record_sha256"}
        )
    )
    path.write_bytes(_canonical(fixed_envelope))
    assert parse_typed_evidence(path)["status"] == "passed"

    receipt["record_sha256"] = "0" * 64
    bad_source = _json_file(tmp_path, "exact-wheel-bad.json", receipt)
    bad_path = _envelope(
        tmp_path,
        kind="exact_wheel_execution",
        payload={"source": bad_source},
        name="exact-wheel-bad-envelope.json",
    )
    _assert_reject(bad_path)


def test_retained_supply_chain_rechecks_pre_publish_and_all_retained_bytes(
    tmp_path: Path,
) -> None:
    wheel_raw = b"synthetic-wheel-bytes"
    sdist_raw = b"synthetic-sdist-bytes"
    wheel_path = tmp_path / "deeplaw-0.13.0-py3-none-any.whl"
    sdist_path = tmp_path / "deeplaw-0.13.0.tar.gz"
    wheel_path.write_bytes(wheel_raw)
    sdist_path.write_bytes(sdist_raw)
    wheel_sha = _sha(wheel_raw)
    sdist_sha = _sha(sdist_raw)
    candidate_binding = {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": LOCK,
        "wheel_sha256": wheel_sha,
        "sdist_sha256": sdist_sha,
    }
    retained_value = {
        "schema_version": "deeplaw.retained-candidate-artifacts/v1",
        "package_version": "0.13.0",
        "release_ready": False,
        "claim_eligible": False,
        "git_commit": COMMIT,
        "git_tree": TREE,
        "lock_sha256": LOCK,
        "wheel": {
            "filename": wheel_path.name,
            "sha256": wheel_sha,
            "bytes": len(wheel_raw),
        },
        "sdist": {
            "filename": sdist_path.name,
            "sha256": sdist_sha,
            "bytes": len(sdist_raw),
        },
    }
    retained_source = _json_file(tmp_path, "retained.json", retained_value)
    aux = {}
    supply_values = {
        "sbom": {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "library",
                    "name": "deeplaw",
                    "version": "0.13.0",
                }
            },
            "components": [
                {
                    "type": "library",
                    "name": "deeplaw",
                    "version": "0.13.0",
                    "purl": "pkg:pypi/deeplaw@0.13.0",
                }
            ],
        },
        "openvex": {
            "@context": "https://openvex.dev/ns/v0.2.0",
            "@id": "synthetic://openvex/pass24",
            "author": "synthetic-contract-fixture",
            "timestamp": "2026-01-01T00:00:00Z",
            "version": 1,
            "statements": [
                {
                    "vulnerability": {"name": "SYNTHETIC-1"},
                    "products": [{"@id": "pkg:pypi/deeplaw@0.13.0"}],
                    "status": "not_affected",
                    "justification": "vulnerable_code_not_in_execute_path",
                    "impact_statement": "Synthetic contract fixture only.",
                }
            ],
        },
        "licenses": {
            "schema_version": "deeplaw.installed-license-inventory/v1",
            "policy_schema_version": "deeplaw.release-license-policy/v1",
            "package_count": 1,
            "status": "passed",
            "blocked": [],
            "review_required": [],
            "packages": [
                {
                    "name": "deeplaw",
                    "normalized_name": "deeplaw",
                    "version": "0.13.0",
                    "license_expression": "MIT",
                    "declared_license": "MIT",
                    "license_classifiers": [],
                    "status": "approved",
                    "reason": "synthetic contract fixture",
                }
            ],
            "binding": {
                "commit": COMMIT,
                "tree": TREE,
                "lock_sha256": LOCK,
                "package_version": "0.13.0",
                "worktree_clean": True,
            },
        },
        "provenance": {
            "schema_version": "deeplaw.reproducible-build-report/v2",
            "binding": {
                "commit": COMMIT,
                "tree": TREE,
                "lock_sha256": LOCK,
                "package_version": "0.13.0",
                "worktree_clean": True,
            },
            "environment": {"platform_system": "synthetic"},
            "repository_commit": COMMIT,
            "working_tree_dirty": False,
            "source_date_epoch": 946684800,
            "build_constraints_sha256": "6" * 64,
            "lock_sha256": LOCK,
            "build_dependencies": {
                "hatchling": "1.31.0",
                "packaging": "26.2",
                "pathspec": "1.1.1",
                "pluggy": "1.6.0",
                "trove-classifiers": "2026.6.1.19",
            },
            "reproducible": True,
            "package_inventory_verified": True,
            "artifacts": [
                {
                    "name": "deeplaw-0.13.0-py3-none-any.whl",
                    "sha256": wheel_sha,
                    "byte_size": len(wheel_raw),
                    "path_count": 1,
                    "inventory_sha256": "7" * 64,
                },
                {
                    "name": "deeplaw-0.13.0.tar.gz",
                    "sha256": sdist_sha,
                    "byte_size": len(sdist_raw),
                    "path_count": 1,
                    "inventory_sha256": "8" * 64,
                },
            ],
            "artifact_release_eligible": True,
            "artifact_release_blockers": [],
        },
    }
    supply_values["licenses"]["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in supply_values["licenses"].items()
                if key != "record_sha256"
            }
        )
    )
    supply_values["provenance"]["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in supply_values["provenance"].items()
                if key != "record_sha256"
            }
        )
    )
    for field, value in supply_values.items():
        aux[field] = _json_file(
            tmp_path,
            f"{field}.json",
            value,
        )
    pre_publish: dict[str, Any] = {
        "schema_version": "deeplaw.pre-publish-artifact-gate/v1",
        "status": "pre_publish_passed",
        "created_at": "2026-01-03T00:00:00Z",
        "candidate": {"commit": COMMIT, "tree": TREE, "lock_sha256": LOCK},
        "builds": {
            "count": 2,
            "byte_identical": True,
            "first": {
                "build_id": "first",
                "wheel_sha256": wheel_sha,
                "sdist_sha256": sdist_sha,
                "receipt_sha256": "1" * 64,
            },
            "second": {
                "build_id": "second",
                "wheel_sha256": wheel_sha,
                "sdist_sha256": sdist_sha,
                "receipt_sha256": "2" * 64,
            },
        },
            "retained_artifacts": {
                "manifest_sha256": _sha((tmp_path / "retained.json").read_bytes()),
                "manifest_path": "retained.json",
                "wheel": {
                    "name": wheel_path.name,
                    "sha256": wheel_sha,
                    "byte_size": len(wheel_raw),
                    "retained_path": wheel_path.name,
                },
                "sdist": {
                    "name": sdist_path.name,
                    "sha256": sdist_sha,
                    "byte_size": len(sdist_raw),
                    "retained_path": sdist_path.name,
                },
            },
    }
    for field in ("sbom", "openvex", "licenses", "provenance"):
        pre_publish[field] = {
            "format": "synthetic",
            "sha256": aux[field]["sha256"],
            "path": aux[field]["relative_path"],
            "verified": True,
        }
    pre_publish["record_sha256"] = ""
    pre_publish["record_sha256"] = _sha(
        _canonical({key: item for key, item in pre_publish.items() if key != "record_sha256"})
    )
    pre_source = _json_file(tmp_path, "pre-publish.json", pre_publish)
    candidate_build_source = aux["provenance"]
    path = _envelope(
        tmp_path,
        kind="retained_supply_chain",
        payload={
            "candidate_build_source": candidate_build_source,
            "retained_candidate_source": retained_source,
            "pre_publish_receipt_source": pre_source,
            "wheel_source": _bytes_file(
                tmp_path,
                wheel_path.name,
                wheel_raw,
                media_type="application/octet-stream",
            ),
            "sdist_source": _bytes_file(
                tmp_path,
                sdist_path.name,
                sdist_raw,
                media_type="application/octet-stream",
            ),
            "sbom_source": aux["sbom"],
            "openvex_source": aux["openvex"],
            "licenses_source": aux["licenses"],
            "provenance_source": aux["provenance"],
        },
        candidate_binding=candidate_binding,
    )
    result = parse_typed_evidence(path)
    assert result["status"] == "passed"
    assert result["metrics"]["public_redownload_verified"] is False

    wrong_build = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    wrong_build["artifacts"][0]["sha256"] = "f" * 64
    wrong_build["record_sha256"] = _sha(
        _canonical({key: item for key, item in wrong_build.items() if key != "record_sha256"})
    )
    wrong_build_ref = _json_file(tmp_path, "provenance-bad.json", wrong_build)
    broken_build_envelope = json.loads(path.read_text(encoding="utf-8"))
    broken_build_envelope["payload"]["candidate_build_source"] = wrong_build_ref
    broken_build_envelope["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in broken_build_envelope.items()
                if key != "record_sha256"
            }
        )
    )
    path.write_bytes(_canonical(broken_build_envelope))
    _assert_reject(path)
    wrong_build_ref_path = tmp_path / "provenance-bad.json"
    wrong_build_ref_path.unlink()
    restored_envelope = json.loads(path.read_text(encoding="utf-8"))
    restored_envelope["payload"]["candidate_build_source"] = candidate_build_source
    restored_envelope["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in restored_envelope.items()
                if key != "record_sha256"
            }
        )
    )
    path.write_bytes(_canonical(restored_envelope))

    bad_sbom = _json_file(tmp_path, "sbom.json", {})
    broken_pre = json.loads(
        (tmp_path / "pre-publish.json").read_text(encoding="utf-8")
    )
    broken_pre["sbom"]["sha256"] = bad_sbom["sha256"]
    broken_pre["record_sha256"] = _sha(
        _canonical(
            {key: item for key, item in broken_pre.items() if key != "record_sha256"}
        )
    )
    (tmp_path / "pre-publish.json").write_bytes(_canonical(broken_pre))
    broken_envelope = json.loads(path.read_text(encoding="utf-8"))
    broken_envelope["payload"]["sbom_source"] = bad_sbom
    broken_envelope["payload"]["pre_publish_receipt_source"].update(
        {
            "byte_size": (tmp_path / "pre-publish.json").stat().st_size,
            "sha256": _sha((tmp_path / "pre-publish.json").read_bytes()),
        }
    )
    broken_envelope["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in broken_envelope.items()
                if key != "record_sha256"
            }
        )
    )
    path.write_bytes(_canonical(broken_envelope))
    _assert_reject(path)


def _semantic_gold() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.semantic-human-gold/v3",
        "status": "semantic_human_gold_frozen",
        "frozen_at": "2026-01-01T00:00:00Z",
        "gold_id": "semanticgold_" + "a" * 24,
        "model_outputs_seen_before_freeze": False,
        "candidate_visible_when_frozen": False,
        "claim_eligible": False,
        "author": {"identity": "synthetic-human-author", "role": "human_author"},
        "human_approval": {
            "attestation_type": "external_human_attestation",
            "attestation_identity": "synthetic-human-approver",
            "attestation_digest": "9" * 64,
            "approval_record": {
                "record_id": "synthetic-approval-record",
                "record_sha256": "8" * 64,
                "issuer": "synthetic-issuer",
            },
            "approved_at": "2026-01-01T00:00:00Z",
            "decision": "approved",
        },
        "labels": [{"label_id": "l1", "description": "synthetic label"}],
        "cases": [
            {
                "case_id": f"goldcase_{index:03d}",
                "labels": ["l1"],
                "expected": {"include": ["l1"], "exclude": []},
                "duties": ["d1"],
                "hard_failures": ["h1"],
                "thresholds": {
                    "minimum_case_pass_rate": 1,
                    "minimum_duty_coverage": 1,
                    "maximum_hard_failures": 0,
                    "maximum_false_authority": 0,
                },
            }
            for index in range(1, 4)
        ],
        "duties": [{"duty_id": "d1", "description": "synthetic duty"}],
        "hard_failures": [{"code": "h1", "description": "synthetic hard failure"}],
        "thresholds": {
            "minimum_case_pass_rate": 1,
            "minimum_duty_coverage": 1,
            "maximum_hard_failures": 0,
            "maximum_false_authority": 0,
        },
    }


def _gold_binding(gold_raw: bytes) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "deeplaw.candidate-gold-binding-receipt/v1",
        "status": "post_build_candidate_gold_bound",
        "bound_at": "2026-01-02T00:00:00Z",
        "semantic_gold": {
            "gold_id": "semanticgold_" + "a" * 24,
            "schema_version": "deeplaw.semantic-human-gold/v3",
            "sha256": _sha(gold_raw),
        },
        "candidate": {"commit": COMMIT, "tree": TREE, "lock_sha256": LOCK},
        "artifacts": {
            "wheel": {"name": "deeplaw-0.13.0-py3-none-any.whl", "sha256": WHEEL, "byte_size": 1},
            "sdist": {"name": "deeplaw-0.13.0.tar.gz", "sha256": SDIST, "byte_size": 1},
        },
        "holdout": {"role": "qualification_holdout", "sha256": "6" * 64},
        "blind": {"role": "final_blind", "sha256": "7" * 64},
        "scorer": SCORER,
        "runner": RUNNER,
        "record_sha256": "",
    }
    value["record_sha256"] = _sha(
        _canonical({key: item for key, item in value.items() if key != "record_sha256"})
    )
    return value


def _attestation(
    gold_raw: bytes,
    private_key: Ed25519PrivateKey,
    *,
    key_id: str | None = None,
) -> dict[str, Any]:
    # This is a synthetic contract fixture only; it is not a human attestation.
    public_key = private_key.public_key().public_bytes_raw()
    key_id = key_id or _sha(public_key)
    value: dict[str, Any] = {
        "schema_version": "deeplaw.external-human-attestation/v1",
        "attestation_identity": "synthetic-human-approver",
        "attestation_digest": "9" * 64,
        "approval_record": {
            "record_id": "synthetic-approval-record",
            "record_sha256": "8" * 64,
            "issuer": "synthetic-issuer",
        },
        "approved_at": "2026-01-01T00:00:00Z",
        "decision": "approved",
        "semantic_gold_sha256": _sha(gold_raw),
        "signing_key_id": key_id,
        "signature_algorithm": "Ed25519",
        "signature_b64": "",
        "signature_payload_sha256": "",
        "record_sha256": "",
    }
    signature_payload = {
        "schema_version": "deeplaw.external-human-attestation/v1",
        "semantic_gold_sha256": value["semantic_gold_sha256"],
        "attestation_identity": value["attestation_identity"],
        "approved_at": value["approved_at"],
        "approval_record": value["approval_record"],
    }
    signature_payload_bytes = _canonical(signature_payload)
    value["signature_payload_sha256"] = _sha(signature_payload_bytes)
    value["signature_b64"] = base64.b64encode(
        private_key.sign(signature_payload_bytes)
    ).decode("ascii")
    value["record_sha256"] = _sha(
        _canonical({key: item for key, item in value.items() if key != "record_sha256"})
    )
    return value


def test_human_gold_requires_external_attestation_and_crosses_processes(
    tmp_path: Path,
) -> None:
    gold = _semantic_gold()
    gold_source = _json_file(tmp_path, "gold.json", gold)
    gold_raw = (tmp_path / "gold.json").read_bytes()
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode("ascii")
    binding_source = _json_file(tmp_path, "binding.json", _gold_binding(gold_raw))
    attestation_source = _json_file(
        tmp_path,
        "external-human-attestation.synthetic.json",
        _attestation(gold_raw, private_key),
    )
    rows_source = _json_file(
        tmp_path,
        "gold-rows.json",
        {
            "receipt": _receipt(
                corpus_sha256="6" * 64,
                corpus_role="qualification_holdout",
            ),
            "rows": [
                {
                    "case_id": f"goldcase_{index:03d}",
                    "expected": {"include": ["l1"], "exclude": []},
                    "observed": {"include": ["l1"], "exclude": []},
                    "duties": ["d1"],
                    "hard_failures": [],
                    "scorer_process_id": "scorer-process",
                    "runner_process_id": "runner-process",
                    "false_authority": False,
                }
                for index in range(1, 4)
            ]
        },
    )
    path = _envelope(
        tmp_path,
        kind="human_gold_scorer",
        payload={
            "semantic_gold_source": gold_source,
            "candidate_binding_source": binding_source,
            "scorer_rows_source": rows_source,
            "human_attestation_source": attestation_source,
            "process_identity": {
                "scorer_process_id": "scorer-process",
                "runner_process_id": "runner-process",
                "scorer_identity_sha256": SCORER["sha256"],
                "runner_identity_sha256": RUNNER["sha256"],
                "separate_processes": True,
            },
        },
        corpus_sha256="6" * 64,
        corpus_role="qualification_holdout",
    )
    result = parse_typed_evidence(
        path,
        trusted_human_approver={
            "identity": "synthetic-human-approver",
            "key_id": _sha(private_key.public_key().public_bytes_raw()),
            "public_key_b64": public_key_b64,
        },
    )
    assert result["metrics"]["human_attested"] is True
    assert result["metrics"]["attestation_receipt_bound"] is True
    assert result["metrics"]["case_pass_rate"] == 1
    assert result["metrics"]["false_authority_count"] == 0
    assert result["status"] == "passed"

    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(path)
    wrong_key = Ed25519PrivateKey.generate()
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            path,
            trusted_human_approver={
                "identity": "synthetic-human-approver",
                "key_id": _sha(wrong_key.public_key().public_bytes_raw()),
                "public_key_b64": base64.b64encode(
                    wrong_key.public_key().public_bytes_raw()
                ).decode("ascii"),
            },
        )
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            path,
            trusted_human_approver={
                "identity": "synthetic-human-approver",
                "key_id": "0" * 64,
                "public_key_b64": public_key_b64,
            },
        )

    tampered_attestation = _attestation(gold_raw, private_key)
    tampered_attestation["record_sha256"] = "0" * 64
    tampered_source = _json_file(
        tmp_path,
        "external-human-attestation-tampered.synthetic.json",
        tampered_attestation,
    )
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["payload"]["human_attestation_source"] = tampered_source
    tampered["record_sha256"] = _sha(
        _canonical({key: item for key, item in tampered.items() if key != "record_sha256"})
    )
    tampered_path = tmp_path / "gold-tampered-attestation.json"
    tampered_path.write_bytes(_canonical(tampered))
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            tampered_path,
            trusted_human_approver={
                "identity": "synthetic-human-approver",
                "key_id": _sha(private_key.public_key().public_bytes_raw()),
                "public_key_b64": public_key_b64,
            },
        )

    missing = json.loads(path.read_text(encoding="utf-8"))
    missing["payload"].pop("human_attestation_source")
    missing["record_sha256"] = _sha(
        _canonical({key: item for key, item in missing.items() if key != "record_sha256"})
    )
    missing_path = tmp_path / "gold-missing-attestation.json"
    missing_path.write_bytes(_canonical(missing))
    _assert_reject(missing_path)


def test_legal_rows_require_28_exact_sources_and_recompute_wrong_version(
    tmp_path: Path,
) -> None:
    original_refs: list[dict[str, Any]] = []
    sources = [
        {
            "source_id": f"source-{index:02d}",
            "version_id": f"version-{index:02d}",
            "document_sha256": "",
            "document_byte_size": 0,
            "media_type": "text/markdown",
            "authority": "official",
            "effective_date": "2026-01-01",
        }
        for index in range(28)
    ]
    for index, source in enumerate(sources):
        raw = f"synthetic legal source {index}\n".encode()
        source_file = _bytes_file(
            tmp_path,
            f"source-{index:02d}.md",
            raw,
            media_type="text/markdown",
        )
        source["document_sha256"] = source_file["sha256"]
        source["document_byte_size"] = source_file["byte_size"]
        original_refs.append(
            {
                "source_id": source["source_id"],
                "version_id": source["version_id"],
                "source": source_file,
            }
        )
    catalog_source = _json_file(tmp_path, "legal-catalog.json", {"sources": sources})
    expected_source = _json_file(
        tmp_path,
        "legal-expected.json",
        {
            "rows": [
                {
                    "source_id": source["source_id"],
                    "version_id": source["version_id"],
                    "fragment_id": f"fragment-{index:02d}",
                    "expected": _legal_raw_evidence(source, index),
                }
                for index, source in enumerate(sources)
            ]
        },
    )
    observed_source = _json_file(
        tmp_path,
        "legal-observed.json",
        {
            "receipt": _receipt(corpus_sha256=expected_source["sha256"]),
            "rows": [
                {
                    "source_id": source["source_id"],
                    "version_id": source["version_id"],
                    "fragment_id": f"fragment-{index:02d}",
                    "observed": _legal_raw_evidence(source, index),
                }
                for index, source in enumerate(sources)
            ]
        },
    )
    path = _envelope(
        tmp_path,
        kind="legal_rows",
        payload={
            "source_catalog_source": catalog_source,
            "original_source_refs": original_refs,
            "expected_source": expected_source,
            "observed_source": observed_source,
        },
    )
    expected_hash = expected_source["sha256"]
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["corpus"]["sha256"] = expected_hash
    envelope["record_sha256"] = _sha(
        _canonical({key: item for key, item in envelope.items() if key != "record_sha256"})
    )
    path.write_bytes(_canonical(envelope))
    result = parse_typed_evidence(path, expected_corpus_sha256=expected_hash)
    assert result["status"] == "passed"
    assert result["metrics"]["exact_source_count"] == 28

    catalog_original_raw = (tmp_path / "legal-catalog.json").read_bytes()
    catalog_bad = json.loads(catalog_original_raw)
    catalog_bad["sources"][0]["authority"] = ""
    catalog_bad_raw = _canonical(catalog_bad)
    (tmp_path / "legal-catalog.json").write_bytes(catalog_bad_raw)
    bad_catalog_envelope = json.loads(path.read_text(encoding="utf-8"))
    bad_catalog_envelope["payload"]["source_catalog_source"].update(
        {"byte_size": len(catalog_bad_raw), "sha256": _sha(catalog_bad_raw)}
    )
    bad_catalog_envelope["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in bad_catalog_envelope.items()
                if key != "record_sha256"
            }
        )
    )
    path.write_bytes(_canonical(bad_catalog_envelope))
    _assert_reject(path)
    (tmp_path / "legal-catalog.json").write_bytes(catalog_original_raw)
    restored_catalog_envelope = json.loads(path.read_text(encoding="utf-8"))
    restored_catalog_envelope["payload"]["source_catalog_source"] = catalog_source
    restored_catalog_envelope["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in restored_catalog_envelope.items()
                if key != "record_sha256"
            }
        )
    )
    path.write_bytes(_canonical(restored_catalog_envelope))

    (tmp_path / "source-00.md").write_text("tampered original bytes\n", encoding="utf-8")
    _assert_reject(path)


def test_wiki_context_and_scale_recompute_rows_without_caller_counts(
    tmp_path: Path,
) -> None:
    wiki_root = tmp_path / "wiki"
    context_root = tmp_path / "context"
    scale_root = tmp_path / "scale"
    wiki_root.mkdir()
    context_root.mkdir()
    scale_root.mkdir()
    def _journey_snapshot(
        identity: str,
        revision: str,
        aliases: list[str],
        relations: list[dict[str, str]],
        file_protected: bool = False,
    ) -> dict[str, Any]:
        return {
            "identity": identity,
            "revision_sha256": revision,
            "exists": True,
            "aliases": aliases,
            "relations": relations,
            "file_protected": file_protected,
        }

    journey_rows: list[dict[str, Any]] = []
    for operation in (
        "alias",
        "same_name_entity",
        "rename",
        "move",
        "edit",
        "reconcile",
        "backlink",
        "outlink",
        "source_successor",
        "wrong_merge",
        "user_file_protection",
        "full_incremental_equivalence",
    ):
        before = _journey_snapshot("wiki:one", "a" * 64, ["One"], [])
        after = _journey_snapshot("wiki:one", "a" * 64, ["One"], [])
        if operation == "alias":
            after["aliases"] = ["One", "Alias"]
        elif operation == "same_name_entity":
            before["aliases"] = ["Shared"]
            after = _journey_snapshot("wiki:two", "b" * 64, ["Shared"], [])
        elif operation == "edit":
            after["revision_sha256"] = "b" * 64
        elif operation in {"backlink", "outlink", "source_successor"}:
            after["relations"] = [
                {
                    "predicate": operation,
                    "target_identity": "wiki:target",
                    "direction": "out",
                }
            ]
        elif operation == "wrong_merge":
            after = _journey_snapshot("wiki:two", "b" * 64, ["Two"], [])
        elif operation == "user_file_protection":
            after["file_protected"] = True
        journey_rows.append(
            {
                "journey_id": f"{operation}-1",
                "operation": operation,
                "before": before,
                "after": after,
                "expected": after,
            }
        )
    wiki_expected = _json_file(
        wiki_root,
        "wiki-expected.json",
        {"rows": journey_rows},
    )
    wiki_observed = _json_file(
        wiki_root,
        "wiki-observed.json",
        {
            "receipt": _receipt(corpus_sha256=wiki_expected["sha256"]),
            "rows": [
                {
                    "journey_id": row["journey_id"],
                    "operation": row["operation"],
                    "before": row["before"],
                    "after": row["after"],
                    "observed": row["expected"],
                }
                for row in journey_rows
            ]
        },
    )
    wiki_path = _envelope(
        wiki_root,
        kind="wiki_journey_rows",
        payload={"expected_source": wiki_expected, "observed_source": wiki_observed},
        name="wiki-envelope.json",
    )
    expected_hash = wiki_expected["sha256"]
    wiki_envelope = json.loads(wiki_path.read_text(encoding="utf-8"))
    wiki_envelope["corpus"]["sha256"] = expected_hash
    wiki_envelope["record_sha256"] = _sha(
        _canonical({key: item for key, item in wiki_envelope.items() if key != "record_sha256"})
    )
    wiki_path.write_bytes(_canonical(wiki_envelope))
    assert parse_typed_evidence(
        wiki_path,
        expected_corpus_sha256=expected_hash,
    )["status"] == "passed"
    wiki_bad_observed = json.loads(
        (wiki_root / "wiki-observed.json").read_text(encoding="utf-8")
    )
    wiki_bad_observed["rows"][0]["after"]["revision_sha256"] = "b" * 64
    wiki_bad_raw = _canonical(wiki_bad_observed)
    (wiki_root / "wiki-observed.json").write_bytes(wiki_bad_raw)
    wiki_envelope = json.loads(wiki_path.read_text(encoding="utf-8"))
    wiki_envelope["payload"]["observed_source"].update(
        {"byte_size": len(wiki_bad_raw), "sha256": _sha(wiki_bad_raw)}
    )
    wiki_envelope["record_sha256"] = _sha(
        _canonical({key: item for key, item in wiki_envelope.items() if key != "record_sha256"})
    )
    wiki_path.write_bytes(_canonical(wiki_envelope))
    wiki_mismatch = parse_typed_evidence(
        wiki_path,
        expected_corpus_sha256=expected_hash,
    )
    assert wiki_mismatch["status"] == "failed"
    assert wiki_mismatch["hard_failure_counts"]["wiki_journey_mismatch"] == 1

    item = {
        "identity": "knowledge:one",
        "revision_sha256": "b" * 64,
        "selection": "include",
        "version_id": "revision-1",
        "authority": "official",
        "duties": ["duty-1"],
        "gap_codes": [],
    }
    context_expected = _json_file(
        context_root,
        "context-expected.json",
        {
            "expected_include": [
                {
                    "identity": "knowledge:one",
                    "revision_sha256": "b" * 64,
                    "version_id": "revision-1",
                    "authority": "official",
                }
            ],
            "expected_exclude": [],
            "required_duties": ["duty-1"],
            "acceptable_gap": {"allowed": True, "codes": []},
            "hard_failures": {"wrong_version": True, "false_authority": True},
            "projection": "normal",
            "projection_budget": {
                "continuity_max_bytes": 2048,
                "normal_max_bytes": 8192,
                "legal_source_first_max_bytes": 16384,
                "tools_list_max_bytes": 8192,
                "global_max_bytes": 65536,
            },
        },
    )
    context_sources = {
        "provider_capsule_source": _json_file(
            context_root,
            "provider.json",
            {"receipt": _receipt(corpus_sha256=context_expected["sha256"]), "items": [item]},
        ),
        "query_trace_source": _json_file(
            context_root,
            "query.json",
            {"receipt": _receipt(corpus_sha256=context_expected["sha256"]), "items": [item]},
        ),
        "ledger_source": _json_file(
            context_root,
            "ledger.json",
            {"receipt": _receipt(corpus_sha256=context_expected["sha256"]), "items": [item]},
        ),
        "usage_source": _json_file(
            context_root,
            "usage-context.json",
            {
                "receipt": _receipt(corpus_sha256=context_expected["sha256"]),
                "rows": [
                    {
                        "run_id": "run-v013-synthetic",
                        "candidate_commit": COMMIT,
                        "candidate_tree": TREE,
                        "corpus_sha256": context_expected["sha256"],
                        "runner_identity": RUNNER["identity"],
                        "runner_sha256": RUNNER["sha256"],
                        "input_tokens": 1,
                        "output_tokens": 2,
                        "cache_tokens": 3,
                        "reasoning_tokens": 4,
                        "tools_list_bytes": 5,
                        "provider_bytes": 5,
                        "relevant_chars": 6,
                        "context_chars": 7,
                        "evidence_identities": ["evidence-1", "evidence-1"],
                        "distractor_answer_delta": 0.0,
                    }
                ]
            },
        ),
    }
    context_path = _envelope(
        context_root,
        kind="context_capsule_selection_usage",
        payload={"expected_source": context_expected, **context_sources},
        name="context-envelope.json",
    )
    context_envelope = json.loads(context_path.read_text(encoding="utf-8"))
    context_envelope["corpus"]["sha256"] = context_expected["sha256"]
    context_envelope["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in context_envelope.items()
                if key != "record_sha256"
            }
        )
    )
    context_path.write_bytes(_canonical(context_envelope))
    context_result = parse_typed_evidence(
        context_path,
        expected_corpus_sha256=context_expected["sha256"],
    )
    assert context_result["metrics"]["usage"]["input_tokens"] == 1
    assert context_result["metrics"]["relevant_chars"] == 6
    assert context_result["metrics"]["context_chars"] == 7
    assert context_result["metrics"]["duplicate_evidence"] == 1
    assert context_result["metrics"]["redundancy"] == pytest.approx(1 - 6 / 7)
    provider_original_raw = (context_root / "provider.json").read_bytes()
    provider_bad = json.loads(provider_original_raw)
    provider_bad["receipt"]["candidate"]["tree"] = "9" * 40
    provider_bad_raw = _canonical(provider_bad)
    (context_root / "provider.json").write_bytes(provider_bad_raw)
    context_bad_envelope = json.loads(context_path.read_text(encoding="utf-8"))
    context_bad_envelope["payload"]["provider_capsule_source"].update(
        {"byte_size": len(provider_bad_raw), "sha256": _sha(provider_bad_raw)}
    )
    context_bad_envelope["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in context_bad_envelope.items()
                if key != "record_sha256"
            }
        )
    )
    context_path.write_bytes(_canonical(context_bad_envelope))
    _assert_reject(context_path)
    (context_root / "provider.json").write_bytes(provider_original_raw)
    restored_context = json.loads(context_path.read_text(encoding="utf-8"))
    restored_context["payload"]["provider_capsule_source"] = context_sources[
        "provider_capsule_source"
    ]
    restored_context["record_sha256"] = _sha(
        _canonical(
            {
                key: item for key, item in restored_context.items() if key != "record_sha256"
            }
        )
    )
    context_path.write_bytes(_canonical(restored_context))
    context_bad_usage = json.loads(
        (context_root / "usage-context.json").read_text(encoding="utf-8")
    )
    context_bad_usage["rows"][0]["duty_covered"] = True
    context_bad_raw = _canonical(context_bad_usage)
    (context_root / "usage-context.json").write_bytes(context_bad_raw)
    context_envelope = json.loads(context_path.read_text(encoding="utf-8"))
    context_envelope["payload"]["usage_source"].update(
        {"byte_size": len(context_bad_raw), "sha256": _sha(context_bad_raw)}
    )
    context_envelope["record_sha256"] = _sha(
        _canonical(
            {key: item for key, item in context_envelope.items() if key != "record_sha256"}
        )
    )
    context_path.write_bytes(_canonical(context_envelope))
    _assert_reject(context_path)

    scale_source = _json_file(
        scale_root,
        "scale-expected.json",
        {
            "rows": [
                {
                    "sample_size": size,
                    "expected_cases": [
                        {"case_id": f"scale-case-{size}", "expected": True}
                    ],
                    "thresholds": {
                        "max_latency_ms": 100,
                        "max_rss_bytes": 100,
                        "max_storage_bytes": 100,
                        "min_throughput_per_sec": 1,
                    },
                }
                for size in (1000, 10000, 100000)
            ]
        },
    )
    scale_observed = _json_file(
        scale_root,
        "scale-observed.json",
        {
            "receipt": _receipt(corpus_sha256=scale_source["sha256"]),
            "rows": [
                {
                    "sample_size": size,
                    "latency_ms": 1,
                    "rss_bytes": 2,
                    "storage_bytes": 3,
                    "throughput_per_sec": 4,
                    "observed_cases": [
                        {"case_id": f"scale-case-{size}", "observed": True}
                    ],
                    "command": "synthetic-scale-command",
                    "execution_id": f"synthetic-scale-{size}",
                    "exit_code": 0,
                }
                for size in (1000, 10000, 100000)
            ]
        },
    )
    scale_path = _envelope(
        scale_root,
        kind="scale_report",
        payload={"expected_source": scale_source, "observed_source": scale_observed},
        name="scale-envelope.json",
    )
    expected_hash = scale_source["sha256"]
    scale_envelope = json.loads(scale_path.read_text(encoding="utf-8"))
    scale_envelope["corpus"]["sha256"] = expected_hash
    scale_envelope["record_sha256"] = _sha(
        _canonical({key: item for key, item in scale_envelope.items() if key != "record_sha256"})
    )
    scale_path.write_bytes(_canonical(scale_envelope))
    assert parse_typed_evidence(
        scale_path,
        expected_corpus_sha256=expected_hash,
    )["status"] == "passed"
    observed_value = json.loads((scale_root / "scale-observed.json").read_text(encoding="utf-8"))
    observed_value["rows"][0]["latency_ms"] = 101
    observed_raw = _canonical(observed_value)
    (scale_root / "scale-observed.json").write_bytes(observed_raw)
    scale_envelope = json.loads(scale_path.read_text(encoding="utf-8"))
    scale_envelope["payload"]["observed_source"].update(
        {"byte_size": len(observed_raw), "sha256": _sha(observed_raw)}
    )
    scale_envelope["record_sha256"] = _sha(
        _canonical(
            {
                key: item
                for key, item in scale_envelope.items()
                if key != "record_sha256"
            }
        )
    )
    scale_path.write_bytes(_canonical(scale_envelope))
    over_threshold = parse_typed_evidence(
        scale_path,
        expected_corpus_sha256=expected_hash,
    )
    assert over_threshold["status"] == "failed"
    assert over_threshold["hard_failure_counts"]["scale_latency_exceeded"] == 1


def test_path_policy_accepts_urls_and_locators_but_rejects_local_paths(
    tmp_path: Path,
) -> None:
    raw = (
        b"<testsuites><testsuite>"
        b'<testcase classname="tests.test_knowledge_control" '
        b'name="test_interrupted_migration_rolls_back_and_retains_a_verified_backup"/>'
        b'<testcase classname="tests.test_v013_pass22_continuity_closure" '
        b'name="test_partial_checkpoint_recovers_after_process_exit_and_restart"/>'
        b"<system-out>https://example.test/source page:/1</system-out>"
        b"</testsuite></testsuites>"
    )
    source = _bytes_file(tmp_path, "path-policy.xml", raw, media_type="application/xml")
    path = _envelope(tmp_path, kind="candidate_full_junit", payload={"source": source})
    assert parse_typed_evidence(path)["status"] == "passed"

    bad_raw = raw.replace(b"page:/1", b"/root/private/secret.txt")
    (tmp_path / "path-policy.xml").write_bytes(bad_raw)
    bad_envelope = json.loads(path.read_text(encoding="utf-8"))
    bad_envelope["payload"]["source"].update(
        {"byte_size": len(bad_raw), "sha256": _sha(bad_raw)}
    )
    bad_envelope["record_sha256"] = _sha(
        _canonical(
            {key: item for key, item in bad_envelope.items() if key != "record_sha256"}
        )
    )
    path.write_bytes(_canonical(bad_envelope))
    _assert_reject(path)


def test_typed_evidence_cli_requires_all_external_bindings(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    descriptor_root = tmp_path / "descriptors"
    evidence_root.mkdir()
    descriptor_root.mkdir()
    junit_source = _bytes_file(
        evidence_root,
        "candidate.xml",
        b'<testsuites><testcase classname="fixture" name="pass" /></testsuites>',
        media_type="application/xml",
    )
    manifest = _envelope(
        evidence_root,
        kind="candidate_full_junit",
        payload={"source": junit_source},
    )
    candidate_path = descriptor_root / "candidate.json"
    candidate_path.write_bytes(
        _canonical(
            {
                "commit": COMMIT,
                "tree": TREE,
                "lock_sha256": LOCK,
                "wheel_sha256": WHEEL,
                "sdist_sha256": SDIST,
            }
        )
    )
    run_path = descriptor_root / "run.json"
    run_path.write_bytes(_canonical({"run_id": "wrong-run", "workflow_run_id": 1}))
    corpus_path = descriptor_root / "corpus.json"
    corpus_path.write_bytes(_canonical({"sha256": CORPUS, "role": "candidate_full"}))
    runner_path = descriptor_root / "runner.json"
    runner_path.write_bytes(_canonical(RUNNER))
    scorer_path = descriptor_root / "scorer.json"
    scorer_path.write_bytes(_canonical(SCORER))
    command = [
        sys.executable,
        "-m",
        "benchmarks.release.typed_qualification_evidence",
        "--manifest",
        str(manifest),
        "--root",
        str(evidence_root),
        "--candidate",
        str(candidate_path),
        "--run",
        str(run_path),
        "--corpus",
        str(corpus_path),
        "--runner",
        str(runner_path),
        "--scorer",
        str(scorer_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert "rejected" in completed.stderr
    missing = subprocess.run(
        [argument for argument in command if argument not in {"--candidate", str(candidate_path)}],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 2

"""Focused integrity tests for the exact-candidate Kernel Gate v9 bundle."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from benchmarks.hosts import host_process_receipt_v2
from benchmarks.release import kernel_qualification_bundle_v1 as bundle

RUN_IDS = {
    "candidate_run_id": 101,
    "evidence_run_id": 202,
    "qualification_run_id": 303,
}
EXPECTED_CANDIDATE = {
    "commit": "1" * 40,
    "tree": "2" * 40,
    "lock_sha256": "e2cacd96e66132fcb28f1b9bf4746709ad2696159ffb8498ddf0769c213a7082",
    "wheel_sha256": "3" * 64,
    "sdist_sha256": "4" * 64,
    "version": "0.13.0",
}
BROKER_SOURCE_BYTES = {
    "codex": (
        b"#!/bin/sh\n"
        b'exec /Applications/ChatGPT.app/Contents/Resources/codex "$@"\n'
    ),
    "opencode": b'#!/bin/sh\nexec /usr/local/bin/opencode "$@"\n',
}
BROKER_SOURCE_SHA256 = {
    host: hashlib.sha256(raw).hexdigest()
    for host, raw in BROKER_SOURCE_BYTES.items()
}
HOST_BINARY = {
    "codex": (
        "codex-cli 0.148.0-alpha.15",
        "7645c3caf5607e4528eb3a15b12496c284c2a918939aed34e863c760c1b421e7",
    ),
    "opencode": (
        "1.18.16",
        "a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f",
    ),
}
HOST_IDENTITY = {
    "schema_version": "deeplaw.host-exact-identity/v1",
    "hosts": {
        "codex": {
            "binary_version": HOST_BINARY["codex"][0],
            "binary_sha256": HOST_BINARY["codex"][1],
            "request_model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "auth_status_command": "codex login status",
            "auth_material_access": "forbidden",
        },
        "opencode": {
            "version": HOST_BINARY["opencode"][0],
            "source_commit": "a3647eb025c7615159d417dcc49fc39fdaeba65b",
            "config_selector": "deepseek/deepseek-v4-flash",
            "expected_response_model_id": "deepseek-v4-flash",
            "executable_sha256": HOST_BINARY["opencode"][1],
            "package_sha256": "d40af2479740f8ad3a32b700e9a907794ba4314c926d0e805c20fe39751d8722",
            "runtime": "host_bun_runtime_only",
            "dotenv_policy": "owner_only_external_strict_parser",
            "secret_visibility": "forbidden",
        },
    },
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bundle.canonical_json(value) + b"\n")


def _copy_bindings(root: Path) -> None:
    for relative in (
        bundle.ACTIVE_RELATIVE_PATH,
        bundle.CLASSIFICATION_RELATIVE_PATH,
        bundle.PROTOCOL_RELATIVE_PATH,
    ):
        source = bundle.REPOSITORY / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative == bundle.ACTIVE_RELATIVE_PATH:
            active = json.loads(source.read_text(encoding="utf-8"))
            active["status"] = "frozen_exact_candidate_machine_evaluation_pending"
            active["candidate_version"] = "0.13.0"
            active["blocker"] = None
            active["candidate_binding"] = {
                "package_version": "0.13.0",
                "source_commit": EXPECTED_CANDIDATE["commit"],
                "source_tree": EXPECTED_CANDIDATE["tree"],
                "lock_sha256": EXPECTED_CANDIDATE["lock_sha256"],
                "wheel_filename": "deeplaw-0.13.0-py3-none-any.whl",
                "wheel_sha256": EXPECTED_CANDIDATE["wheel_sha256"],
                "sdist_filename": "deeplaw-0.13.0.tar.gz",
                "sdist_sha256": EXPECTED_CANDIDATE["sdist_sha256"],
                "artifact_manifest_sha256": "5" * 64,
            }
            _write_json(target, active)
        else:
            shutil.copyfile(source, target)


def _typed_manifest(
    kind: str, role: str, corpus_sha256: str, *, workflow_id: int
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": bundle.TYPED_SCHEMA_VERSION,
        "profile": "kernel_release_core",
        "reference_provenance": "deterministic_expected_evidence",
        "human_authenticity": "not_claimed",
        "kind": kind,
        "candidate_binding": {
            key: EXPECTED_CANDIDATE[key]
            for key in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256")
        },
        "run_binding": {
            "run_id": f"{kind}-run",
            "workflow_run_id": workflow_id,
        },
        "corpus": {"sha256": corpus_sha256, "role": role},
        "runner": {"identity": "fixture-runner", "sha256": "a" * 64},
        "scorer": {"identity": "fixture-scorer", "sha256": "b" * 64},
        "payload": {},
    }
    value["record_sha256"] = bundle.record_sha256(value)
    return value


def _preflight(host: str) -> dict[str, Any]:
    host_version, host_sha256 = HOST_BINARY[host]
    broker_sha256 = BROKER_SOURCE_SHA256[host]
    return {
        "schema_version": "deeplaw.host-preflight-receipt/v1",
        "host": {"name": host, "version": host_version, "sha256": host_sha256},
        "broker_source": {
            "source_kind": "repository_external_launcher",
            "repository_external": True,
            "sha256": broker_sha256,
            "bytes": len(BROKER_SOURCE_BYTES[host]),
            "owner_only_mode": True,
            "expected_sha256": broker_sha256,
        },
        "status": "passed",
        "stage": "complete",
        "reason_code": "preflight_passed",
        "observed": {"check_count": 1, "elapsed_ms": 1},
    }


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _native_event_binding(host: str, task_case: str, index: int) -> dict[str, str]:
    prefix = f"{host}:{task_case}:{index}"
    return {
        "event_sequence_sha256": _digest(f"{prefix}:event-sequence"),
        "session_identity_sha256": _digest(f"{prefix}:session-identity"),
        "lifecycle_record_sha256": _digest(f"{prefix}:lifecycle-record"),
    }


def _process_receipt(host: str, task_case: str, index: int) -> dict[str, Any]:
    process_identity = _digest(f"{host}:{task_case}:{index}:process")
    native_binding = _native_event_binding(host, task_case, index)
    if host == "codex":
        proof = {
            "proof_kind": "codex_stdio_hook_correlation",
            "process_identity_sha256": process_identity,
            "connection_sha256": _digest(f"{host}:{task_case}:{index}:connection"),
            "initialize_request_sha256": _digest(
                f"{host}:{task_case}:{index}:initialize-request"
            ),
            "initialized_notification_sha256": _digest(
                f"{host}:{task_case}:{index}:initialized-notification"
            ),
            "initialized_connection_count": 1,
            "hook_session_sha256": _digest(f"{host}:{task_case}:{index}:hook-session"),
            "hook_event_sha256": _digest(f"{host}:{task_case}:{index}:hook-event"),
            "native_event_sequence_sha256": native_binding["event_sequence_sha256"],
            "native_session_identity_sha256": native_binding[
                "session_identity_sha256"
            ],
            "native_lifecycle_record_sha256": native_binding[
                "lifecycle_record_sha256"
            ],
            "same_process": True,
            "same_connection": True,
        }
        proof["connection_correlation_sha256"] = (
            host_process_receipt_v2.correlation_sha256(
                {
                    key: proof[key]
                    for key in (
                        "process_identity_sha256",
                        "connection_sha256",
                        "initialize_request_sha256",
                        "initialized_notification_sha256",
                        "initialized_connection_count",
                        "hook_session_sha256",
                        "hook_event_sha256",
                        "native_event_sequence_sha256",
                        "native_session_identity_sha256",
                        "native_lifecycle_record_sha256",
                    )
                }
            )
        )
    else:
        child_session = _digest(f"{host}:{task_case}:{index}:child-session")
        proof = {
            "proof_kind": "opencode_public_fork_route_correlation",
            "process_identity_sha256": process_identity,
            "request_method": "POST",
            "route_observation_sha256": _digest(
                f"{host}:{task_case}:{index}:actual-route"
            ),
            "request_body_sha256": _digest(f"{host}:{task_case}:{index}:request-body"),
            "response_sha256": _digest(f"{host}:{task_case}:{index}:response"),
            "parent_session_sha256": _digest(
                f"{host}:{task_case}:{index}:parent-session"
            ),
            "child_session_sha256": child_session,
            "child_plugin_event_sha256": _digest(
                f"{host}:{task_case}:{index}:child-plugin-event"
            ),
            "child_plugin_session_sha256": child_session,
            "native_event_sequence_sha256": native_binding["event_sequence_sha256"],
            "native_session_identity_sha256": native_binding[
                "session_identity_sha256"
            ],
            "native_lifecycle_record_sha256": native_binding[
                "lifecycle_record_sha256"
            ],
            "same_process": True,
            "actual_route_observed": True,
        }
        proof["route_correlation_sha256"] = (
            host_process_receipt_v2.correlation_sha256(
                {
                    key: proof[key]
                    for key in (
                        "process_identity_sha256",
                        "request_method",
                        "route_observation_sha256",
                        "request_body_sha256",
                        "response_sha256",
                        "parent_session_sha256",
                        "child_session_sha256",
                        "child_plugin_event_sha256",
                        "child_plugin_session_sha256",
                        "native_event_sequence_sha256",
                        "native_session_identity_sha256",
                        "native_lifecycle_record_sha256",
                    )
                }
            )
        )
    return host_process_receipt_v2.build_receipt(
        host=host,
        task_case=task_case,
        run_id=f"fixture-{host}-{index}",
        candidate_binding=EXPECTED_CANDIDATE,
        run_binding=RUN_IDS,
        host_binary={"version": HOST_BINARY[host][0], "sha256": HOST_BINARY[host][1]},
        broker_source={
            "repository_external": True,
            "owner_only_mode": True,
            "sha256": BROKER_SOURCE_SHA256[host],
        },
        host_identity_sha256=bundle.host_identity_sha256(HOST_IDENTITY["hosts"][host]),
        host_identity_source_sha256=hashlib.sha256(
            bundle.canonical_json(HOST_IDENTITY) + b"\n"
        ).hexdigest(),
        process_identity_sha256=process_identity,
        broker_instance_sha256=_digest(f"{host}:{task_case}:{index}:broker-instance"),
        nonce_sha256=_digest(f"{host}:{task_case}:{index}:nonce"),
        issued_at="2026-08-27T00:00:00Z",
        expires_at="2026-08-27T00:04:00Z",
        validation_reference_time="2026-08-27T00:02:00Z",
        selector_source_symlink=host == "opencode",
        execution_target_regular=True,
        execution_target_single_link=True,
        status="exited",
        exit_code=0,
        native_event_binding=native_binding,
        proof=proof,
        isolation={
            "runner_received_secret": False,
            "mcp_received_secret": False,
            "ambient_auth_forwarded_to_mcp": False,
            "raw_output_retained": False,
        },
    )


def _make_fixture(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _copy_bindings(root)
    _write_json(root / bundle.HOST_IDENTITY_RELATIVE_PATH, HOST_IDENTITY)
    _write_json(
        root / bundle.HOST_EXECUTION_IDENTITY_RELATIVE_PATH,
        {
            "schema_version": "deeplaw.host-execution-identity/v1",
            "hosts": {
                host: {
                    "selector_source_symlink": host == "opencode",
                    "execution_target_regular": True,
                    "execution_target_single_link": True,
                    "host_identity_sha256": bundle.host_identity_sha256(
                        HOST_IDENTITY["hosts"][host]
                    ),
                    "host_identity_source_sha256": hashlib.sha256(
                        bundle.canonical_json(HOST_IDENTITY) + b"\n"
                    ).hexdigest(),
                }
                for host in ("codex", "opencode")
            },
        },
    )
    external_identity = root.parent / "external-host-exact-identity.json"
    _write_json(external_identity, HOST_IDENTITY)
    if external_identity.is_symlink():
        raise AssertionError("fixture external Host identity must not be a symlink")
    external_identity.chmod(0o600)
    corpus_hashes = {
        role: hashlib.sha256(role.encode()).hexdigest() for role in bundle.CORPUS_ROLES
    }
    typed_roles = {
        "candidate_full_junit": "candidate_full",
        "candidate_platform_receipt": "candidate_platform",
        "exact_wheel_execution": "candidate_full",
        "professional_evidence_rows": "professional_evidence",
        "wiki_journey_rows": "living_wiki",
        "context_capsule_selection_usage": "host_qualification",
        "scale_report": "scale_10000",
        "retained_supply_chain": "supply_chain",
    }
    for kind, role in typed_roles.items():
        workflow_id = (
            RUN_IDS["candidate_run_id"]
            if kind in bundle.CANDIDATE_WORKFLOW_KINDS
            else RUN_IDS["evidence_run_id"]
        )
        _write_json(
            root / "typed" / f"{kind}.json",
            _typed_manifest(kind, role, corpus_hashes[role], workflow_id=workflow_id),
        )
    task_cases = ("continuity", "living_wiki", "professional_evidence")
    for index in range(6):
        host = "codex" if index < 3 else "opencode"
        _write_json(
            root / "typed" / f"host_event_sequence-{host}-{index}.json",
            _typed_manifest(
                "host_event_sequence",
                "host_qualification",
                corpus_hashes["host_qualification"],
                workflow_id=RUN_IDS["evidence_run_id"],
            ),
        )
    for index in range(6):
        host = "codex" if index < 3 else "opencode"
        _write_json(root / "receipts" / host / f"preflight-{index}.json", _preflight(host))
        process_receipt = _process_receipt(host, task_cases[index % 3], index)
        _write_json(root / "receipts" / host / f"process-{index}.json", process_receipt)
    for host, raw in BROKER_SOURCE_BYTES.items():
        path = root / "retained-broker-source" / f"{host}.launcher-source"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def fake_parse(path: Path, **_: Any) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        metrics = {}
        if value["kind"] == "host_event_sequence":
            host = "codex" if "codex" in path.as_posix() else "opencode"
            index = int(path.stem.rsplit("-", 1)[-1])
            task_case = ("continuity", "living_wiki", "professional_evidence")[
                index % 3
            ]
            metrics.update(
                host=host,
                task_case=task_case,
                run_id=f"fixture-{host}-{index}",
                host_identity_sha256=bundle.host_identity_sha256(HOST_IDENTITY["hosts"][host]),
                **_native_event_binding(host, task_case, index),
            )
        return {
            "kind": value["kind"],
            "status": "passed",
            "evidence_record_sha256": value["record_sha256"],
            "metrics": metrics,
        }

    monkeypatch.setattr(bundle, "parse_typed_evidence", fake_parse)
    return external_identity


def test_build_and_validate_exact_candidate_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)

    built = bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external,
    )
    assert built["candidate_binding"] == EXPECTED_CANDIDATE
    assert built["record_sha256"] == bundle.record_sha256(built)

    validated = bundle.validate_bundle(
        root,
        expected_candidate=EXPECTED_CANDIDATE,
        expected_run_ids=RUN_IDS,
    )
    assert validated["status"] == "passed"
    assert validated["typed_counts"] == bundle.TYPED_COUNTS
    assert validated["preflight_receipt_count"] == 6
    assert validated["process_receipt_count"] == 6
    assert validated["broker_source_count"] == 2
    assert validated["corpus_roles"] == sorted(bundle.CORPUS_ROLES)


def test_bundle_accepts_six_retained_v2_slots_without_parallel_receipt_family(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external_identity = _make_fixture(root, monkeypatch)
    assert {path.name for path in tmp_path.iterdir()} == {
        "bundle",
        "external-host-exact-identity.json",
    }

    built = bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external_identity,
    )

    assert built["candidate_binding"] == EXPECTED_CANDIDATE
    assert bundle.validate_bundle(root)["process_receipt_count"] == 6


def test_external_host_identity_is_bound_and_replacement_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    _make_fixture(root, monkeypatch)
    external = tmp_path / "owner-host-identity.json"
    _write_json(external, HOST_IDENTITY)
    external.chmod(0o600)

    bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external,
    )
    replaced = json.loads(external.read_text(encoding="utf-8"))
    replaced["hosts"]["codex"]["binary_version"] = "codex-cli moving"
    _write_json(external, replaced)
    external.chmod(0o600)
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root, host_identity_input=external)


def test_exact_10k_report_is_owned_by_candidate_full_run() -> None:
    assert "scale_report" in bundle.CANDIDATE_WORKFLOW_KINDS


def test_manifest_record_digest_and_file_hash_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)
    bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external,
    )

    typed_file = root / "typed" / "scale_report.json"
    typed_file.write_bytes(typed_file.read_bytes() + b"\n")
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root)

    external = _make_fixture(root, monkeypatch)
    bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external,
    )
    manifest_path = root / bundle.MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_ids"]["qualification_run_id"] += 1
    _write_json(manifest_path, manifest)
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root)

    _make_fixture(root, monkeypatch)
    (root / "auth.json").write_text("{}", encoding="utf-8")
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root)


def test_bundle_rejects_symlink_and_competitive_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)
    bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external,
    )

    try:
        (root / "receipts" / "codex" / "extra-link.json").symlink_to(
            root / "receipts" / "codex" / "process-0.json"
        )
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root)

    (root / "receipts" / "codex" / "extra-link.json").unlink()
    manifest_path = root / bundle.MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["superiority"] = False
    _write_json(manifest_path, manifest)
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root)


def test_manifest_candidate_binding_and_secret_filename_are_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)
    bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external,
    )

    manifest_path = root / bundle.MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidate_binding"]["commit"] = "f" * 40
    manifest["record_sha256"] = bundle.record_sha256(manifest)
    _write_json(manifest_path, manifest)
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root)


def test_process_receipt_is_schema_bound_and_cannot_retain_raw_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)
    bundle.build_bundle(
        root,
        run_ids=RUN_IDS,
        expected_candidate=EXPECTED_CANDIDATE,
        host_identity_input=external,
    )

    process_path = root / "receipts" / "codex" / "process-0.json"
    process = json.loads(process_path.read_text(encoding="utf-8"))
    process["stdout"] = "forbidden raw output"
    process["record_sha256"] = bundle.record_sha256(process)
    _write_json(process_path, process)
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root)


def test_failed_preflight_cannot_enter_a_kernel_qualification_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)
    preflight_path = root / "receipts" / "opencode" / "preflight-3.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight.update(
        status="failed",
        stage="auth",
        reason_code="auth_unavailable",
    )
    _write_json(preflight_path, preflight)

    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.build_bundle(
            root,
            run_ids=RUN_IDS,
            expected_candidate=EXPECTED_CANDIDATE,
            host_identity_input=external,
        )


def test_broker_receipts_bind_the_exact_retained_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)
    broker_path = root / "retained-broker-source" / "codex.launcher-source"
    broker_path.write_bytes(broker_path.read_bytes() + b"# changed\n")

    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.build_bundle(
            root,
            run_ids=RUN_IDS,
            expected_candidate=EXPECTED_CANDIDATE,
            host_identity_input=external,
        )


def test_retained_broker_source_rejects_a_credential_literal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)
    broker_path = root / "retained-broker-source" / "opencode.launcher-source"
    broker_path.write_bytes(
        b"#!/bin/sh\nDEEPSEEK_API_KEY=abcdefghijklmnopqrstuvwxyz123456\n"
    )

    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.build_bundle(
            root,
            run_ids=RUN_IDS,
            expected_candidate=EXPECTED_CANDIDATE,
            host_identity_input=external,
        )


def test_process_receipt_run_and_record_digest_are_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)

    process_path = root / "receipts" / "codex" / "process-0.json"
    process = json.loads(process_path.read_text(encoding="utf-8"))
    process["run_id"] = "unrelated-run"
    process["record_sha256"] = bundle.record_sha256(process)
    _write_json(process_path, process)
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.build_bundle(
            root,
            run_ids=RUN_IDS,
            expected_candidate=EXPECTED_CANDIDATE,
            host_identity_input=external,
        )

    _make_fixture(root, monkeypatch)
    process_path = root / "receipts" / "codex" / "process-0.json"
    process = json.loads(process_path.read_text(encoding="utf-8"))
    process["record_sha256"] = "f" * 64
    _write_json(process_path, process)
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.validate_bundle(root)


def test_bundle_rejects_a_file_above_the_closed_size_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    external = _make_fixture(root, monkeypatch)
    oversized = root / "candidate-inventory.bin"
    with oversized.open("wb") as stream:
        stream.truncate(bundle.MAX_FILE_BYTES + 1)
    with pytest.raises(bundle.KernelQualificationBundleError):
        bundle.build_bundle(
            root,
            run_ids=RUN_IDS,
            expected_candidate=EXPECTED_CANDIDATE,
            host_identity_input=external,
        )

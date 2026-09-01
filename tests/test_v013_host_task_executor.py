from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from benchmarks.hosts import host_process_receipt_v2
from benchmarks.hosts import run_v013_host_task_executor as executor


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


CANDIDATE = {
    "commit": "1" * 40,
    "tree": "2" * 40,
    "lock_sha256": "3" * 64,
    "wheel_sha256": "4" * 64,
    "sdist_sha256": "5" * 64,
}
IDENTITY = {
    "schema_version": "deeplaw.host-exact-identity/v1",
    "hosts": {
        "codex": {
            "binary_version": "codex-cli test",
            "binary_sha256": "6" * 64,
        },
        "opencode": {
            "version": "1.18.16",
            "executable_sha256": "7" * 64,
        },
    },
    "source_sha256": "8" * 64,
    "source_bytes": 10,
}
BROKER_BYTES = {"codex": b"codex broker\n", "opencode": b"opencode broker\n"}
BROKER_SHA = {host: hashlib.sha256(raw).hexdigest() for host, raw in BROKER_BYTES.items()}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _native(host: str, task: str) -> dict[str, str]:
    return {
        "event_sequence_sha256": _sha(f"{host}:{task}:event"),
        "session_identity_sha256": _sha(f"{host}:{task}:session"),
        "lifecycle_record_sha256": _sha(f"{host}:{task}:lifecycle"),
    }


def _v2_process(host: str, task: str, *, index: int) -> dict[str, Any]:
    process_identity = _sha(f"{host}:{task}:{index}:process")
    native = _native(host, task)
    if host == "codex":
        proof = {
            "proof_kind": "codex_stdio_hook_correlation",
            "process_identity_sha256": process_identity,
            "connection_sha256": _sha(f"{host}:{task}:{index}:connection"),
            "initialize_request_sha256": _sha(
                f"{host}:{task}:{index}:initialize-request"
            ),
            "initialized_notification_sha256": _sha(
                f"{host}:{task}:{index}:initialized-notification"
            ),
            "initialized_connection_count": 1,
            "hook_session_sha256": _sha(f"{host}:{task}:{index}:hook-session"),
            "hook_event_sha256": _sha(f"{host}:{task}:{index}:hook-event"),
            "native_event_sequence_sha256": native["event_sequence_sha256"],
            "native_session_identity_sha256": native["session_identity_sha256"],
            "native_lifecycle_record_sha256": native["lifecycle_record_sha256"],
            "same_process": True,
            "same_connection": True,
        }
        proof["connection_correlation_sha256"] = host_process_receipt_v2.correlation_sha256(
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
    else:
        child_session = _sha(f"{host}:{task}:{index}:child-session")
        proof = {
            "proof_kind": "opencode_public_fork_route_correlation",
            "process_identity_sha256": process_identity,
            "request_method": "POST",
            "route_observation_sha256": _sha(f"{host}:{task}:{index}:actual-route"),
            "request_body_sha256": hashlib.sha256(b"{}").hexdigest(),
            "response_sha256": _sha(f"{host}:{task}:{index}:response"),
            "parent_session_sha256": _sha(f"{host}:{task}:{index}:parent-session"),
            "child_session_sha256": child_session,
            "child_plugin_event_sha256": _sha(f"{host}:{task}:{index}:plugin-event"),
            "child_plugin_session_sha256": child_session,
            "native_event_sequence_sha256": native["event_sequence_sha256"],
            "native_session_identity_sha256": native["session_identity_sha256"],
            "native_lifecycle_record_sha256": native["lifecycle_record_sha256"],
            "same_process": True,
            "actual_route_observed": True,
        }
        proof["route_correlation_sha256"] = host_process_receipt_v2.correlation_sha256(
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
    return host_process_receipt_v2.build_receipt(
        host=host,
        task_case=task,
        run_id=f"run-{host}-{task}",
        candidate_binding=CANDIDATE,
        run_binding={"evidence_run_id": 202, "qualification_run_id": 303},
        host_binary=executor.host_preflight_receipt.host_binary_identity(IDENTITY, host),
        broker_source={
            "repository_external": True,
            "owner_only_mode": True,
            "sha256": BROKER_SHA[host],
        },
        host_identity_sha256=executor.host_preflight_receipt.host_identity_sha256(
            IDENTITY["hosts"][host]
        ),
        host_identity_source_sha256=IDENTITY["source_sha256"],
        process_identity_sha256=process_identity,
        broker_instance_sha256=_sha(f"{host}:{task}:{index}:broker-instance"),
        nonce_sha256=_sha(f"{host}:{task}:{index}:nonce"),
        issued_at="2026-08-27T00:00:00Z",
        expires_at="2026-08-27T00:05:00Z",
        validation_reference_time="2026-08-27T00:02:00Z",
        selector_source_symlink=host == "opencode",
        execution_target_regular=True,
        execution_target_single_link=True,
        status="exited",
        exit_code=0,
        native_event_binding=native,
        proof=proof,
        isolation={
            "runner_received_secret": False,
            "mcp_received_secret": False,
            "ambient_auth_forwarded_to_mcp": False,
            "raw_output_retained": False,
        },
    )


def _make_source(root: Path) -> None:
    if os.name != "nt":
        root.chmod(0o700)
    execution = {
        "schema_version": executor.EXECUTION_IDENTITY_SCHEMA,
        "hosts": {
            host: {
                "selector_source_symlink": host == "opencode",
                "execution_target_regular": True,
                "execution_target_single_link": True,
                "host_identity_sha256": executor.host_preflight_receipt.host_identity_sha256(
                    IDENTITY["hosts"][host]
                ),
                "host_identity_source_sha256": IDENTITY["source_sha256"],
            }
            for host in executor.HOSTS
        },
    }
    _write_json(
        root / "candidate-inventory" / "host-execution-identity.json",
        execution,
    )
    for host, raw in BROKER_BYTES.items():
        path = root / "retained-broker-source" / f"{host}.launcher-source"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    for host in executor.HOSTS:
        binary = executor.host_preflight_receipt.host_binary_identity(IDENTITY, host)
        for task in executor.TASK_CASES:
            payload: dict[str, Any] = {}
            for source_key in executor.TYPED_SOURCE_SLOTS:
                relative = f"sources/{source_key}.json"
                raw = json.dumps(
                    {"host": host, "task": task, "source": source_key},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                source_path = root / "slots" / host / task / relative
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(raw)
                payload[source_key] = {
                    "relative_path": relative,
                    "byte_size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "media_type": "application/json",
                }
            _write_json(
                root / "slots" / host / task / "host-event-sequence.json",
                {
                    "schema_version": executor.TYPED_SCHEMA,
                    "kind": "host_event_sequence",
                    "corpus": {"role": "host_qualification", "sha256": _sha("corpus")},
                    "payload": payload,
                    "record_sha256": _sha(f"{host}:{task}:record"),
                },
            )
            _write_json(
                root / "receipts" / host / task / "host-preflight.json",
                {
                    "status": "passed",
                    "host": {
                        "name": host,
                        "version": binary["version"],
                        "sha256": binary["sha256"],
                    },
                    "broker_source": {
                        "repository_external": True,
                        "owner_only_mode": True,
                        "sha256": BROKER_SHA[host],
                        "expected_sha256": BROKER_SHA[host],
                    },
                },
            )
            process_receipts = [
                _v2_process(host, task, index=index)
                for index in (1, 2)
            ]
            process_set = executor.host_process_receipt_set_v1.build_receipt_set(
                host=host,
                task_case=task,
                run_id=f"run-{host}-{task}",
                candidate_binding=CANDIDATE,
                run_binding={"evidence_run_id": 202, "qualification_run_id": 303},
                host_binary=binary,
                broker_source={
                    "repository_external": True,
                    "owner_only_mode": True,
                    "sha256": BROKER_SHA[host],
                },
                host_identity_sha256=executor.host_preflight_receipt.host_identity_sha256(
                    IDENTITY["hosts"][host]
                ),
                host_identity_source_sha256=IDENTITY["source_sha256"],
                task_native_event_binding=_native(host, task),
                processes=process_receipts,
            )
            _write_json(
                root / "receipts" / host / task / "host-process.json",
                process_set,
            )
    if os.name == "nt":
        from deeplaw.windows_acl import harden_windows_vault

        hardening = harden_windows_vault(root.parent)
        assert hardening["applied"] is True
        assert hardening["verification"]["permissions_verified"] is True


def _patch_validator_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        executor,
        "validate_external_collector_handoff",
        lambda *args, **kwargs: {"status": "not_executed"},
    )
    monkeypatch.setattr(
        executor,
        "load_exact_candidate_binding",
        lambda *args, **kwargs: dict(CANDIDATE),
    )
    monkeypatch.setattr(
        executor.host_preflight_receipt,
        "load_host_identity_input_with_bytes",
        lambda *args, **kwargs: (dict(IDENTITY), b"identity\n"),
    )
    monkeypatch.setattr(
        executor.host_preflight_receipt,
        "validate_receipt",
        lambda value: dict(value),
    )

    def retained(path: Path, **_: Any) -> dict[str, Any]:
        task = path.parent.name
        host = path.parent.parent.name
        envelope = json.loads(path.read_text(encoding="utf-8"))
        return {
            "kind": "host_event_sequence",
            "status": "passed",
            "evidence_record_sha256": envelope["record_sha256"],
            "metrics": {
                "host": host,
                "task_case": task,
                "run_id": f"run-{host}-{task}",
                **_native(host, task),
            },
        }

    monkeypatch.setattr(executor, "validate_retained_manifest", retained)
    monkeypatch.setattr(
        executor,
        "validate_host_task_matrix",
        lambda results, **kwargs: {
            "status": "derived",
            "result_count": len(results),
        },
    )

def _arguments(tmp_path: Path) -> dict[str, Any]:
    return {
        "handoff": tmp_path / "handoff.json",
        "candidate_binding_input": tmp_path / "candidate.json",
        "evidence_run_id": 202,
        "qualification_run_id": 303,
        "host_identity_input": tmp_path / "identity.json",
        "codex_broker_sha256": BROKER_SHA["codex"],
        "opencode_broker_sha256": BROKER_SHA["opencode"],
    }


class _WindowsOSProxy:
    """Simulate executor platform branching without mutating the test runner."""

    name = "nt"

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)


def _simulate_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "os", _WindowsOSProxy())


def _verified_acl_report(
    path: Path | None = None,
    *,
    checked: int = 1,
    kind: str = "directory",
) -> dict[str, Any]:
    selected = path or Path("/tmp/acl-fixture")
    current_sid = "S-1-5-21-1000"
    entries = [
        {
            "path": str(selected if index == 0 else selected / f"entry-{index}"),
            "kind": kind if index == 0 else "file",
            "owner_sid": current_sid,
            "owner_matches_current_user": True,
            "reparse_point": False,
            "acl_inheritance_enabled": False,
            "inherited_rule_count": 0,
            "users_rule_count": 0,
            "everyone_rule_count": 0,
            "valid": True,
        }
        for index in range(min(checked, 1000))
    ]
    return {
        "schema_version": "deeplaw.windows-acl-report/v1",
        "current_user_sid": current_sid,
        "entry_count": checked,
        "owner_sid_verified": True,
        "users_principal_sid": "S-1-5-32-545",
        "everyone_principal_sid": "S-1-1-0",
        "reparse_points_absent": True,
        "permissions_verified": True,
        "errors": [],
        "errors_truncated": False,
        "entries": entries,
        "entries_truncated": checked > 1000,
        "platform": "nt",
        "status": "verified",
        "scan_complete": True,
        "files_and_directories_checked": checked,
    }


def _verified_hardening_report(path: Path | None = None, *, checked: int = 1) -> dict[str, Any]:
    selected = path or Path("/tmp/acl-fixture")
    return {
        "schema_version": "deeplaw.windows-acl-hardening/v1",
        "platform": "nt",
        "applied": True,
        "item_count": checked,
        "current_user_sid": "S-1-5-21-1000",
        "verification": _verified_acl_report(selected, checked=checked),
    }


def test_windows_acl_canary_rejects_unverified_source_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A macOS simulation must fail closed on an unverified native ACL report."""

    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)

    _simulate_windows(monkeypatch)
    from deeplaw import windows_acl

    monkeypatch.setattr(
        windows_acl,
        "native_windows_acl_report",
        lambda _root: {"permissions_verified": False},
    )

    with pytest.raises(executor.HostTaskExecutorError, match="source root"):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "incomplete",
        "extra",
        "wrong_owner",
        "inheritance",
        "reparse",
        "wrong_path",
        "wrong_kind",
        "count",
        "truncated",
        "error",
    ),
)
def test_windows_acl_admission_rejects_malformed_source_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    _simulate_windows(monkeypatch)
    from deeplaw import windows_acl

    report: dict[str, Any] = _verified_acl_report(source.resolve())
    if mutation == "incomplete":
        report = {"platform": "nt", "permissions_verified": True}
    elif mutation == "extra":
        report["unexpected"] = True
    elif mutation == "wrong_owner":
        report["entries"][0]["owner_sid"] = "S-1-5-21-9999"
    elif mutation == "inheritance":
        report["entries"][0]["acl_inheritance_enabled"] = True
        report["entries"][0]["inherited_rule_count"] = 1
    elif mutation == "reparse":
        report["entries"][0]["reparse_point"] = True
    elif mutation == "wrong_path":
        report["entries"][0]["path"] = str(tmp_path / "other")
    elif mutation == "wrong_kind":
        report["entries"][0]["kind"] = "file"
    elif mutation == "count":
        report["entry_count"] = 2
    elif mutation == "truncated":
        report["entries_truncated"] = True
    else:
        report["errors"] = ["unexpected_acl_error"]

    monkeypatch.setattr(
        windows_acl,
        "native_windows_acl_report",
        lambda _path: copy.deepcopy(report),
    )
    output = tmp_path / "final"
    with pytest.raises(executor.HostTaskExecutorError, match="source root"):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation",
    ("wrong_owner", "inheritance", "wrong_path", "details", "count"),
)
def test_windows_acl_admission_rejects_malformed_hardening_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    _simulate_windows(monkeypatch)
    from deeplaw import windows_acl

    monkeypatch.setattr(
        windows_acl,
        "native_windows_acl_report",
        lambda path: _verified_acl_report(Path(path), checked=1),
    )
    monkeypatch.setattr(
        windows_acl,
        "native_windows_path_acl_report",
        lambda path: _verified_acl_report(Path(path), checked=1),
    )

    def hardener(path: Path) -> dict[str, Any]:
        report = _verified_hardening_report(Path(path))
        verification = report["verification"]
        if mutation == "wrong_owner":
            report["current_user_sid"] = "S-1-5-21-9999"
        elif mutation == "inheritance":
            verification["entries"][0]["acl_inheritance_enabled"] = True
        elif mutation == "wrong_path":
            verification["entries"][0]["path"] = str(tmp_path / "other")
        elif mutation == "details":
            verification.pop("entries")
        else:
            report["item_count"] = 2
        return report

    monkeypatch.setattr(windows_acl, "harden_windows_vault", hardener)
    output = tmp_path / "final"
    with pytest.raises(executor.HostTaskExecutorError, match="temporary staging"):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()
    assert not list(tmp_path.glob(".final.admit-*"))


def test_windows_acl_canary_rejects_unverified_output_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    _simulate_windows(monkeypatch)
    from deeplaw import windows_acl

    monkeypatch.setattr(
        windows_acl,
        "native_windows_acl_report",
        lambda path: _verified_acl_report(Path(path), checked=1),
    )
    unverified = _verified_acl_report()
    unverified["status"] = "failed"
    unverified["permissions_verified"] = False
    monkeypatch.setattr(
        windows_acl,
        "native_windows_path_acl_report",
        lambda _path: unverified,
    )

    output = tmp_path / "final"
    with pytest.raises(executor.HostTaskExecutorError, match="output parent"):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()
    assert not list(tmp_path.glob(".final.admit-*"))


def test_windows_acl_hardener_failure_rolls_back_temporary_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    _simulate_windows(monkeypatch)
    from deeplaw import windows_acl

    events: list[tuple[str, Path]] = []

    def source_acl(path: Path) -> dict[str, Any]:
        events.append(("source", Path(path)))
        return _verified_acl_report(Path(path), checked=1)

    def parent_acl(path: Path) -> dict[str, Any]:
        events.append(("parent", Path(path)))
        return _verified_acl_report(Path(path), checked=1)

    def fail_hardener(path: Path) -> None:
        events.append(("harden", Path(path)))
        raise RuntimeError("injected hardener failure")

    monkeypatch.setattr(windows_acl, "native_windows_acl_report", source_acl)
    monkeypatch.setattr(windows_acl, "native_windows_path_acl_report", parent_acl)
    monkeypatch.setattr(windows_acl, "harden_windows_vault", fail_hardener)

    output = tmp_path / "final"
    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert [kind for kind, _ in events] == ["source", "parent", "harden"]
    assert not output.exists()
    assert not list(tmp_path.glob(".final.admit-*"))


def test_windows_acl_post_write_recursive_failure_rolls_back_temporary_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    _simulate_windows(monkeypatch)
    from deeplaw import windows_acl

    recursive_roots: list[Path] = []
    unverified = _verified_acl_report(checked=1)
    unverified["status"] = "failed"
    unverified["permissions_verified"] = False

    def recursive_acl(path: Path) -> dict[str, Any]:
        selected = Path(path)
        recursive_roots.append(selected)
        if len(recursive_roots) == 2:
            assert (
                selected / "candidate-inventory" / "host-execution-identity.json"
            ).is_file()
            return unverified
        return _verified_acl_report(Path(path), checked=1)

    monkeypatch.setattr(windows_acl, "native_windows_acl_report", recursive_acl)
    monkeypatch.setattr(
        windows_acl,
        "native_windows_path_acl_report",
        lambda path: _verified_acl_report(Path(path), checked=1),
    )
    monkeypatch.setattr(
        windows_acl,
        "harden_windows_vault",
        lambda path: _verified_hardening_report(Path(path)),
    )

    output = tmp_path / "final"
    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert len(recursive_roots) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".final.admit-*"))


def test_windows_acl_hardening_and_validation_run_before_promotion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    _simulate_windows(monkeypatch)
    from deeplaw import windows_acl

    events: list[tuple[str, Path]] = []

    def recursive_acl(path: Path) -> dict[str, Any]:
        selected = Path(path)
        events.append(("recursive", selected))
        return _verified_acl_report(selected, checked=1)

    def parent_acl(path: Path) -> dict[str, Any]:
        selected = Path(path)
        events.append(("parent", selected))
        return _verified_acl_report(selected, checked=1)

    def hardener(path: Path) -> dict[str, Any]:
        selected = Path(path)
        events.append(("harden", selected))
        if len([kind for kind, _ in events if kind == "harden"]) == 2:
            assert (
                selected / "candidate-inventory" / "host-execution-identity.json"
            ).is_file()
        return _verified_hardening_report(selected)

    monkeypatch.setattr(windows_acl, "native_windows_acl_report", recursive_acl)
    monkeypatch.setattr(windows_acl, "native_windows_path_acl_report", parent_acl)
    monkeypatch.setattr(windows_acl, "harden_windows_vault", hardener)

    output = tmp_path / "final"
    result = executor.admit_host_task_staging(source, output, **_arguments(tmp_path))

    assert result["status"] == "admitted"
    assert [kind for kind, _ in events] == [
        "recursive",
        "parent",
        "harden",
        "harden",
        "recursive",
    ]
    assert events[0][1] == source.resolve()
    assert events[1][1] == tmp_path.resolve()
    assert events[2][1].name.startswith(".final.admit-")
    assert events[3][1] == events[2][1]
    assert events[4][1] == events[2][1]
    assert executor._inventory(output) == executor._inventory(source)
    assert not list(tmp_path.glob(".final.admit-*"))


def test_admits_exact_six_slot_tree_transactionally(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    result = executor.admit_host_task_staging(
        source,
        output,
        **_arguments(tmp_path),
    )

    assert result == {
        "schema_version": "deeplaw.v013-host-task-executor-admission/v1",
        "status": "admitted",
        "slot_count": 6,
        "host_count": 2,
        "task_count_per_host": 3,
        "candidate_binding": CANDIDATE,
        "evidence_run_id": 202,
        "qualification_run_id": 303,
        "claim_eligible": False,
        "formal_admission": False,
        "release_ready": False,
    }
    assert executor._inventory(output) == executor._inventory(source)


def test_stable_reader_accepts_windows_cross_interface_mode_difference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(b'{"safe":true}\n')
    real_fstat = os.fstat

    def windows_style_fstat(descriptor: int) -> os.stat_result:
        observed = real_fstat(descriptor)
        fields = list(observed)
        fields[0] = observed.st_mode ^ stat.S_IXUSR
        return os.stat_result(fields)

    monkeypatch.setattr(executor.os, "fstat", windows_style_fstat)

    assert executor._read_stable_file(source, maximum_bytes=1024) == b'{"safe":true}\n'


def test_missing_slot_fails_without_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    (source / "slots" / "codex" / "living_wiki" / "host-event-sequence.json").unlink()
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


def test_symlink_and_hardlink_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_validator_seams(monkeypatch)
    for kind in ("symlink", "hardlink"):
        case_parent = tmp_path / f"{kind}-case"
        case_parent.mkdir(mode=0o700)
        if os.name != "nt":
            case_parent.chmod(0o700)
        source = case_parent / "source"
        source.mkdir()
        _make_source(source)
        original = source / "retained-broker-source" / "codex.launcher-source"
        extra = source / "extra.json"
        if kind == "symlink":
            extra.symlink_to(original)
        else:
            os.link(original, extra)
        output = case_parent / "final"
        with pytest.raises(executor.HostTaskExecutorError):
            executor.admit_host_task_staging(source, output, **_arguments(case_parent))
        assert not output.exists()


def test_native_process_binding_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    receipt = source / "receipts" / "opencode" / "professional_evidence" / "host-process.json"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["task_native_event_binding"]["event_sequence_sha256"] = "f" * 64
    _write_json(receipt, value)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


def test_process_topology_must_match_execution_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    receipt = source / "receipts" / "opencode" / "continuity" / "host-process.json"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["processes"][0]["receipt"]["selector_source_symlink"] = False
    _write_json(receipt, value)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


def test_second_validation_failure_rolls_back_temporary_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    original = executor._validate_staging
    calls = 0

    def fail_second(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise executor.HostTaskExecutorError("injected staged-byte rejection")
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "_validate_staging", fail_second)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()
    assert not list(tmp_path.glob(".final.admit-*"))


def test_existing_output_is_never_replaced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"
    output.mkdir()

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert output.is_dir()


def test_atomic_promotion_never_replaces_racing_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"
    original = executor._promote_no_replace

    def race(staging: Path, target: Path) -> None:
        target.mkdir()
        (target / "winner.txt").write_text("independent winner", encoding="utf-8")
        original(staging, target)

    monkeypatch.setattr(executor, "_promote_no_replace", race)

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert (output / "winner.txt").read_text(encoding="utf-8") == "independent winner"
    assert not list(tmp_path.glob(".final.admit-*"))


def test_post_rename_fsync_failure_is_reported_as_committed_uncertain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    def fail_parent_fsync(descriptor: int) -> None:
        raise OSError("injected post-rename fsync failure")

    monkeypatch.setattr(executor, "_fsync_promoted_parent", fail_parent_fsync)

    if os.name == "nt":
        result = executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
        assert result["status"] == "admitted"
        assert executor._inventory(output) == executor._inventory(source)
        return

    with pytest.raises(executor.HostTaskPromotionCommittedError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert executor._inventory(output) == executor._inventory(source)
    assert not list(tmp_path.glob(".final.admit-*"))


@pytest.mark.parametrize("kind", ("file", "directory", "forbidden_directory"))
def test_slot_topology_rejects_every_extra_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    slot = source / "slots" / "codex" / "continuity"
    if kind == "file":
        (slot / "extra.json").write_text("{}", encoding="utf-8")
    elif kind == "directory":
        (slot / "extra").mkdir()
    else:
        (slot / "secret-material").mkdir()
    _patch_validator_seams(monkeypatch)

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / f"final-{kind}",
            **_arguments(tmp_path),
        )


@pytest.mark.parametrize("failure", ("status", "record"))
def test_failed_or_record_unbound_typed_evidence_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    retained = executor.validate_retained_manifest

    def invalid(path: Path, **kwargs: Any) -> dict[str, Any]:
        value = retained(path, **kwargs)
        if path.parent.parent.name == "codex" and path.parent.name == "continuity":
            if failure == "status":
                value["status"] = "failed"
            else:
                value["evidence_record_sha256"] = "f" * 64
        return value

    monkeypatch.setattr(executor, "validate_retained_manifest", invalid)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


def test_identity_change_between_public_reads_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    reads = iter((b"identity-one\n", b"identity-two\n"))
    monkeypatch.setattr(
        executor.host_preflight_receipt,
        "load_host_identity_input_with_bytes",
        lambda *args, **kwargs: (dict(IDENTITY), next(reads)),
    )

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )


def test_construction_v2_candidate_is_rejected_by_real_exact_loader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    real_loader = executor.load_exact_candidate_binding
    _patch_validator_seams(monkeypatch)
    monkeypatch.setattr(executor, "load_exact_candidate_binding", real_loader)
    candidate = tmp_path / "candidate.json"
    _write_json(
        candidate,
        {"schema_version": "deeplaw.v013-external-kit-manifest/v2"},
    )
    arguments = _arguments(tmp_path)
    arguments["candidate_binding_input"] = candidate

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **arguments,
        )


def test_source_symlink_race_after_snapshot_cannot_escape_captured_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("authorization: bearer EXTERNAL_SECRET_123456789", encoding="utf-8")
    broker = source / "retained-broker-source" / "codex.launcher-source"
    original_write = executor._write_snapshot

    def mutate_then_write(snapshot: dict[str, bytes], target: Path) -> None:
        broker.unlink()
        broker.symlink_to(outside)
        original_write(snapshot, target)

    monkeypatch.setattr(executor, "_write_snapshot", mutate_then_write)
    output = tmp_path / "final"

    executor.admit_host_task_staging(source, output, **_arguments(tmp_path))

    assert (
        output / "retained-broker-source" / "codex.launcher-source"
    ).read_bytes() == BROKER_BYTES["codex"]


def test_chmod_failure_rolls_back_temporary_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    original = Path.chmod

    def fail_staging(path: Path, mode: int, *args: Any, **kwargs: Any) -> None:
        if path.name.startswith(".final.admit-"):
            raise OSError("injected chmod failure")
        original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", fail_staging)

    if os.name == "nt":
        result = executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )
        assert result["status"] == "admitted"
        assert not list(tmp_path.glob(".final.admit-*"))
        return

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )
    assert not list(tmp_path.glob(".final.admit-*"))


def test_broker_source_rejects_credential_literal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    secret = b'api_key="ABCDEFGHIJKLMNOPQRSTUVWX"\n'
    (source / "retained-broker-source" / "codex.launcher-source").write_bytes(secret)
    _patch_validator_seams(monkeypatch)
    arguments = _arguments(tmp_path)
    arguments["codex_broker_sha256"] = hashlib.sha256(secret).hexdigest()

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **arguments,
        )


@pytest.mark.parametrize("relative", (".env", "credentials/input.json", "secrets.json"))
def test_forbidden_source_component_is_rejected_before_content_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    slot = source / "slots" / "codex" / "continuity"
    manifest = slot / "host-event-sequence.json"
    envelope = json.loads(manifest.read_text(encoding="utf-8"))
    original = slot / envelope["payload"]["event_source"]["relative_path"]
    forbidden = slot / relative
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    original.replace(forbidden)
    envelope["payload"]["event_source"]["relative_path"] = relative
    _write_json(manifest, envelope)
    _patch_validator_seams(monkeypatch)
    stable_read = executor._read_stable_file
    forbidden_read = False

    def observe(path: Path, *, maximum_bytes: int) -> bytes:
        nonlocal forbidden_read
        if path == forbidden:
            forbidden_read = True
        return stable_read(path, maximum_bytes=maximum_bytes)

    monkeypatch.setattr(executor, "_read_stable_file", observe)

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )
    assert forbidden_read is False

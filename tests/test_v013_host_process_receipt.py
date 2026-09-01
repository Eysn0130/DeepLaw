from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.hosts import host_preflight_receipt as preflight
from benchmarks.hosts import host_process_receipt as receipt


def _verified_acl_report(path: Path, *, kind: str = "file") -> dict[str, object]:
    current_sid = "S-1-5-21-1000"
    return {
        "schema_version": "deeplaw.windows-acl-report/v1",
        "current_user_sid": current_sid,
        "entry_count": 1,
        "owner_sid_verified": True,
        "users_principal_sid": "S-1-5-32-545",
        "everyone_principal_sid": "S-1-1-0",
        "reparse_points_absent": True,
        "permissions_verified": True,
        "errors": [],
        "errors_truncated": False,
        "entries": [
            {
                "path": str(path),
                "kind": kind,
                "owner_sid": current_sid,
                "owner_matches_current_user": True,
                "reparse_point": False,
                "acl_inheritance_enabled": False,
                "inherited_rule_count": 0,
                "users_rule_count": 0,
                "everyone_rule_count": 0,
                "valid": True,
            }
        ],
        "entries_truncated": False,
        "platform": "nt",
        "status": "verified",
        "scan_complete": True,
        "files_and_directories_checked": 1,
    }


def _identity(
    *,
    codex_version: str,
    codex_sha256: str,
    opencode_sha256: str,
    opencode_version: str = "1.18.16",
) -> dict[str, object]:
    return {
        "schema_version": preflight.HOST_IDENTITY_SCHEMA_VERSION,
        "hosts": {
            "codex": {
                "binary_version": codex_version,
                "binary_sha256": codex_sha256,
                "request_model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "auth_status_command": "codex login status",
                "auth_material_access": "forbidden",
            },
            "opencode": {
                "version": opencode_version,
                "source_commit": "a" * 40,
                "config_selector": "deepseek/deepseek-v4-flash",
                "expected_response_model_id": "deepseek-v4-flash",
                "executable_sha256": opencode_sha256,
                "package_sha256": "b" * 64,
                "runtime": "host_bun_runtime_only",
                "dotenv_policy": "owner_only_external_strict_parser",
                "secret_visibility": "forbidden",
            },
        },
        "source_sha256": "c" * 64,
        "source_bytes": 128,
    }


def _artifacts(tmp_path: Path, host: str) -> tuple[dict[str, object], Path, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    version = "codex-cli 0.149.0-alpha.4.3" if host == "codex" else "1.18.16"
    binary = tmp_path / f"{host}-binary"
    if os.name == "nt":
        binary = Path(sys.executable).resolve(strict=True)
        completed = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            check=True,
            timeout=30,
            env=preflight._host_version_probe_environment(),
        )
        version = (completed.stdout + completed.stderr).decode("utf-8").strip()
    else:
        binary.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8"
        )
    if os.name != "nt":
        binary.chmod(0o700)
    binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
    broker = tmp_path / f"{host}-broker"
    broker.write_bytes(b"#!/bin/sh\nexit 0\n")
    broker.chmod(0o700)
    if os.name == "nt":
        from deeplaw.windows_acl import harden_windows_private_file

        hardening = harden_windows_private_file(broker)
        assert hardening["applied"] is True
        assert hardening["verification"]["permissions_verified"] is True
    identity = _identity(
        codex_version="codex-cli 0.149.0-alpha.4.3",
        codex_sha256=binary_sha256 if host == "codex" else "d" * 64,
        opencode_sha256=binary_sha256 if host == "opencode" else "e" * 64,
        opencode_version=version if host == "opencode" else "1.18.16",
    )
    if host == "codex" and os.name == "nt":
        identity["hosts"]["codex"]["binary_version"] = version
    return identity, repository, binary, broker


def _build(
    tmp_path: Path, host: str = "opencode", *, selector: bool = False
) -> tuple[dict[str, object], dict[str, object]]:
    identity, repository, binary, broker = _artifacts(tmp_path, host)
    selected_binary = binary
    if selector:
        selected_binary = tmp_path / f"{host}-selector"
        selected_binary.symlink_to(binary)
    value = receipt.build_process_receipt(
        host=host,
        task_case="continuity",
        run_id=f"run-{host}-001",
        identity=identity,
        repository=repository,
        host_binary=selected_binary,
        broker_path=broker,
        supervisor={"observed": True, "exit_code": 0},
        isolation={
            "runner_received_secret": False,
            "mcp_received_secret": False,
            "ambient_auth_forwarded_to_mcp": False,
            "raw_output_retained": False,
        },
        execution_identity={
            "selector_source_symlink": selector,
            "execution_target_regular": True,
            "execution_target_single_link": True,
        },
        expected_broker_sha256=hashlib.sha256(broker.read_bytes()).hexdigest(),
    )
    return value, identity


@pytest.mark.parametrize(
    ("host", "selector"),
    (("codex", False), ("opencode", False), ("opencode", True)),
)
def test_valid_codex_and_opencode_receipts_are_path_free_and_identity_bound(
    tmp_path: Path, host: str, selector: bool
) -> None:
    value, identity = _build(tmp_path, host, selector=selector)
    assert receipt.validate_process_receipt(
        value,
        identity=identity,
        expected_host=host,
        expected_task_case="continuity",
        expected_run_id=f"run-{host}-001",
    ) == value
    assert value["record_sha256"] == receipt.record_sha256(value)
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    assert str(tmp_path) not in serialized
    for forbidden in ("path", "command", "env", "stdout", "stderr", "prompt", "transcript"):
        assert forbidden not in serialized.casefold()


@pytest.mark.parametrize(
    "supervisor",
    (
        {"observed": False, "exit_code": 0},
        {"observed": True, "exit_code": 1},
        {"observed": True, "exit_code": 0, "extra": True},
    ),
)
def test_supervisor_exit_and_unknown_fields_fail_closed(
    tmp_path: Path, supervisor: dict[str, object]
) -> None:
    identity, repository, binary, broker = _artifacts(tmp_path, "opencode")
    with pytest.raises(receipt.HostProcessReceiptError):
        receipt.build_receipt(
            host="opencode",
            task_case="continuity",
            run_id="run-opencode-001",
            identity=identity,
            repository=repository,
            host_binary=binary,
            broker_path=broker,
            supervisor=supervisor,
            isolation={
                "runner_received_secret": False,
                "mcp_received_secret": False,
                "ambient_auth_forwarded_to_mcp": False,
                "raw_output_retained": False,
            },
            expected_broker_sha256=hashlib.sha256(broker.read_bytes()).hexdigest(),
        )


def test_isolation_and_topology_fail_closed(tmp_path: Path) -> None:
    identity, repository, binary, broker = _artifacts(tmp_path, "codex")
    isolation = {
        "runner_received_secret": False,
        "mcp_received_secret": False,
        "ambient_auth_forwarded_to_mcp": False,
        "raw_output_retained": False,
    }
    isolation["runner_received_secret"] = True
    with pytest.raises(receipt.HostProcessReceiptError):
        receipt.build_receipt(
            host="codex",
            task_case="continuity",
            run_id="run-codex-001",
            identity=identity,
            repository=repository,
            host_binary=binary,
            broker_path=broker,
            supervisor={"observed": True, "exit_code": 0},
            isolation=isolation,
            expected_broker_sha256=hashlib.sha256(broker.read_bytes()).hexdigest(),
        )

    with pytest.raises(receipt.HostProcessReceiptError):
        receipt.build_receipt(
            host="codex",
            task_case="continuity",
            run_id="run-codex-001",
            identity=identity,
            repository=repository,
            host_binary=binary,
            broker_path=broker,
            supervisor={"observed": True, "exit_code": 0},
            isolation={key: False for key in isolation},
            selector_source_symlink=True,
            expected_broker_sha256=hashlib.sha256(broker.read_bytes()).hexdigest(),
        )


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "non_owner_only", "repo_inside"))
def test_broker_identity_boundaries_fail_closed(tmp_path: Path, kind: str) -> None:
    expected_acl_path = tmp_path / "reported-broker"
    assert preflight._windows_acl_report_owner_only(
        _verified_acl_report(expected_acl_path),
        expected_path=expected_acl_path,
    )
    for invalid_report in (
        None,
        {},
        {"platform": "nt", "permissions_verified": True},
        {"platform": "posix", "permissions_verified": True},
        {"platform": "nt", "permissions_verified": False},
    ):
        assert not preflight._windows_acl_report_owner_only(
            invalid_report,
            expected_path=expected_acl_path,
        )
    identity, repository, binary, broker = _artifacts(tmp_path, "opencode")
    selected = broker
    if kind == "symlink":
        selected = tmp_path / "broker-link"
        selected.symlink_to(broker)
    elif kind == "hardlink":
        selected = tmp_path / "broker-hardlink"
        os.link(broker, selected)
    elif kind == "non_owner_only":
        if os.name == "nt":
            system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
            assert system_root
            icacls = Path(system_root) / "System32" / "icacls.exe"
            assert icacls.is_file()
            subprocess.run(
                [str(icacls), str(broker), "/grant", "*S-1-1-0:R"],
                capture_output=True,
                check=True,
                timeout=30,
                env=preflight._host_version_probe_environment(),
            )
            from deeplaw.windows_acl import native_windows_path_acl_report

            assert native_windows_path_acl_report(broker)["permissions_verified"] is False
        else:
            broker.chmod(0o750)
    elif kind == "repo_inside":
        selected = repository / "broker"
        selected.write_bytes(broker.read_bytes())
        selected.chmod(0o700)
    with pytest.raises(receipt.HostProcessReceiptError):
        receipt.build_receipt(
            host="opencode",
            task_case="continuity",
            run_id="run-opencode-001",
            identity=identity,
            repository=repository,
            host_binary=binary,
            broker_path=selected,
            supervisor={"observed": True, "exit_code": 0},
            isolation={
                "runner_received_secret": False,
                "mcp_received_secret": False,
                "ambient_auth_forwarded_to_mcp": False,
                "raw_output_retained": False,
            },
            expected_broker_sha256=hashlib.sha256(broker.read_bytes()).hexdigest(),
        )


def test_windows_acl_recursive_entry_truncation_matches_native_bound(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    report = _verified_acl_report(root, kind="directory")
    first = report["entries"][0]
    report["entries"] = [
        first,
        *[
            {
                **copy.deepcopy(first),
                "path": str(root / f"entry-{index}"),
                "kind": "file",
            }
            for index in range(1, 1_000)
        ],
    ]
    report["entry_count"] = 1_001
    report["files_and_directories_checked"] = 1_001
    report["entries_truncated"] = True
    assert preflight.validate_windows_acl_report(
        report,
        expected_path=root,
        expected_kind="directory",
        recursive=True,
    ) == report
    assert not preflight._windows_acl_report_owner_only(
        report,
        expected_path=root,
    )
    report["entries_truncated"] = False
    with pytest.raises(preflight.ReceiptValidationError):
        preflight.validate_windows_acl_report(
            report,
            expected_path=root,
            expected_kind="directory",
            recursive=True,
        )


def test_wrong_expected_broker_digest_fails_closed(tmp_path: Path) -> None:
    identity, repository, binary, broker = _artifacts(tmp_path, "opencode")
    with pytest.raises(receipt.HostProcessReceiptError):
        receipt.build_receipt(
            host="opencode",
            task_case="continuity",
            run_id="run-opencode-001",
            identity=identity,
            repository=repository,
            host_binary=binary,
            broker_path=broker,
            supervisor={"observed": True, "exit_code": 0},
            isolation={
                "runner_received_secret": False,
                "mcp_received_secret": False,
                "ambient_auth_forwarded_to_mcp": False,
                "raw_output_retained": False,
            },
            expected_broker_sha256="f" * 64,
        )


def test_validator_rejects_identity_drift_record_mismatch_and_unknown_fields(
    tmp_path: Path,
) -> None:
    value, identity = _build(tmp_path)
    drifted_identity = copy.deepcopy(identity)
    drifted_identity["hosts"]["opencode"]["executable_sha256"] = "f" * 64
    with pytest.raises(receipt.HostProcessReceiptError):
        receipt.validate_receipt(value, identity=drifted_identity)

    changed_run = {**value, "run_id": "run-opencode-002"}
    with pytest.raises(receipt.HostProcessReceiptError):
        receipt.validate_receipt(changed_run)

    unknown = {**value, "unexpected": True}
    with pytest.raises(receipt.HostProcessReceiptError):
        receipt.validate_receipt(unknown)


def test_process_receipt_producer_has_no_dotenv_or_raw_file_surface() -> None:
    source = Path(receipt.__file__).read_text(encoding="utf-8").casefold()
    assert ".env" not in source
    assert "dotenv" not in source
    assert "read_bytes" not in source

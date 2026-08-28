from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.hosts import host_preflight_receipt as receipt
from benchmarks.hosts import run_pass13_codex_continuity_qualification as codex
from benchmarks.hosts import run_pass13_opencode_continuity_qualification as opencode


def _host() -> dict[str, object]:
    return {
        "name": "opencode",
        "version": "1.18.16",
        "sha256": "a" * 64,
    }


def _broker() -> dict[str, object]:
    return {
        "source_kind": "repository_external_launcher",
        "repository_external": True,
        "sha256": "b" * 64,
        "bytes": 12,
        "owner_only_mode": True,
        "expected_sha256": None,
    }


def _host_identity(*, codex_version: str = "codex-cli moving") -> dict[str, object]:
    return {
        "schema_version": receipt.HOST_IDENTITY_SCHEMA_VERSION,
        "hosts": {
            "codex": {
                "binary_version": codex_version,
                "binary_sha256": "a" * 64,
                "request_model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "auth_status_command": "codex login status",
                "auth_material_access": "forbidden",
            },
            "opencode": {
                "version": "1.18.16",
                "source_commit": "b" * 40,
                "config_selector": "deepseek/deepseek-v4-flash",
                "expected_response_model_id": "deepseek-v4-flash",
                "executable_sha256": "c" * 64,
                "package_sha256": "d" * 64,
                "runtime": "host_bun_runtime_only",
                "dotenv_policy": "owner_only_external_strict_parser",
                "secret_visibility": "forbidden",
            },
        },
    }


def _write_identity(path: Path, value: dict[str, object]) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


def _versioned_test_executable(
    tmp_path: Path,
    name: str,
    posix_version: str,
) -> tuple[Path, str]:
    if os.name == "nt":
        executable = Path(sys.executable).resolve(strict=True)
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            check=True,
            timeout=30,
            env=receipt._host_version_probe_environment(),
        )
        return executable, (completed.stdout + completed.stderr).decode().strip()
    executable = tmp_path / name
    executable.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{posix_version}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable, posix_version


def test_v1_receipt_is_strict_and_has_no_free_text_or_sensitive_surface(
    tmp_path: Path,
) -> None:
    value = receipt.build_receipt(
        host=_host(),
        broker_source=_broker(),
        status="failed",
        stage="broker",
        reason_code="broker_missing",
        check_count=1,
    )
    assert receipt.validate_receipt(value) == value
    path = receipt.write_receipt(tmp_path, value)
    assert path.name == receipt.RECEIPT_FILENAME
    assert json.loads(path.read_text(encoding="utf-8")) == value

    for key, bad_value in (
        ("path", "/private/credential/auth.json"),
        ("argv", ["opencode", "--secret"]),
        ("stdout", "raw host output"),
        ("prompt", "do not retain this"),
        ("reason", "raw exception text"),
    ):
        invalid = {**value, key: bad_value}
        with pytest.raises(receipt.ReceiptValidationError):
            receipt.validate_receipt(invalid)


@pytest.mark.parametrize(
    "message",
    [
        "arbitrary provider text",
        "untrusted server text",
        "unknown provider token text",
        "unknown model diagnostic",
        "untrusted mcp text",
        "arbitrary binary diagnostic",
    ],
)
def test_unknown_failures_close_to_internal_error(message: str) -> None:
    assert receipt.reason_code_for_exception(RuntimeError(message)) == "preflight_internal_error"
    assert receipt.stage_for_reason("preflight_internal_error") == "preflight"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Codex existing login was not confirmed", "auth_unavailable"),
        ("selected model was absent from model/list", "model_unavailable"),
        ("MCP status exposed an unexpected tool", "mcp_not_advertised"),
        ("Codex App Server failed to start", "transport_start_failed"),
        ("actual OpenCode provider token usage is missing", "usage_receipt_missing"),
    ],
)
def test_known_failure_categories_are_closed(message: str, expected: str) -> None:
    assert receipt.reason_code_for_exception(RuntimeError(message)) == expected


def test_broker_hash_is_exact_bytes_and_survives_source_deletion(tmp_path: Path) -> None:
    broker = tmp_path / "owner-broker"
    broker.write_bytes(b"owner-only broker bytes")
    broker.chmod(0o700)
    repository = tmp_path / "repository"
    repository.mkdir()
    observed = receipt.inspect_broker_source(broker, repository=repository)
    assert observed["sha256"] == receipt.sha256_file(broker)
    assert observed["bytes"] == len(b"owner-only broker bytes")
    assert observed["repository_external"] is True
    value = receipt.build_receipt(
        host=_host(),
        broker_source=observed,
        status="failed",
        stage="broker",
        reason_code="broker_hash_mismatch",
        check_count=1,
    )
    broker.unlink()
    assert receipt.validate_receipt(value)["broker_source"]["sha256"] == observed["sha256"]


def test_broker_change_during_exact_byte_read_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broker = tmp_path / "owner-broker"
    broker.write_bytes(b"owner-only broker bytes")
    broker.chmod(0o700)
    repository = tmp_path / "repository"
    repository.mkdir()

    original = receipt._sha256_file_with_bytes

    def mutate_after_read(path: Path) -> tuple[str, int]:
        observed_hash, observed_bytes = original(path)
        path.write_bytes(path.read_bytes() + b" changed")
        return observed_hash, observed_bytes

    monkeypatch.setattr(receipt, "_sha256_file_with_bytes", mutate_after_read)
    observed = receipt.inspect_broker_source(broker, repository=repository)
    assert observed["failure_reason_code"] == "broker_not_regular"
    assert observed["sha256"] is None
    assert observed["bytes"] == 0


@pytest.mark.parametrize(
    ("path_kind", "expected"),
    [("missing", "broker_missing"), ("directory", "broker_not_regular")],
)
def test_broker_missing_and_not_regular_are_typed(
    tmp_path: Path, path_kind: str, expected: str
) -> None:
    path = tmp_path / "broker"
    if path_kind == "directory":
        path.mkdir()
    observed = receipt.inspect_broker_source(path, repository=tmp_path / "repo")
    assert observed["failure_reason_code"] == expected


def test_expected_broker_hash_mismatch_is_typed(tmp_path: Path) -> None:
    broker = tmp_path / "broker"
    broker.write_bytes(b"broker")
    broker.chmod(0o700)
    observed = receipt.inspect_broker_source(
        broker,
        repository=tmp_path / "repo",
        expected_sha256="f" * 64,
    )
    assert observed["failure_reason_code"] == "broker_hash_mismatch"


def test_opencode_broker_validation_has_repository_external_containment(
    tmp_path: Path,
) -> None:
    host = tmp_path / "opencode"
    host.write_bytes(b"host")
    host.chmod(0o700)
    repository = tmp_path / "repository"
    repository.mkdir()
    inside = repository / "owner-broker"
    inside.write_bytes(b"broker")
    inside.chmod(0o700)
    with pytest.raises(opencode.QualificationError, match="outside the repository"):
        opencode._validate_owner_broker_launcher(
            inside,
            host_binary=host,
            repository=repository,
        )


def test_opencode_broker_missing_not_regular_and_hash_mismatch_are_closed(
    tmp_path: Path,
) -> None:
    host = tmp_path / "opencode"
    host.write_bytes(b"host")
    host.chmod(0o700)
    missing = tmp_path / "missing-broker"
    with pytest.raises(opencode.QualificationError) as missing_error:
        opencode._validate_owner_broker_launcher(missing, host_binary=host)
    assert receipt.reason_code_for_exception(missing_error.value) == "broker_missing"

    directory = tmp_path / "directory-broker"
    directory.mkdir()
    with pytest.raises(opencode.QualificationError) as regular_error:
        opencode._validate_owner_broker_launcher(directory, host_binary=host)
    assert receipt.reason_code_for_exception(regular_error.value) == "broker_not_regular"

    broker = tmp_path / "broker"
    broker.write_bytes(b"broker")
    broker.chmod(0o700)
    with pytest.raises(opencode.QualificationError) as hash_error:
        opencode._validate_owner_broker_launcher(
            broker,
            host_binary=host,
            expected_broker_sha256="f" * 64,
        )
    assert receipt.reason_code_for_exception(hash_error.value) == "broker_hash_mismatch"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Codex existing login was not confirmed", "auth_unavailable"),
        ("selected model was absent from model/list", "model_unavailable"),
        ("MCP inventory was empty", "mcp_not_advertised"),
        ("Codex MCP inventory failed to start", "transport_start_failed"),
    ],
)
def test_codex_preflight_categories_are_typed(message: str, expected: str) -> None:
    assert receipt.reason_code_for_exception(codex.QualificationFailure(message)) == expected


def test_opencode_availability_usage_missing_is_typed() -> None:
    with pytest.raises(opencode.QualificationError, match="usage receipt") as failure:
        opencode.parse_availability_result(
            stdout=b'{"type":"text","part":{"text":"available"}}\n',
            returncode=0,
            elapsed_ms=1,
        )
    assert receipt.reason_code_for_exception(failure.value) == "usage_receipt_missing"


def test_external_host_identity_is_owner_only_and_source_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    identity_path = tmp_path / "host-identity.json"
    raw = _write_identity(identity_path, _host_identity())
    loaded = receipt.load_host_identity_input(identity_path, repository=repository)
    assert loaded["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert loaded["source_bytes"] == len(raw)
    assert loaded["hosts"]["codex"]["binary_version"] == "codex-cli moving"

    # Windows lstat/fstat can expose different mode/uid/timestamp
    # representations for the same open file.  The cross-interface check
    # must retain only regular type, non-zero dev/ino, size, and bytes read;
    # path-before/path-after and fd-before/fd-after still require the full
    # mutation signature.  This is intentionally a production-seam canary.
    original_lstat = Path.lstat
    path_stat = original_lstat(identity_path)
    path_snapshot = type(
        "PathStat",
        (),
        {
            "st_dev": path_stat.st_dev,
            "st_ino": path_stat.st_ino,
            "st_size": path_stat.st_size,
            "st_mode": path_stat.st_mode,
            "st_uid": getattr(os, "geteuid", lambda: path_stat.st_uid)(),
            "st_nlink": path_stat.st_nlink,
            "st_mtime_ns": 100,
            "st_ctime_ns": 200,
        },
    )()
    fd_snapshot = type(
        "FdStat",
        (),
        {
            "st_dev": path_stat.st_dev,
            "st_ino": path_stat.st_ino,
            "st_size": path_stat.st_size,
            "st_mode": stat.S_IFREG | 0o600,
            "st_uid": path_snapshot.st_uid + 1000,
            "st_nlink": path_stat.st_nlink,
            "st_mtime_ns": 300,
            "st_ctime_ns": 400,
        },
    )()

    def windows_lstat(path: Path) -> os.stat_result:
        if Path(path) == identity_path:
            return path_snapshot  # type: ignore[return-value]
        return original_lstat(path)

    def windows_fstat(descriptor: int) -> os.stat_result:
        return fd_snapshot  # type: ignore[return-value]

    monkeypatch.setattr(Path, "lstat", windows_lstat)
    monkeypatch.setattr(os, "fstat", windows_fstat)
    assert receipt.load_host_identity_input(identity_path, repository=repository) == loaded

    inside = repository / receipt.HOST_IDENTITY_FILENAME
    _write_identity(inside, _host_identity())
    with pytest.raises(receipt.HostIdentityValidationError):
        receipt.load_host_identity_input(inside, repository=repository)

    symlink = tmp_path / "identity-link.json"
    try:
        symlink.symlink_to(identity_path)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(receipt.HostIdentityValidationError):
        receipt.load_host_identity_input(symlink, repository=repository)

    hardlink = tmp_path / "identity-hardlink.json"
    try:
        os.link(identity_path, hardlink)
    except OSError:
        pytest.skip("hardlinks are unavailable")
    with pytest.raises(receipt.HostIdentityValidationError):
        receipt.load_host_identity_input(hardlink, repository=repository)


def test_host_identity_binary_probe_binds_exact_regular_target(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    executable, version = _versioned_test_executable(
        tmp_path, "codex", "codex-cli moving"
    )
    identity_value = _host_identity(codex_version=version)
    identity_value["hosts"]["codex"]["binary_sha256"] = hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()
    identity_path = tmp_path / "host-identity.json"
    _write_identity(identity_path, identity_value)
    loaded = receipt.load_host_identity_input(identity_path, repository=repository)
    observed = receipt.inspect_host_binary(
        executable,
        host="codex",
        identity=loaded,
        repository=repository,
    )
    assert observed["version"] == version
    assert observed["source_symlink"] is False
    assert observed["host_identity_source_sha256"] == loaded["source_sha256"]


def test_opencode_selector_allows_one_symlink_but_rejects_a_symlink_chain(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    executable, version = _versioned_test_executable(
        tmp_path, "opencode-target", "1.18.16"
    )
    identity_value = _host_identity()
    identity_value["hosts"]["opencode"]["version"] = version
    identity_value["hosts"]["opencode"]["executable_sha256"] = hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()
    identity_path = tmp_path / "host-identity.json"
    _write_identity(identity_path, identity_value)
    loaded = receipt.load_host_identity_input(identity_path, repository=repository)

    selector = tmp_path / "opencode"
    try:
        selector.symlink_to(executable)
    except OSError:
        pytest.skip("symlinks are unavailable")
    observed = receipt.inspect_host_binary(
        selector,
        host="opencode",
        identity=loaded,
        repository=repository,
    )
    assert observed["selector_source_symlink"] is True

    chained_selector = tmp_path / "opencode-chain"
    chained_selector.symlink_to(selector)
    with pytest.raises(receipt.HostIdentityValidationError, match="symlink chain"):
        receipt.inspect_host_binary(
            chained_selector,
            host="opencode",
            identity=loaded,
            repository=repository,
        )


def test_host_binary_mutation_after_hash_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    if os.name == "nt":
        executable = tmp_path / "codex.exe"
        shutil.copyfile(sys.executable, executable)
        version = subprocess.run(
            [sys.executable, "--version"],
            capture_output=True,
            check=True,
            timeout=30,
            env=receipt._host_version_probe_environment(),
        )
        codex_version = (version.stdout + version.stderr).decode().strip()
    else:
        executable, codex_version = _versioned_test_executable(
            tmp_path, "codex", "codex-cli moving"
        )
    executable.chmod(0o700)
    identity_value = _host_identity(codex_version=codex_version)
    identity_value["hosts"]["codex"]["binary_sha256"] = hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()
    identity_path = tmp_path / "host-identity.json"
    _write_identity(identity_path, identity_value)
    loaded = receipt.load_host_identity_input(identity_path, repository=repository)

    original = receipt.sha256_file

    def mutate_after_hash(path: Path) -> str:
        digest = original(path)
        path.write_bytes(path.read_bytes() + b" changed")
        return digest

    monkeypatch.setattr(receipt, "sha256_file", mutate_after_hash)
    with pytest.raises(receipt.HostIdentityValidationError, match="hash probe"):
        receipt.inspect_host_binary(
            executable,
            host="codex",
            identity=loaded,
            repository=repository,
        )

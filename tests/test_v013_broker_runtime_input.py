from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.hosts import run_v013_host_task_qualification as host_task_runner
from benchmarks.hosts.broker_runtime_input import (
    BROKER_RUNTIME_BOOTSTRAP,
    BrokerRuntimeInputError,
    parse_runtime_manifest,
)
from benchmarks.hosts.run_v013_host_task_qualification import (
    HostTaskQualificationError,
)
from tests.helpers import build_private_broker_interpreter


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_runtime_is_posix_only(tmp_path: Path) -> None:
    with (
        pytest.raises(HostTaskQualificationError, match="POSIX"),
        host_task_runner._stage_exact_broker_executable(
            tmp_path / "unused-broker",
            repository=tmp_path,
            host_binary=tmp_path / "unused-host",
            expected_sha256="1" * 64,
            broker_interpreter=tmp_path / "unused-python",
            expected_broker_interpreter_sha256="2" * 64,
            expected_broker_interpreter_version="Python 3.0.0",
            broker_runtime_root=tmp_path / "runtime",
            broker_runtime_manifest=tmp_path / "manifest.json",
            expected_broker_runtime_manifest_sha256="3" * 64,
        ),
    ):
        pass


def _write_manifest(root: Path, manifest_path: Path, *, reverse: bool = False) -> str:
    files: list[dict[str, object]] = []
    for selected in sorted(path for path in root.rglob("*") if path.is_file()):
        files.append(
            {
                "relative_path": selected.relative_to(root).as_posix(),
                "byte_size": selected.stat().st_size,
                "sha256": _sha256(selected),
            }
        )
    if reverse:
        files.reverse()
    value = {
        "schema_version": "deeplaw.broker-runtime-input/v1",
        "runtime_root_identity": "private_external_nonstdlib_runtime",
        "files": files,
        "artifacts": {"wheel_sha256": "a" * 64, "lock_sha256": "b" * 64},
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if manifest_path.exists():
        manifest_path.chmod(0o600)
    manifest_path.write_bytes(raw)
    manifest_path.chmod(0o400)
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path: Path) -> dict[str, Path | str]:
    tmp_path = tmp_path.resolve(strict=True)
    repository = tmp_path / "repository"
    repository.mkdir()
    runtime = tmp_path / "runtime"
    site_packages = runtime / "site-packages" / "runtime_fixture"
    source_root = runtime / "source"
    site_packages.mkdir(parents=True)
    source_root.mkdir(parents=True)
    (source_root / "placeholder.py").write_bytes(b"")
    (site_packages / "__init__.py").write_text(
        "VALUE = 'explicit-runtime'\n",
        encoding="utf-8",
        newline="\n",
    )
    (site_packages / "value.py").write_text(
        "VALUE = 'explicit-runtime'\n",
        encoding="utf-8",
        newline="\n",
    )
    (site_packages / "runtime_fixture.py").write_text(
        "VALUE = 'explicit-runtime'\n",
        encoding="utf-8",
        newline="\n",
    )
    for selected in runtime.rglob("*"):
        if selected.is_dir():
            selected.chmod(0o500)
        else:
            selected.chmod(0o400)
    runtime.chmod(0o500)
    broker = tmp_path / "broker-source.py"
    broker.write_text(
        "from runtime_fixture import VALUE\n"
        "import sys\n"
        "print(VALUE + '|' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
        newline="\n",
    )
    broker.chmod(0o700)
    host_binary = tmp_path / "host-binary"
    host_binary.write_bytes(b"different-host-binary")
    host_binary.chmod(0o700)
    interpreter, version = build_private_broker_interpreter(tmp_path)
    manifest = tmp_path / "runtime-manifest.json"
    manifest_sha256 = _write_manifest(runtime, manifest)
    return {
        "broker": broker,
        "host_binary": host_binary,
        "interpreter": interpreter,
        "interpreter_version": version,
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "repository": repository,
        "runtime": runtime,
    }


def _stage(fixture: dict[str, Path | str], **overrides: object) -> object:
    options: dict[str, object] = {
        "repository": fixture["repository"],
        "host_binary": fixture["host_binary"],
        "expected_sha256": _sha256(fixture["broker"]),
        "broker_interpreter": fixture["interpreter"],
        "expected_broker_interpreter_sha256": _sha256(fixture["interpreter"]),
        "expected_broker_interpreter_version": fixture["interpreter_version"],
        "broker_runtime_root": fixture["runtime"],
        "broker_runtime_manifest": fixture["manifest"],
        "expected_broker_runtime_manifest_sha256": fixture["manifest_sha256"],
    }
    options.update(overrides)
    return host_task_runner._stage_exact_broker_executable(
        fixture["broker"],
        **options,
    )


def _closed_env() -> dict[str, str]:
    return {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def test_existing_staged_source_lacks_nonstdlib_imports(tmp_path: Path) -> None:
    if os.name == "nt":
        _assert_runtime_is_posix_only(tmp_path)
        return
    fixture = _fixture(tmp_path)
    broker = fixture["broker"]
    assert isinstance(broker, Path)
    broker.write_text(
        "from benchmarks.hosts.codex_app_server_client import CodexAppServerClient\n"
        "print(CodexAppServerClient.__name__)\n",
        encoding="utf-8",
        newline="\n",
    )
    with host_task_runner._stage_exact_broker_executable(
        broker,
        repository=fixture["repository"],
        host_binary=fixture["host_binary"],
        expected_sha256=_sha256(broker),
        broker_interpreter=fixture["interpreter"],
        expected_broker_interpreter_sha256=_sha256(fixture["interpreter"]),
        expected_broker_interpreter_version=fixture["interpreter_version"],
    ) as launcher:
        completed = subprocess.run(
            list(launcher),
            capture_output=True,
            check=False,
            timeout=5,
            env=_closed_env(),
        )
    assert completed.returncode != 0
    assert b"ModuleNotFoundError" in completed.stderr


def test_explicit_runtime_bootstrap_imports_without_cwd_or_pythonpath(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_runtime_is_posix_only(tmp_path)
        return
    fixture = _fixture(tmp_path)
    foreign_cwd = tmp_path / "foreign-cwd"
    foreign_cwd.mkdir()
    with _stage(fixture) as launcher:
        assert launcher[:3] == (str(fixture["interpreter"]), "-I", "-S")
        assert launcher[3] == "-c"
        assert launcher[4] == BROKER_RUNTIME_BOOTSTRAP
        completed = subprocess.run(
            [*launcher, "synthetic-control"],
            cwd=foreign_cwd,
            capture_output=True,
            check=False,
            timeout=5,
            env=_closed_env(),
        )
    assert completed.returncode == 0
    assert completed.stdout == b"explicit-runtime|synthetic-control\n"


def test_runtime_manifest_allows_reordered_entries_and_zero_byte_files(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_runtime_is_posix_only(tmp_path)
        return
    fixture = _fixture(tmp_path)
    fixture["manifest_sha256"] = _write_manifest(
        fixture["runtime"], fixture["manifest"], reverse=True
    )
    with _stage(fixture) as launcher:
        completed = subprocess.run(
            [*launcher, "reordered"],
            capture_output=True,
            check=False,
            timeout=5,
            env=_closed_env(),
        )
    assert completed.returncode == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "empty_dir",
        "wrong_hash",
        "symlink",
        "hardlink",
        "writable",
        "root_writable",
        "manifest_writable",
    ],
)
def test_runtime_tree_rejects_manifest_or_file_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    if os.name == "nt":
        _assert_runtime_is_posix_only(tmp_path)
        return
    fixture = _fixture(tmp_path)
    target = fixture["runtime"] / "site-packages" / "runtime_fixture" / "runtime_fixture.py"
    assert isinstance(target, Path)
    target.parent.chmod(0o700)
    if mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        target.parent.joinpath("extra.py").write_text("EXTRA = True\n", encoding="utf-8")
        target.parent.joinpath("extra.py").chmod(0o400)
    elif mutation == "empty_dir":
        empty = target.parent / "unmanifested"
        empty.mkdir()
        empty.chmod(0o500)
    elif mutation == "wrong_hash":
        target.chmod(0o600)
        target.write_text("VALUE = 'changed'\n", encoding="utf-8", newline="\n")
        target.chmod(0o400)
    elif mutation == "symlink":
        replacement = tmp_path / "symlink-target.py"
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o400)
        target.unlink()
        target.symlink_to(replacement)
    elif mutation == "hardlink":
        replacement = tmp_path / "hardlink-target.py"
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o400)
        target.unlink()
        os.link(replacement, target)
    elif mutation == "writable":
        target.chmod(0o664)
    elif mutation == "root_writable":
        fixture["runtime"].chmod(0o700)
    elif mutation == "manifest_writable":
        fixture["manifest"].chmod(0o600)
    else:
        raise AssertionError(mutation)
    target.parent.chmod(0o500)
    with pytest.raises(HostTaskQualificationError), _stage(fixture):
        pass


def test_runtime_control_group_requires_interpreter_and_all_fields(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_runtime_is_posix_only(tmp_path)
        return
    fixture = _fixture(tmp_path)
    with (
        pytest.raises(HostTaskQualificationError, match="together"),
        host_task_runner._stage_exact_broker_executable(
            fixture["broker"],
            repository=fixture["repository"],
            host_binary=fixture["host_binary"],
            expected_sha256=_sha256(fixture["broker"]),
            broker_interpreter=fixture["interpreter"],
            expected_broker_interpreter_sha256=_sha256(fixture["interpreter"]),
            expected_broker_interpreter_version=fixture["interpreter_version"],
            broker_runtime_root=fixture["runtime"],
            expected_broker_runtime_manifest_sha256=fixture["manifest_sha256"],
        ),
    ):
        pass
    with (
        pytest.raises(HostTaskQualificationError, match="pinned broker interpreter"),
        host_task_runner._stage_exact_broker_executable(
            fixture["broker"],
            repository=fixture["repository"],
            host_binary=fixture["host_binary"],
            expected_sha256=_sha256(fixture["broker"]),
            broker_runtime_root=fixture["runtime"],
            broker_runtime_manifest=fixture["manifest"],
            expected_broker_runtime_manifest_sha256=fixture["manifest_sha256"],
        ),
    ):
        pass
    with (
        pytest.raises(HostTaskQualificationError, match="artifact digest"),
        _stage(
            fixture,
            expected_broker_runtime_wheel_sha256="c" * 64,
            expected_broker_runtime_lock_sha256="d" * 64,
        ),
    ):
        pass


def test_runtime_manifest_rejects_duplicate_and_unsafe_paths() -> None:
    base = {
        "schema_version": "deeplaw.broker-runtime-input/v1",
        "runtime_root_identity": "private_external_nonstdlib_runtime",
        "files": [
            {"relative_path": "source/a.py", "byte_size": 0, "sha256": "1" * 64}
        ],
        "artifacts": {"wheel_sha256": "a" * 64, "lock_sha256": "b" * 64},
    }
    for path in ("pkg/a.py", "../escape.py", "/absolute.py", "C:/drive.py"):
        value = json.loads(json.dumps(base))
        value["files"][0]["relative_path"] = path
        with pytest.raises(BrokerRuntimeInputError):
            parse_runtime_manifest(json.dumps(value).encode())
    duplicate_value = json.loads(json.dumps(base))
    duplicate_value["files"].append(dict(duplicate_value["files"][0]))
    duplicate = json.dumps(duplicate_value)
    with pytest.raises(BrokerRuntimeInputError):
        parse_runtime_manifest(duplicate.encode())
    with pytest.raises(BrokerRuntimeInputError, match="duplicate keys"):
        parse_runtime_manifest(b'{"schema_version":"x","schema_version":"y"}')
    with pytest.raises(BrokerRuntimeInputError):
        parse_runtime_manifest(b"[" * 1500 + b"0" + b"]" * 1500)


def test_runtime_replacement_after_staging_fails_closed_even_on_consumer_error(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_runtime_is_posix_only(tmp_path)
        return
    fixture = _fixture(tmp_path)
    target = fixture["runtime"] / "site-packages" / "runtime_fixture" / "runtime_fixture.py"
    replacement = tmp_path / "same-bytes-runtime-file.py"
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(0o400)
    with (
        pytest.raises(HostTaskQualificationError, match="runtime input changed"),
        _stage(fixture),
    ):
        target.parent.chmod(0o700)
        os.replace(replacement, target)
        target.parent.chmod(0o500)
        raise RuntimeError("consumer failure")


def test_runtime_manifest_matches_contract_schema(tmp_path: Path) -> None:
    if os.name == "nt":
        _assert_runtime_is_posix_only(tmp_path)
        return
    fixture = _fixture(tmp_path)
    raw = fixture["manifest"].read_bytes()
    value = json.loads(raw)
    schema = json.loads(
        Path("contracts/broker-runtime-input.v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(value)
    parsed = parse_runtime_manifest(raw)
    assert parsed.total_bytes == sum(item["byte_size"] for item in value["files"])

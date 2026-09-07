from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.hosts import run_v013_host_task_qualification as host_task_runner
from benchmarks.hosts.run_v013_host_task_qualification import (
    HostTaskQualificationError,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_script_interpreter(path: Path, version: str = "Python 0.0.0") -> Path:
    path.write_text(
        f"#!/bin/sh\nprintf '{version}\\n'\n",
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(0o755)
    return path


def _assert_pinned_interpreter_is_posix_only(tmp_path: Path) -> None:
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
        ),
    ):
        pass


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    broker = tmp_path / "broker-source.py"
    broker.write_text(
        'import sys\nprint("FIXED_BROKER_IDENTITY:" + sys.version.split()[0])\n',
        encoding="utf-8",
        newline="\n",
    )
    broker.chmod(0o700)
    interpreter = Path(sys.executable).resolve(strict=True)
    version_probe = subprocess.run(
        [str(interpreter), "--version"],
        capture_output=True,
        check=False,
        timeout=5,
        env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    version = (version_probe.stdout + version_probe.stderr).decode().strip()
    assert version_probe.returncode == 0
    assert version.startswith("Python ")
    host_binary = tmp_path / "host-binary"
    host_binary.write_bytes(b"different-host-binary")
    host_binary.chmod(0o700)
    return broker, interpreter, version, repository, host_binary


def _stage(
    broker: Path,
    interpreter: Path,
    version: str,
    repository: Path,
    host_binary: Path,
    **overrides: object,
) -> object:
    options: dict[str, object] = {
        "repository": repository,
        "host_binary": host_binary,
        "expected_sha256": _sha256(broker),
        "broker_interpreter": interpreter,
        "expected_broker_interpreter_sha256": _sha256(interpreter),
        "expected_broker_interpreter_version": version,
    }
    options.update(overrides)
    return host_task_runner._stage_exact_broker_executable(broker, **options)


def test_source_only_stage_cannot_run_python_source_but_pinned_interpreter_can(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_pinned_interpreter_is_posix_only(tmp_path)
        return
    broker, interpreter, version, repository, host_binary = _fixture(tmp_path)

    with (
        host_task_runner._stage_exact_broker_executable(
            broker,
            repository=repository,
            host_binary=host_binary,
            expected_sha256=_sha256(broker),
        ) as staged,
        pytest.raises(OSError),
    ):
        subprocess.run([str(staged)], capture_output=True, check=True, timeout=5)

    with _stage(broker, interpreter, version, repository, host_binary) as launcher:
        assert isinstance(launcher, tuple)
        completed = subprocess.run(
            list(launcher),
            capture_output=True,
            check=False,
            timeout=5,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    assert completed.returncode == 0
    assert completed.stdout == (
        f'FIXED_BROKER_IDENTITY:{version.removeprefix("Python ")}\n'.encode()
    )


@pytest.mark.parametrize(
    "missing_name",
    [
        "broker_interpreter",
        "expected_broker_interpreter_sha256",
        "expected_broker_interpreter_version",
    ],
)
def test_interpreter_binding_requires_all_control_fields(
    tmp_path: Path,
    missing_name: str,
) -> None:
    if os.name == "nt":
        _assert_pinned_interpreter_is_posix_only(tmp_path)
        return
    broker, interpreter, version, repository, host_binary = _fixture(tmp_path)
    options = {
        "broker_interpreter": interpreter,
        "expected_broker_interpreter_sha256": _sha256(interpreter),
        "expected_broker_interpreter_version": version,
    }
    options[missing_name] = None
    with (
        pytest.raises(HostTaskQualificationError, match="interpreter"),
        _stage(broker, interpreter, version, repository, host_binary, **options),
    ):
        pass


def test_interpreter_binding_rejects_wrong_hash_version_links_and_writability(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_pinned_interpreter_is_posix_only(tmp_path)
        return
    broker, interpreter, version, repository, host_binary = _fixture(tmp_path)

    with (
        pytest.raises(HostTaskQualificationError, match="hash"),
        _stage(
            broker,
            interpreter,
            version,
            repository,
            host_binary,
            expected_broker_interpreter_sha256="f" * 64,
        ),
    ):
        pass
    with (
        pytest.raises(HostTaskQualificationError, match="version"),
        _stage(
            broker,
            interpreter,
            version,
            repository,
            host_binary,
            expected_broker_interpreter_version="Python 0.0.0",
        ),
    ):
        pass

    symlink_target = _write_script_interpreter(tmp_path / "symlink-target")
    symlink = tmp_path / "python-symlink"
    symlink.symlink_to(symlink_target)
    with (
        pytest.raises(HostTaskQualificationError, match="regular"),
        _stage(
            broker,
            symlink,
            version,
            repository,
            host_binary,
            expected_broker_interpreter_sha256=_sha256(symlink_target),
        ),
    ):
        pass

    hardlink_source = _write_script_interpreter(tmp_path / "hardlink-source")
    hardlink = tmp_path / "python-hardlink"
    os.link(hardlink_source, hardlink)
    with (
        pytest.raises(HostTaskQualificationError, match="single-link"),
        _stage(
            broker,
            hardlink,
            "Python 0.0.0",
            repository,
            host_binary,
            expected_broker_interpreter_sha256=_sha256(hardlink_source),
        ),
    ):
        pass

    writable_interpreter = _write_script_interpreter(tmp_path / "writable-interpreter")
    writable_interpreter.chmod(0o775)
    with (
        pytest.raises(HostTaskQualificationError, match="writable"),
        _stage(
            broker,
            writable_interpreter,
            "Python 0.0.0",
            repository,
            host_binary,
        ),
    ):
        pass


def test_interpreter_version_probe_uses_closed_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_pinned_interpreter_is_posix_only(tmp_path)
        return
    broker, interpreter, version, repository, host_binary = _fixture(tmp_path)
    observed: dict[str, dict[str, str]] = {}
    original_run = host_task_runner.bounded_subprocess.run_bounded_subprocess

    def capture(*args: object, **kwargs: object) -> object:
        environment = kwargs.get("environment")
        assert isinstance(environment, dict)
        observed["env"] = environment
        return original_run(*args, **kwargs)

    monkeypatch.setattr(
        host_task_runner.bounded_subprocess,
        "run_bounded_subprocess",
        capture,
    )
    with _stage(broker, interpreter, version, repository, host_binary):
        pass
    assert observed["env"] == {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
    }


def test_interpreter_replacement_after_version_probe_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_pinned_interpreter_is_posix_only(tmp_path)
        return
    broker, _, _, repository, host_binary = _fixture(tmp_path)
    interpreter = _write_script_interpreter(tmp_path / "probe-interpreter")
    replacement = _write_script_interpreter(tmp_path / "replacement-interpreter")
    original_run = host_task_runner.bounded_subprocess.run_bounded_subprocess

    def replace_after_probe(*args: object, **kwargs: object) -> object:
        completed = original_run(*args, **kwargs)
        os.replace(replacement, interpreter)
        return completed

    monkeypatch.setattr(
        host_task_runner.bounded_subprocess,
        "run_bounded_subprocess",
        replace_after_probe,
    )
    with (
        pytest.raises(HostTaskQualificationError, match="changed"),
        _stage(
            broker,
            interpreter,
            "Python 0.0.0",
            repository,
            host_binary,
        ),
    ):
        pass


def test_cli_forwards_complete_pinned_interpreter_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    def fake_preflight(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {"status": "failed", "formal_admission": False}

    monkeypatch.setattr(
        host_task_runner,
        "run_codex_owner_external_zero_model_preflight",
        fake_preflight,
    )
    assert (
        host_task_runner.main(
            [
                "--codex-zero-model-preflight",
                "--candidate-binding-input",
                str(tmp_path / "candidate.json"),
                "--host-identity-input",
                str(tmp_path / "identity.json"),
                "--codex-binary",
                str(tmp_path / "codex"),
                "--codex-broker",
                str(tmp_path / "broker"),
                "--expected-codex-broker-sha256",
                "1" * 64,
                "--codex-broker-interpreter",
                str(tmp_path / "python"),
                "--expected-codex-broker-interpreter-sha256",
                "2" * 64,
                "--expected-codex-broker-interpreter-version",
                "Python 3.11.15",
                "--task-case",
                "continuity",
                "--run-id",
                "run",
                "--evidence-run-id",
                "1",
                "--qualification-run-id",
                "1",
            ]
        )
        == 0
    )
    assert observed["broker_interpreter"] == tmp_path / "python"
    assert observed["expected_broker_interpreter_sha256"] == "2" * 64
    assert observed["expected_broker_interpreter_version"] == "Python 3.11.15"


def test_interpreter_replacement_after_staging_fails_closed(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_pinned_interpreter_is_posix_only(tmp_path)
        return
    broker, _, _, repository, host_binary = _fixture(tmp_path)
    interpreter = _write_script_interpreter(tmp_path / "staged-interpreter")
    replacement = tmp_path / "same-bytes-replacement"
    replacement.write_bytes(interpreter.read_bytes())
    replacement.chmod(0o755)
    with (
        pytest.raises(HostTaskQualificationError, match="changed"),
        _stage(
            broker,
            interpreter,
            "Python 0.0.0",
            repository,
            host_binary,
            expected_broker_interpreter_sha256=_sha256(interpreter),
        ),
    ):
        os.replace(replacement, interpreter)
        raise RuntimeError("consumer failure")


def test_preflight_passes_pinned_interpreter_sequence_to_broker_consumer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        _assert_pinned_interpreter_is_posix_only(tmp_path)
        return
    broker, interpreter, version, repository, host_binary = _fixture(tmp_path)
    captured: dict[str, object] = {}
    candidate = {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "lock_sha256": "c" * 64,
        "wheel_sha256": "d" * 64,
        "sdist_sha256": "e" * 64,
    }
    monkeypatch.setattr(
        host_task_runner,
        "load_zero_model_candidate_binding",
        lambda *_args, **_kwargs: candidate,
    )
    monkeypatch.setattr(
        host_task_runner,
        "_load_external_identity",
        lambda *_args, **_kwargs: {"source_sha256": "f" * 64, "hosts": {"codex": {}}},
    )
    monkeypatch.setattr(
        host_task_runner,
        "_validate_codex_binary_static",
        lambda *_args, **_kwargs: {"version": "fixture", "sha256": "1" * 64},
    )
    monkeypatch.setattr(host_task_runner, "host_identity_sha256", lambda _: "2" * 64)
    monkeypatch.setattr(
        host_task_runner,
        "build_codex_zero_model_preflight_request",
        lambda **_: {"schema_version": "fixture"},
    )

    def consume(
        launcher: object,
        *,
        request: object,
        seen_nonce_sha256s: set[str],
    ) -> dict[str, object]:
        captured["launcher"] = launcher
        return {
            "schema_version": "deeplaw.codex-owner-external-broker-control/v4",
            "host_process_receipt": {"record_sha256": "3" * 64},
            "observed_sequence": [],
            "fresh_ephemeral_thread": True,
            "turn_start_count": 1,
            "session_start_hook": {},
            "provider_guard": {},
            "accepted_connection_count": 0,
            "request_count": 0,
            "model_inventory_count": 0,
            "model_invocation_count": 0,
            "provider_request_count": 0,
            "sampling_count": 0,
        }

    monkeypatch.setattr(host_task_runner, "consume_codex_zero_model_preflight", consume)
    result = host_task_runner.run_codex_owner_external_zero_model_preflight(
        candidate_binding_input=tmp_path / "candidate.json",
        host_identity_input=tmp_path / "identity.json",
        codex_binary=host_binary,
        codex_broker=broker,
        expected_broker_sha256=_sha256(broker),
        task_case="continuity",
        run_id="run",
        evidence_run_id=1,
        qualification_run_id=1,
        broker_interpreter=interpreter,
        expected_broker_interpreter_sha256=_sha256(interpreter),
        expected_broker_interpreter_version=version,
        repository=repository,
    )

    launcher = captured["launcher"]
    assert isinstance(launcher, tuple)
    assert launcher[:3] == (str(interpreter), "-I", "-S")
    assert Path(launcher[3]).name == "broker-executable"
    assert result["broker_interpreter"] == {
        "identity": "python",
        "sha256": _sha256(interpreter),
        "version": version,
        "execution_mode": "pinned_interpreter",
        "import_closure_bound": False,
    }

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from deeplaw import bounded_subprocess
from deeplaw.bounded_subprocess import (
    BoundedSubprocessError,
    BoundedSubprocessFailureKind,
    run_bounded_subprocess,
)


def test_bounded_subprocess_captures_exact_bounded_output(tmp_path: Path) -> None:
    sidecar = tmp_path / "echo.py"
    sidecar.write_text(
        "import sys\npayload = sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(payload[::-1])\n",
        encoding="utf-8",
    )

    result = run_bounded_subprocess(
        [sys.executable, str(sidecar)],
        input_bytes=b"deeplaw",
        timeout_seconds=5,
        max_stdout_bytes=1_024,
        max_stderr_bytes=1_024,
    )

    assert result.returncode == 0
    assert result.stdout == b"walpeed"
    assert result.stderr == b""


@pytest.mark.parametrize(
    ("stream", "message", "kind"),
    (
        ("stdout", "stdout exceeded", BoundedSubprocessFailureKind.STDOUT_LIMIT),
        ("stderr", "stderr exceeded", BoundedSubprocessFailureKind.STDERR_LIMIT),
    ),
)
def test_bounded_subprocess_kills_oversized_output(
    tmp_path: Path,
    stream: str,
    message: str,
    kind: BoundedSubprocessFailureKind,
) -> None:
    sidecar = tmp_path / "oversized.py"
    sidecar.write_text(
        "import sys, time\n"
        f"sys.{stream}.write('x' * 1000000)\n"
        f"sys.{stream}.flush()\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )

    with pytest.raises(BoundedSubprocessError, match=message) as captured:
        run_bounded_subprocess(
            [sys.executable, str(sidecar)],
            timeout_seconds=5,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )
    error = captured.value
    assert error.kind is kind
    assert len(getattr(error, stream)) == 1_024
    assert getattr(error, f"{stream}_truncated") is True


def test_bounded_subprocess_kills_timeout(tmp_path: Path) -> None:
    sidecar = tmp_path / "slow.py"
    sidecar.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")

    with pytest.raises(BoundedSubprocessError, match="timed out") as captured:
        run_bounded_subprocess(
            [sys.executable, str(sidecar)],
            timeout_seconds=0.05,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )
    assert captured.value.kind is BoundedSubprocessFailureKind.TIMEOUT


def test_bounded_subprocess_reports_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected start failure")

    monkeypatch.setattr(bounded_subprocess.subprocess, "Popen", fail_start)

    with pytest.raises(BoundedSubprocessError, match="failed to start") as captured:
        run_bounded_subprocess(
            [sys.executable, "-c", ""],
            timeout_seconds=5,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )

    assert captured.value.kind is BoundedSubprocessFailureKind.START_FAILED


def test_bounded_subprocess_reports_unavailable_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CleanupGuard:
        def __init__(self, result: bool) -> None:
            self.result = result
            self.timeouts: list[float] = []

        def cleanup(self, *, timeout_seconds: float) -> bool:
            self.timeouts.append(timeout_seconds)
            return self.result

    class NoPipesProcess:
        stdin = None
        stdout = None
        stderr = None

        def poll(self) -> int:
            return 0

        def wait(self, *, timeout: float | None = None) -> int:
            del timeout
            return 0

    process = NoPipesProcess()
    confirmed_guard = CleanupGuard(True)
    monkeypatch.setattr(
        bounded_subprocess,
        "spawn_process",
        lambda *_args, **_kwargs: (process, confirmed_guard),
    )

    with pytest.raises(BoundedSubprocessError, match="pipes are unavailable") as captured:
        run_bounded_subprocess(
            [sys.executable, "-c", ""],
            timeout_seconds=5,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )

    assert captured.value.kind is BoundedSubprocessFailureKind.PIPES_UNAVAILABLE
    assert confirmed_guard.timeouts == ([5] if os.name == "nt" else [])

    class UnreapedPipesProcess:
        pid = 4320
        stdin = None
        stdout = None
        stderr = None

        def __init__(self) -> None:
            self.wait_timeouts: list[float] = []
            self.killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self, *, timeout: float) -> None:
            self.wait_timeouts.append(timeout)
            raise subprocess.TimeoutExpired("fake-pipes", timeout)

    unreaped = UnreapedPipesProcess()
    unconfirmed_guard = CleanupGuard(False)

    def fail_group_kill(_pid: int, _signal: int) -> None:
        raise OSError("synthetic process-group cleanup failure")

    monkeypatch.setattr(bounded_subprocess.os, "killpg", fail_group_kill)
    monkeypatch.setattr(
        bounded_subprocess,
        "spawn_process",
        lambda *_args, **_kwargs: (unreaped, unconfirmed_guard),
    )

    with pytest.raises(
        BoundedSubprocessError,
        match="cleanup could not be confirmed",
    ) as unconfirmed:
        run_bounded_subprocess(
            [sys.executable, "-c", ""],
            timeout_seconds=0.05,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )

    assert unconfirmed.value.kind is BoundedSubprocessFailureKind.CLEANUP_UNCONFIRMED
    assert unconfirmed.value.stdout == b""
    assert unconfirmed.value.stderr == b""
    assert unreaped.killed is True
    assert len(unreaped.wait_timeouts) == 2
    assert all(0 < timeout <= 0.05 for timeout in unreaped.wait_timeouts)
    assert unconfirmed_guard.timeouts == ([5] if os.name == "nt" else [])


def test_bounded_subprocess_kills_descendants_that_inherit_output_pipes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    accounting_fields = [
        name
        for name, _field_type in bounded_subprocess._JOBOBJECT_BASIC_ACCOUNTING_INFORMATION._fields_
    ]
    assert accounting_fields == [
        "TotalUserTime",
        "TotalKernelTime",
        "ThisPeriodTotalUserTime",
        "ThisPeriodTotalKernelTime",
        "TotalPageFaultCount",
        "TotalProcesses",
        "ActiveProcesses",
        "TotalTerminatedProcesses",
    ]

    events: list[str] = []
    popen_options: list[dict[str, object]] = []

    class SyntheticProcess:
        pid = 730

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            events.append("kill")

        def wait(self, *, timeout: float) -> int:
            del timeout
            return 0

    class SyntheticKernel32:
        def CreateJobObjectW(self, *_args: object) -> int:
            events.append("create-job")
            return 11

        def OpenProcess(self, *_args: object) -> int:
            events.append("open-process")
            return 12

        def AssignProcessToJobObject(self, *_args: object) -> int:
            events.append("assign")
            return 1

        def OpenThread(self, *_args: object) -> int:
            events.append("open-thread")
            return 13

        def ResumeThread(self, *_args: object) -> int:
            events.append("resume")
            return 1

        def CloseHandle(self, *_args: object) -> int:
            events.append("close")
            return 1

    synthetic_kernel32 = SyntheticKernel32()
    with monkeypatch.context() as synthetic_windows:
        synthetic_windows.setattr(bounded_subprocess.os, "name", "nt")
        synthetic_windows.setattr(
            bounded_subprocess,
            "_kernel32_for_jobs",
            lambda: synthetic_kernel32,
        )
        synthetic_windows.setattr(
            bounded_subprocess,
            "_configure_job",
            lambda _kernel32, _job: True,
        )
        synthetic_windows.setattr(
            bounded_subprocess,
            "_primary_thread_id",
            lambda _kernel32, _pid: 731,
        )
        synthetic_windows.setattr(
            bounded_subprocess,
            "_query_job_active_processes",
            lambda _kernel32, _job: 0,
        )

        def synthetic_popen(
            _command: list[str],
            **kwargs: object,
        ) -> SyntheticProcess:
            events.append("popen")
            popen_options.append(kwargs)
            return SyntheticProcess()

        synthetic_windows.setattr(
            bounded_subprocess.subprocess,
            "Popen",
            synthetic_popen,
        )
        synthetic_process, synthetic_guard = bounded_subprocess.spawn_process(
            ["synthetic.exe"],
            creationflags=512,
        )
        assert synthetic_process.pid == 730
        assert synthetic_guard is not None
        assert synthetic_guard.cleanup(timeout_seconds=1) is True

    assert popen_options[0]["creationflags"] == 512 | 0x00000004
    assert events.index("assign") < events.index("resume")

    native_taskkill: Path | None = None
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
        assert system_root
        native_taskkill = (Path(system_root) / "System32" / "taskkill.exe").resolve(
            strict=True
        )
        assert native_taskkill.is_file()

    def create_fixture(root: Path) -> tuple[Path, Path, Path, Path, Path]:
        root.mkdir(parents=True, exist_ok=True)
        started = root / "child-started"
        child_pid = root / "child-pid"
        parent_pid = root / "parent-pid"
        survived = root / "child-survived"
        child = (
            "import os,pathlib,time;"
            f"pathlib.Path({str(started)!r}).write_text('started', encoding='utf-8');"
            f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid()), encoding='utf-8');"
            "time.sleep(1);"
            f"pathlib.Path({str(survived)!r}).write_text('survived', encoding='utf-8');"
            "time.sleep(10)"
        )
        parent = root / "process-tree.py"
        parent.write_text(
            "import os,pathlib,subprocess,sys,time\n"
            f"pathlib.Path({str(parent_pid)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "time.sleep(10)\n",
            encoding="utf-8",
        )
        return parent, started, child_pid, parent_pid, survived

    def cleanup_windows_fixture(
        child_pid: Path,
        parent_pid: Path,
        *,
        require_live_handle: bool,
    ) -> None:
        if native_taskkill is None:
            return
        cleanup_pid_path = child_pid if child_pid.is_file() else parent_pid
        if not cleanup_pid_path.is_file():
            return
        fixture_pid = int(cleanup_pid_path.read_text(encoding="utf-8"))
        assert fixture_pid > 0
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x00100000, False, fixture_pid)
        if require_live_handle:
            assert handle
        if not handle:
            return
        try:
            cleanup = subprocess.run(
                [str(native_taskkill), "/F", "/T", "/PID", str(fixture_pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=5,
            )
            assert cleanup.returncode == 0
            assert kernel32.WaitForSingleObject(handle, 5_000) == 0
        finally:
            assert kernel32.CloseHandle(handle) != 0

    parent, started, child_pid, parent_pid, survived = create_fixture(tmp_path)
    try:
        with pytest.raises(BoundedSubprocessError, match="timed out") as captured:
            run_bounded_subprocess(
                [sys.executable, str(parent)],
                timeout_seconds=0.5,
                max_stdout_bytes=1_024,
                max_stderr_bytes=1_024,
            )
        assert captured.value.kind is BoundedSubprocessFailureKind.TIMEOUT
        assert started.is_file()
        assert child_pid.is_file()
        time.sleep(1)
        assert not survived.exists()
    finally:
        cleanup_windows_fixture(child_pid, parent_pid, require_live_handle=False)

    if os.name != "nt":
        return

    never_started = tmp_path / "failed-attach-never-started"
    with monkeypatch.context() as failed_attach:
        failed_attach.setattr(
            bounded_subprocess,
            "_primary_thread_id",
            lambda _kernel32, _process_id: None,
        )
        with pytest.raises(
            BoundedSubprocessError,
            match="failed to start",
        ) as start_failure:
            run_bounded_subprocess(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib;"
                        f"pathlib.Path({str(never_started)!r}).write_text('ran')"
                    ),
                ],
                timeout_seconds=2,
                max_stdout_bytes=1_024,
                max_stderr_bytes=1_024,
            )
    assert start_failure.value.kind is BoundedSubprocessFailureKind.START_FAILED
    assert not never_started.exists()

    forced_root = tmp_path / "forced-unconfirmed"
    (
        forced_parent,
        forced_started,
        forced_child_pid,
        forced_parent_pid,
        forced_survived,
    ) = create_fixture(forced_root)
    original_popen = bounded_subprocess.subprocess.Popen

    def spawn_without_guard(
        command: list[str],
        **kwargs: object,
    ) -> tuple[subprocess.Popen[bytes], None]:
        return original_popen(command, **kwargs), None

    monkeypatch.setattr(bounded_subprocess, "spawn_process", spawn_without_guard)
    try:
        with pytest.raises(
            BoundedSubprocessError,
            match="cleanup could not be confirmed",
        ) as captured:
            run_bounded_subprocess(
                [sys.executable, str(forced_parent)],
                timeout_seconds=0.5,
                max_stdout_bytes=1_024,
                max_stderr_bytes=1_024,
            )
        assert captured.value.kind is BoundedSubprocessFailureKind.CLEANUP_UNCONFIRMED
        assert forced_started.is_file()
        assert forced_child_pid.is_file()
        time.sleep(1)
        assert forced_survived.is_file()
    finally:
        cleanup_windows_fixture(
            forced_child_pid,
            forced_parent_pid,
            require_live_handle=True,
        )
